"""
NeuralHub — Servidor Central del Protocolo Neural
===================================================
Versión con persistencia SQLite, round-robin automático y dashboard opcional.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import ssl
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from neural_protocol.core.signal import NeuralSignal, NeuralSignalType
from neural_protocol.core.synapse import Synapse
from neural_protocol.transport.websocket import (
    WebSocketConnection,
    serve_websocket,
    ctrl_msg,
    is_ctrl,
    parse_ctrl,
)


@dataclass
class AgentEntry:
    agent_id: str
    neural_hash: str
    conn: WebSocketConnection
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    signals_received: int = 0
    signals_sent: int = 0

    def touch(self) -> None:
        self.last_seen = time.time()

    @property
    def uptime(self) -> float:
        return time.time() - self.connected_at

    def info(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "neural_hash": self.neural_hash,
            "uptime_s": round(self.uptime, 1),
            "signals_received": self.signals_received,
            "signals_sent": self.signals_sent,
            "last_seen": round(time.time() - self.last_seen, 1),
        }


@dataclass
class PendingSignal:
    encoded: bytes
    target_hash: str
    queued_at: float = field(default_factory=time.time)
    ttl: int = 10

    @property
    def expired(self) -> bool:
        age = time.time() - self.queued_at
        return self.ttl <= 0 or age > 300


class NeuralHub:
    """
    Servidor central WebSocket del NeuralProtocol con persistencia SQLite y round-robin.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8765, db_path: str = "neural_hub.db"):
        self.host = host
        self.port = port
        self.db_path = db_path

        # Registry en memoria (agentes conectados)
        self._agents: Dict[str, AgentEntry] = {}          # neural_hash → AgentEntry
        self._names: Dict[str, str] = {}                  # agent_id → neural_hash (para resolución rápida)
        self._agents_by_name: Dict[str, List[str]] = {}   # nombre → lista de hashes (para round-robin)
        self._rr_index: Dict[str, int] = {}               # nombre → próximo índice

        # Synaptic DB en memoria (cargada desde SQLite)
        self._synapses: Dict[str, Synapse] = {}           # "src:tgt" → Synapse

        # Cola de señales pendientes en memoria (también persistida)
        self._pending: Dict[str, List[PendingSignal]] = {}

        # Stats globales
        self._stats = {
            "total_signals": 0,
            "total_bytes": 0,
            "dropped_signals": 0,
            "queued_signals": 0,
            "started_at": time.time(),
        }

        self._server: Optional[asyncio.AbstractServer] = None
        self._dashboard_runner: Optional = None  # para aiohttp
        self._log: List[str] = []

        # Inicializar base de datos
        self._init_db()

    # ── Persistencia SQLite ─────────────────────────────────────────────

    def _init_db(self) -> None:
        """Crea las tablas necesarias si no existen."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        # Tabla de sinapsis
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS synapses (
                src_tgt TEXT PRIMARY KEY,
                src_hash TEXT,
                tgt_hash TEXT,
                strength REAL,
                transmission_count INTEGER,
                success_count INTEGER,
                created_at REAL,
                last_used REAL
            )
        ''')
        # Tabla de cola de mensajes pendientes
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pending (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_hash TEXT,
                encoded BLOB,
                queued_at REAL,
                ttl INTEGER
            )
        ''')
        conn.commit()
        conn.close()
        self._load_synapses()
        self._load_pending()

    def _load_synapses(self) -> None:
        """Carga las sinapsis desde la BD a memoria."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT src_tgt, src_hash, tgt_hash, strength, transmission_count, success_count, created_at, last_used FROM synapses")
        rows = cursor.fetchall()
        for row in rows:
            src_tgt, src, tgt, strength, tx_count, succ_count, created, last = row
            syn = Synapse(
                source_hash=src,
                target_hash=tgt,
                strength=strength,
                transmission_count=tx_count,
                success_count=succ_count,
                created_at=created,
                last_used=last,
            )
            self._synapses[src_tgt] = syn
        conn.close()

    def _save_synapse(self, key: str, syn: Synapse) -> None:
        """Guarda o actualiza una sinapsis en la BD."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO synapses
            (src_tgt, src_hash, tgt_hash, strength, transmission_count, success_count, created_at, last_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            key,
            syn.source_hash,
            syn.target_hash,
            syn.strength,
            syn.transmission_count,
            syn.success_count,
            syn.created_at,
            syn.last_used,
        ))
        conn.commit()
        conn.close()

    def _load_pending(self) -> None:
        """Carga los mensajes pendientes desde la BD a memoria."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT target_hash, encoded, queued_at, ttl FROM pending")
        rows = cursor.fetchall()
        for target_hash, encoded, queued_at, ttl in rows:
            if target_hash not in self._pending:
                self._pending[target_hash] = []
            ps = PendingSignal(
                encoded=encoded,
                target_hash=target_hash,
                queued_at=queued_at,
                ttl=ttl,
            )
            self._pending[target_hash].append(ps)
            self._stats["queued_signals"] += 1
        conn.close()

    def _save_pending(self, target_hash: str, ps: PendingSignal) -> None:
        """Guarda un mensaje pendiente en la BD."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO pending (target_hash, encoded, queued_at, ttl)
            VALUES (?, ?, ?, ?)
        ''', (target_hash, ps.encoded, ps.queued_at, ps.ttl))
        conn.commit()
        conn.close()

    def _delete_pending(self, target_hash: str, ps: PendingSignal) -> None:
        """Elimina un mensaje pendiente de la BD (cuando se entrega o expira)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM pending
            WHERE target_hash = ? AND queued_at = ? AND ttl = ?
        ''', (target_hash, ps.queued_at, ps.ttl))
        conn.commit()
        conn.close()

    # ── Arranque ──────────────────────────────────────────────────────────

    async def start(self, ssl_context: Optional[ssl.SSLContext] = None, dashboard_port: Optional[int] = None) -> None:
        """
        Inicia el servidor WebSocket y, opcionalmente, el dashboard HTTP.
        Si se proporciona ssl_context, se usa WSS.
        """
        self._server = await serve_websocket(
            handler=self._handle_connection,
            host=self.host,
            port=self.port,
            ssl=ssl_context,
        )
        proto = "wss" if ssl_context else "ws"
        self._info(f"🧠 NeuralHub (persistente) escuchando en {proto}://{self.host}:{self.port} con db {self.db_path}")

        if dashboard_port is not None:
            await self._start_dashboard(dashboard_port)

        asyncio.create_task(self._requeue_loop())
        asyncio.create_task(self._heartbeat_loop())

    async def _start_dashboard(self, port: int) -> None:
        """Inicia el servidor HTTP del dashboard usando aiohttp."""
        try:
            from aiohttp import web
        except ImportError:
            self._info("❌ aiohttp no está instalado. Para usar el dashboard ejecuta: pip install neural-hub[dashboard]")
            return

        # Ruta al archivo HTML (dentro del paquete)
        html_path = Path(__file__).parent / "dashboard" / "index.html"
        if not html_path.exists():
            self._info(f"⚠️ Archivo del dashboard no encontrado en {html_path}")
            return

        routes = web.RouteTableDef()

        @routes.get('/')
        async def index(request):
            return web.FileResponse(html_path)

        @routes.get('/api/status')
        async def api_status(request):
            return web.json_response(self.status())

        app = web.Application()
        app.add_routes(routes)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, port)
        await site.start()
        self._dashboard_runner = runner
        self._info(f"📊 Dashboard disponible en http://{self.host}:{port}")

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._dashboard_runner:
            await self._dashboard_runner.cleanup()
        self._info("🔴 NeuralHub detenido")

    async def serve_forever(self, ssl_context: Optional[ssl.SSLContext] = None, dashboard_port: Optional[int] = None) -> None:
        await self.start(ssl_context, dashboard_port)
        await asyncio.Event().wait()

    # ── Handler de conexión entrante ──────────────────────────────────────

    async def _handle_connection(self, conn: WebSocketConnection) -> None:
        """Maneja el ciclo de vida completo de una conexión de agente."""
        entry: Optional[AgentEntry] = None

        try:
            # Esperar mensaje de registro
            data = await asyncio.wait_for(conn.recv(), timeout=10.0)
            if data is None:
                return

            if not is_ctrl(data):
                await conn.send(ctrl_msg("error", msg="Primer mensaje debe ser registro"))
                return

            ctrl = parse_ctrl(data)
            if ctrl.get("_ctrl") != "register":
                await conn.send(ctrl_msg("error", msg="Se esperaba registro"))
                return

            agent_id   = ctrl["agent_id"]
            neural_hash = ctrl["neural_hash"]

            # Registrar agente
            entry = AgentEntry(
                agent_id=agent_id,
                neural_hash=neural_hash,
                conn=conn,
            )
            self._agents[neural_hash] = entry
            self._names[agent_id] = neural_hash

            # Para round-robin: agregar a lista por nombre
            if agent_id not in self._agents_by_name:
                self._agents_by_name[agent_id] = []
                self._rr_index[agent_id] = 0
            self._agents_by_name[agent_id].append(neural_hash)

            self._info(f"🔗 Agente conectado: {agent_id} #{neural_hash[:8]}")

            # Confirmar registro + enviar directorio actual
            await conn.send(ctrl_msg(
                "registered",
                neural_hash=neural_hash,
                peers={k: v for k, v in self._names.items() if k != agent_id},
            ))

            # Notificar a peers de la nueva conexión
            await self._broadcast_ctrl(
                ctrl_msg("peer_joined", agent_id=agent_id, neural_hash=neural_hash),
                exclude=neural_hash,
            )

            # Entregar señales pendientes
            await self._flush_pending(neural_hash)

            # Loop de recepción de señales
            await self._receive_loop(entry)

        except asyncio.TimeoutError:
            self._info("⏱️  Timeout esperando registro")
        except Exception as e:
            self._info(f"❌ Error en conexión: {e}")
        finally:
            if entry:
                # Limpiar registros
                self._agents.pop(entry.neural_hash, None)
                self._names.pop(entry.agent_id, None)
                # Quitar de la lista round-robin
                if entry.agent_id in self._agents_by_name:
                    try:
                        self._agents_by_name[entry.agent_id].remove(entry.neural_hash)
                    except ValueError:
                        pass
                    if not self._agents_by_name[entry.agent_id]:
                        del self._agents_by_name[entry.agent_id]
                        self._rr_index.pop(entry.agent_id, None)
                self._info(f"🔌 Agente desconectado: {entry.agent_id}")
                await self._broadcast_ctrl(
                    ctrl_msg("peer_left", agent_id=entry.agent_id),
                    exclude=entry.neural_hash,
                )

    async def _receive_loop(self, entry: AgentEntry) -> None:
        """Loop principal: recibe señales de un agente y las rutea."""
        while True:
            data = await entry.conn.recv()
            if data is None:
                break

            entry.touch()

            # Mensajes de control
            if is_ctrl(data):
                await self._handle_ctrl(entry, parse_ctrl(data))
                continue

            # Señal neural binaria
            try:
                signal = NeuralSignal.decode(data)
            except Exception as e:
                self._info(f"⚠️  Frame inválido de {entry.agent_id}: {e}")
                continue

            self._stats["total_signals"] += 1
            self._stats["total_bytes"] += len(data)
            entry.signals_sent += 1

            await self._route_signal(signal, data, entry)

    # ── Control messages ─────────────────────────────────────────────────

    async def _handle_ctrl(self, entry: AgentEntry, ctrl: dict) -> None:
        t = ctrl.get("_ctrl")

        if t == "discover":
            name = ctrl.get("name")
            h = self._names.get(name)
            await entry.conn.send(ctrl_msg(
                "discover_result",
                name=name,
                neural_hash=h,
                online=h in self._agents,
            ))

        elif t == "synapse_update":
            # Agente reporta resultado de transmisión → actualizar plasticidad
            key = f"{ctrl['src']}:{ctrl['tgt']}"
            if key not in self._synapses:
                self._synapses[key] = Synapse(ctrl["src"], ctrl["tgt"])
            syn = self._synapses[key]
            if ctrl.get("success"):
                syn.reinforce()
            else:
                syn.weaken()
            # Persistir
            self._save_synapse(key, syn)

        elif t == "status":
            await entry.conn.send(ctrl_msg(
                "hub_status",
                agents=len(self._agents),
                synapses=len(self._synapses),
                stats=self._stats,
                peers={a.agent_id: a.info() for a in self._agents.values()},
            ))

        elif t == "ping":
            await entry.conn.send(ctrl_msg("pong", ts=time.time()))

    # ── Routing con round-robin ───────────────────────────────────────────

    async def _route_signal(
        self,
        signal: NeuralSignal,
        raw: bytes,
        sender: AgentEntry,
    ) -> None:
        """
        Enruta una señal.
        - Si target está vacío: broadcast.
        - Si target es un hash conocido: unicast directo.
        - Si target es un nombre (no hash): resuelve con round-robin.
        """
        target_str = signal.target

        # Caso 1: Broadcast
        if not target_str:
            delivered = 0
            for h, agent in list(self._agents.items()):
                if h == signal.source:
                    continue
                try:
                    await agent.conn.send(raw)
                    agent.signals_received += 1
                    delivered += 1
                except Exception:
                    pass
            self._info(
                f"📡 BROADCAST {signal.signal_type.name} "
                f"de {sender.agent_id} → {delivered} agentes"
            )
            return

        # Caso 2: ¿Es un hash conocido?
        if target_str in self._agents:
            # Unicast directo a ese hash
            target = self._agents[target_str]
            try:
                await target.conn.send(raw)
                target.signals_received += 1
                self._info(
                    f"  ↪ {signal.signal_type.emoji()} "
                    f"{sender.agent_id}→{target.agent_id} (hash) "
                    f"({len(raw)}B)"
                )
            except Exception as e:
                self._info(f"❌ Error enviando a {target_str[:8]}: {e}")
                self._enqueue(target_str, raw)
            return

        # Caso 3: Interpretar como nombre lógico
        # Buscar la lista de hashes para ese nombre
        hashes = self._agents_by_name.get(target_str, [])
        if not hashes:
            # No hay agentes con ese nombre conectados
            self._info(f"⚠️  Nombre desconocido o sin agentes: '{target_str}'")
            # Opcional: podríamos encolar para cuando aparezca, pero no sabemos el hash.
            # Mejor descartar.
            return

        # Round-robin: seleccionar el siguiente hash
        idx = self._rr_index.get(target_str, 0)
        target_hash = hashes[idx % len(hashes)]
        # Actualizar índice para la próxima vez
        self._rr_index[target_str] = (idx + 1) % len(hashes)

        target = self._agents.get(target_hash)
        if target:
            try:
                await target.conn.send(raw)
                target.signals_received += 1
                self._info(
                    f"  ↪ {signal.signal_type.emoji()} "
                    f"{sender.agent_id}→{target.agent_id} (round-robin '{target_str}') "
                    f"({len(raw)}B)"
                )
            except Exception as e:
                self._info(f"❌ Error enviando a {target_hash[:8]}: {e}")
                self._enqueue(target_hash, raw)
        else:
            # El hash ya no está conectado (raro, porque lo sacamos de la lista)
            # Lo encolamos igualmente
            self._enqueue(target_hash, raw)
            self._info(f"📬 Señal encolada para {target_hash[:8]} (offline)")

    def _enqueue(self, target_hash: str, raw: bytes) -> None:
        if target_hash not in self._pending:
            self._pending[target_hash] = []
        ps = PendingSignal(encoded=raw, target_hash=target_hash)
        self._pending[target_hash].append(ps)
        self._stats["queued_signals"] += 1
        # Persistir
        self._save_pending(target_hash, ps)

    async def _flush_pending(self, neural_hash: str) -> None:
        """Entrega señales encoladas cuando un agente se reconecta."""
        pending = self._pending.pop(neural_hash, [])
        if not pending:
            return

        agent = self._agents.get(neural_hash)
        if not agent:
            return

        delivered = 0
        for ps in pending:
            if ps.expired:
                self._stats["dropped_signals"] += 1
                self._delete_pending(neural_hash, ps)
                continue
            try:
                await agent.conn.send(ps.encoded)
                delivered += 1
                self._delete_pending(neural_hash, ps)
            except Exception:
                # Si falla, lo volvemos a encolar
                self._enqueue(neural_hash, ps.encoded)

        if delivered:
            self._info(f"📨 {delivered} señales pendientes entregadas a {agent.agent_id}")

    async def _broadcast_ctrl(self, msg: bytes, exclude: str = "") -> None:
        for h, agent in list(self._agents.items()):
            if h == exclude:
                continue
            try:
                await agent.conn.send(msg)
            except Exception:
                pass

    # ── Background tasks ─────────────────────────────────────────────────

    async def _requeue_loop(self) -> None:
        """Limpia señales expiradas de la cola cada 60s."""
        while True:
            await asyncio.sleep(60)
            for h in list(self._pending.keys()):
                before = len(self._pending[h])
                self._pending[h] = [p for p in self._pending[h] if not p.expired]
                dropped = before - len(self._pending[h])
                if dropped:
                    self._stats["dropped_signals"] += dropped
                if not self._pending[h]:
                    del self._pending[h]
            # Limpiar BD de mensajes muy antiguos
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM pending WHERE queued_at < ?", (time.time() - 300,))
            conn.commit()
            conn.close()

    async def _heartbeat_loop(self) -> None:
        """Imprime stats cada 30s."""
        while True:
            await asyncio.sleep(30)
            uptime = time.time() - self._stats["started_at"]
            pending_total = sum(len(v) for v in self._pending.values())
            self._info(
                f"💓 Heartbeat | agentes={len(self._agents)} "
                f"señales={self._stats['total_signals']} "
                f"bytes={self._stats['total_bytes']:,} "
                f"pendientes={pending_total} "
                f"uptime={uptime:.0f}s"
            )

    # ── Utilidades ────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "agents_online": len(self._agents),
            "synapses_tracked": len(self._synapses),
            "pending_queues": sum(len(v) for v in self._pending.values()),
            "stats": self._stats,
            "agents": {a.neural_hash: a.info() for a in self._agents.values()},
            "synapses": {
                k: {"strength": round(s.strength, 3), "success_rate": round(s.success_rate, 3)}
                for k, s in self._synapses.items()
            },
        }

    def _info(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] HUB | {msg}"
        self._log.append(line)
        print(line)