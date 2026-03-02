"""
NeuralHub — Servidor Central del Protocolo Neural
===================================================
Versión con persistencia SQLite, round-robin automático, dashboard opcional
y soporte para federación entre hubs (Fase 1). Incluye dominio propio configurable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import ssl
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from neural_protocol.core.signal import NeuralSignal, NeuralSignalType
from neural_protocol.core.synapse import Synapse
from neural_protocol.transport.websocket import (
    WebSocketConnection,
    serve_websocket,
    connect_websocket,
    ctrl_msg,
    is_ctrl,
    parse_ctrl,
)
from neural_protocol.utils.constants import (
    GLOBAL_ID_SEPARATOR,
    CTRL_FWD_SIGNAL,
    CTRL_HUB_REGISTER,
    CTRL_HUB_PEER_UPDATE,
)


@dataclass
class AgentEntry:
    agent_id: str
    neural_hash: str
    conn: WebSocketConnection
    domain: Optional[str] = None                # ← dominio del agente (para federación)
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
            "domain": self.domain,
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


class RemoteHubConnection:
    """
    Gestiona una conexión saliente a un hub remoto (federación).
    Se encarga de la reconexión automática y el envío de mensajes.
    """

    def __init__(self, hub: NeuralHub, domain: str, url: str, token: Optional[str], enabled: bool):
        self.hub = hub
        self.domain = domain
        self.url = url
        self.token = token
        self.enabled = enabled
        self._conn: Optional[WebSocketConnection] = None
        self._connected = asyncio.Event()
        self._reconnect_task: Optional[asyncio.Task] = None
        self._reconnect_attempts = 0
        self.logger = logging.getLogger(f"RemoteHub-{domain}")

    async def connect(self):
        """Inicia la conexión y el bucle de reconexión."""
        self._reconnect_task = asyncio.create_task(self._connection_loop())

    async def _connection_loop(self):
        """Bucle con reconexión automática."""
        while self.enabled and self.hub._running:
            try:
                await self._connect_and_register()
                self._reconnect_attempts = 0
                await self._receive_loop()
            except Exception as e:
                self.logger.error(f"⚠️  Conexión perdida con {self.domain}: {e}")

            if not self.hub._running:
                break

            self._connected.clear()
            delay = min(1.0 * (2 ** self._reconnect_attempts), 30.0)
            self._reconnect_attempts += 1
            self.logger.info(f"🔄 Reconectando a {self.domain} en {delay:.1f}s...")
            await asyncio.sleep(delay)

    async def _connect_and_register(self):
        """Conecta y envía registro como hub."""
        self.logger.info(f"🔌 Conectando a hub remoto {self.domain} ({self.url})")
        parsed = urllib.parse.urlparse(self.url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        use_ssl = parsed.scheme == "wss"

        self._conn = await connect_websocket(host, port, ssl_param=use_ssl)

        # Enviar HUB_REGISTER con el dominio propio del hub local
        reg_msg = ctrl_msg(
            CTRL_HUB_REGISTER,
            domain=self.hub._get_my_domain(),
            token=self.token or ""
        )
        await self._conn.send(reg_msg)

        # Esperar confirmación
        data = await asyncio.wait_for(self._conn.recv(), timeout=10.0)
        if data is None:
            raise ConnectionError("Hub remoto cerró conexión")
        resp = parse_ctrl(data)
        if resp.get("_ctrl") != "hub_registered":
            raise ConnectionError(f"Registro rechazado por {self.domain}: {resp}")

        self.logger.info(f"✅ Registrado en hub remoto {self.domain}")
        self._connected.set()

    async def _receive_loop(self):
        """Escucha mensajes del hub remoto."""
        while self._conn and not self._conn.closed and self.hub._running:
            data = await self._conn.recv()
            if data is None:
                break

            if is_ctrl(data):
                ctrl = parse_ctrl(data)
                ctrl_type = ctrl.get("_ctrl")
                if ctrl_type == CTRL_FWD_SIGNAL:
                    # Procesar mensaje reenviado (entrante desde remoto)
                    await self.hub._handle_fwd_signal(ctrl)
                elif ctrl_type == "pong":
                    pass  # heartbeat
                else:
                    self.logger.debug(f"Control ignorado: {ctrl_type}")
            else:
                # No deberían llegar señales directas de agentes por este canal
                self.logger.warning("Recibido dato binario inesperado de hub remoto")

    async def send_ctrl(self, ctrl: dict) -> bool:
        """
        Envía un mensaje de control al hub remoto.
        El diccionario debe contener la clave '_ctrl' con el tipo de mensaje.
        """
        if not self._connected.is_set():
            self.logger.warning(f"Intento de envío sin conexión a {self.domain}")
            return False
        try:
            # Extraemos el tipo de control y el resto de parámetros
            ctrl_copy = ctrl.copy()
            ctrl_type = ctrl_copy.pop("_ctrl")
            await self._conn.send(ctrl_msg(ctrl_type, **ctrl_copy))
            return True
        except Exception as e:
            self.logger.error(f"Error enviando a {self.domain}: {e}")
            return False

    async def close(self):
        """Cierra la conexión."""
        self.enabled = False
        if self._conn:
            await self._conn.close()
        if self._reconnect_task:
            self._reconnect_task.cancel()


class NeuralHub:
    """
    Servidor central WebSocket del NeuralProtocol con persistencia SQLite y round-robin.
    Ahora con soporte para federación (conexión a otros hubs) y dominio propio configurable.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        domain: Optional[str] = None,                     # dominio propio
        db_path: str = "neural_hub.db",
        remote_hubs: Optional[Dict[str, dict]] = None,    # hubs remotos
    ):
        self.host = host
        self.port = port
        self.domain = domain or f"{host}:{port}"          # ← valor por defecto
        self.db_path = db_path
        self.remote_hubs = remote_hubs or {}

        # Registry en memoria (agentes conectados)
        self._agents: Dict[str, AgentEntry] = {}          # neural_hash → AgentEntry
        self._names: Dict[str, str] = {}                  # agent_id → neural_hash (para resolución rápida)
        self._agents_by_name: Dict[str, List[str]] = {}   # nombre → lista de hashes (para round-robin)
        self._rr_index: Dict[str, int] = {}               # nombre → próximo índice

        # Conexiones a otros hubs (federación)
        self._outgoing_remote_connections: Dict[str, RemoteHubConnection] = {}  # dominio → conexión saliente
        self._incoming_remote_connections: Dict[str, WebSocketConnection] = {}  # dominio → conexión entrante

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
        self._running = False

        # Logger
        self.logger = logging.getLogger("NeuralHub")

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
        self._running = True
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

        # Conectar a hubs remotos configurados
        for domain, config in self.remote_hubs.items():
            if config.get("autoconnect", True):
                asyncio.create_task(self._connect_to_remote_hub(domain, config))

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
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._dashboard_runner:
            await self._dashboard_runner.cleanup()
        # Cerrar conexiones a hubs remotos
        for conn in self._outgoing_remote_connections.values():
            await conn.close()
        for conn in self._incoming_remote_connections.values():
            await conn.close()
        self._info("🔴 NeuralHub detenido")

    async def serve_forever(self, ssl_context: Optional[ssl.SSLContext] = None, dashboard_port: Optional[int] = None) -> None:
        await self.start(ssl_context, dashboard_port)
        await asyncio.Event().wait()

    # ── Conexiones a hubs remotos (salientes) ───────────────────────────

    async def _connect_to_remote_hub(self, domain: str, config: dict) -> None:
        """Establece conexión saliente a un hub remoto."""
        if domain in self._outgoing_remote_connections:
            return
        conn = RemoteHubConnection(
            hub=self,
            domain=domain,
            url=config["url"],
            token=config.get("token"),
            enabled=config.get("enabled", True),
        )
        self._outgoing_remote_connections[domain] = conn
        await conn.connect()

    def _get_my_domain(self) -> str:
        """Devuelve el dominio propio para identificación en federación."""
        return self.domain

    # ── Handler de conexión entrante ──────────────────────────────────────

    async def _handle_connection(self, conn: WebSocketConnection) -> None:
        """Maneja el ciclo de vida completo de una conexión entrante (agente u otro hub)."""
        # Esperar primer mensaje (debe ser control)
        try:
            data = await asyncio.wait_for(conn.recv(), timeout=10.0)
        except asyncio.TimeoutError:
            self._info("⏱️  Timeout esperando mensaje inicial")
            return
        if data is None:
            return

        if not is_ctrl(data):
            await conn.send(ctrl_msg("error", msg="Primer mensaje debe ser de control"))
            return

        ctrl = parse_ctrl(data)
        ctrl_type = ctrl.get("_ctrl")

        # Caso 1: Registro de otro hub
        if ctrl_type == CTRL_HUB_REGISTER:
            await self._handle_hub_register(conn, ctrl)
            return

        # Caso 2: Registro de agente (comportamiento original)
        if ctrl_type != "register":
            await conn.send(ctrl_msg("error", msg="Se esperaba registro de agente"))
            return

        # --- A partir de aquí es el flujo normal para agentes ---
        agent_id   = ctrl["agent_id"]
        neural_hash = ctrl["neural_hash"]
        domain = ctrl.get("domain")  # ← campo opcional

        # Registrar agente
        entry = AgentEntry(
            agent_id=agent_id,
            neural_hash=neural_hash,
            domain=domain,
            conn=conn,
        )
        self._agents[neural_hash] = entry
        self._names[agent_id] = neural_hash

        # Para round-robin: agregar a lista por nombre
        if agent_id not in self._agents_by_name:
            self._agents_by_name[agent_id] = []
            self._rr_index[agent_id] = 0
        self._agents_by_name[agent_id].append(neural_hash)

        self._info(f"🔗 Agente conectado: {agent_id} #{neural_hash[:8]}" + (f"@{domain}" if domain else ""))

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
        try:
            await self._receive_loop(entry)
        except Exception as e:
            self._info(f"❌ Error en loop de recepción: {e}")
        finally:
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

    async def _handle_hub_register(self, conn: WebSocketConnection, ctrl: dict) -> None:
        """Registra una conexión entrante de otro hub."""
        domain = ctrl.get("domain")
        token = ctrl.get("token")
        # Validar token contra configuración local
        expected_token = self.remote_hubs.get(domain, {}).get("token")
        if not expected_token or token != expected_token:
            self._info(f"🚫 Intento de registro de hub {domain} con token inválido")
            await conn.close()
            return

        self._incoming_remote_connections[domain] = conn
        self._info(f"🔌 Hub remoto conectado (entrante): {domain}")

        # Responder confirmación
        await conn.send(ctrl_msg("hub_registered", domain=domain))

        # Lanzar tarea para recibir mensajes de este hub
        asyncio.create_task(self._hub_receive_loop(conn, domain))

    async def _hub_receive_loop(self, conn: WebSocketConnection, domain: str) -> None:
        """Bucle de recepción para mensajes de un hub remoto entrante."""
        try:
            while self._running and not conn.closed:
                data = await conn.recv()
                if data is None:
                    break
                if is_ctrl(data):
                    ctrl = parse_ctrl(data)
                    ctrl_type = ctrl.get("_ctrl")
                    if ctrl_type == CTRL_FWD_SIGNAL:
                        await self._handle_fwd_signal(ctrl)
                    elif ctrl_type == "pong":
                        pass
                    else:
                        self._info(f"Control ignorado de hub {domain}: {ctrl_type}")
                else:
                    self._info(f"⚠️  Dato binario inesperado de hub {domain}")
        except Exception as e:
            self._info(f"❌ Error en recepción de hub {domain}: {e}")
        finally:
            self._incoming_remote_connections.pop(domain, None)
            self._info(f"🔌 Hub remoto desconectado (entrante): {domain}")

    async def _receive_loop(self, entry: AgentEntry) -> None:
        """Loop principal: recibe señales de un agente y las rutea."""
        while self._running:
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

    # ── Routing con soporte para federación ───────────────────────────────

    async def _route_signal(
        self,
        signal: NeuralSignal,
        raw: bytes,
        sender: AgentEntry,
    ) -> None:
        """
        Enruta una señal.
        - Si target contiene '@', se reenvía a otro hub.
        - Si target está vacío: broadcast.
        - Si target es un hash conocido: unicast directo.
        - Si target es un nombre (no hash): resuelve con round-robin.
        """
        target_str = signal.target

        # 1. Detectar si es destino remoto (contiene '@')
        if GLOBAL_ID_SEPARATOR in target_str:
            local_name, domain = target_str.split(GLOBAL_ID_SEPARATOR, 1)
            if domain in self.remote_hubs:
                # Reenviar a hub remoto
                success = await self._forward_to_remote(domain, signal, raw, sender, local_name)
                if not success:
                    self._info(f"❌ No se pudo reenviar a {domain}")
                return
            else:
                self._info(f"⚠️  Dominio remoto desconocido: {domain}")
                return

        # 2. Broadcast
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

        # 3. ¿Es un hash conocido?
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

        # 4. Interpretar como nombre lógico local
        hashes = self._agents_by_name.get(target_str, [])
        if not hashes:
            self._info(f"⚠️  Nombre desconocido o sin agentes: '{target_str}'")
            return

        # Round-robin: seleccionar el siguiente hash
        idx = self._rr_index.get(target_str, 0)
        target_hash = hashes[idx % len(hashes)]
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
            self._enqueue(target_hash, raw)
            self._info(f"📬 Señal encolada para {target_hash[:8]} (offline)")

    async def _forward_to_remote(self, domain: str, signal: NeuralSignal, raw: bytes, sender: AgentEntry, target_agent: str) -> bool:
        """Reenvía una señal a un hub remoto."""
        # Buscar conexión saliente o entrante? Preferimos saliente (nosotros iniciamos)
        conn = self._outgoing_remote_connections.get(domain)
        if not conn:
            self._info(f"🚫 No hay conexión saliente activa al hub {domain}")
            return False

        # Construir mensaje FWD_SIGNAL
        fwd_msg = {
            "_ctrl": CTRL_FWD_SIGNAL,
            "from_hub": self._get_my_domain(),
            "to_hub": domain,
            "original_signal": raw.hex(),   # enviamos en hex para evitar problemas binarios en JSON
            "src_agent": signal.source,
            "tgt_agent": target_agent,
            "ttl": signal.ttl - 1,
        }
        return await conn.send_ctrl(fwd_msg)

    async def _handle_fwd_signal(self, ctrl: dict) -> None:
        """Procesa una señal reenviada desde otro hub."""
        from_hub = ctrl.get("from_hub")
        to_hub = ctrl.get("to_hub")  # debería ser este hub
        original_signal_hex = ctrl.get("original_signal")
        src_agent = ctrl.get("src_agent")
        tgt_agent = ctrl.get("tgt_agent")
        ttl = ctrl.get("ttl", 5)

        if ttl <= 0:
            self._info(f"⏱️  TTL agotado en mensaje de {from_hub}")
            return

        # Reconstruir la señal original
        try:
            original_bytes = bytes.fromhex(original_signal_hex)
            signal = NeuralSignal.decode(original_bytes)
        except Exception as e:
            self._info(f"❌ Error decodificando señal reenviada desde {from_hub}: {e}")
            return

        self._info(f"📨 Señal reenviada desde {from_hub} para {tgt_agent}")

        # Enrutar localmente, pero manteniendo el source original
        signal.target = tgt_agent
        # Buscar el agente local por nombre (round-robin si es necesario)
        await self._route_local(signal, original_bytes, src_agent)

    async def _route_local(self, signal: NeuralSignal, raw: bytes, original_src: str) -> None:
        """Enruta una señal dentro del hub local (usado para mensajes reenviados)."""
        target_name = signal.target
        hashes = self._agents_by_name.get(target_name, [])
        if not hashes:
            self._info(f"⚠️  Agente local '{target_name}' no encontrado para mensaje reenviado")
            # Opcional: notificar al hub origen del error
            return

        # Round-robin simple (podríamos usar el mismo índice)
        idx = self._rr_index.get(target_name, 0)
        target_hash = hashes[idx % len(hashes)]
        self._rr_index[target_name] = (idx + 1) % len(hashes)

        target = self._agents.get(target_hash)
        if target:
            try:
                await target.conn.send(raw)
                target.signals_received += 1
                self._info(f"  ↪ (reenviado) {signal.signal_type.emoji()} → {target.agent_id}")
            except Exception as e:
                self._info(f"❌ Error entregando señal reenviada a {target_hash[:8]}: {e}")
                self._enqueue(target_hash, raw)
        else:
            self._enqueue(target_hash, raw)

    def _enqueue(self, target_hash: str, raw: bytes) -> None:
        if target_hash not in self._pending:
            self._pending[target_hash] = []
        ps = PendingSignal(encoded=raw, target_hash=target_hash)
        self._pending[target_hash].append(ps)
        self._stats["queued_signals"] += 1
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
        while self._running:
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
        while self._running:
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