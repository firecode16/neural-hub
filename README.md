# 🧠 NeuralHub

**Central server for NeuralProtocol agents**  
Acts as the brain's thalamus – all signals pass through, but it doesn't decide.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Dependency: neural-protocol](https://img.shields.io/badge/dependency-neural--protocol-blue)](https://github.com/firecode16/neural-protocol)

---

## What is NeuralHub?

NeuralHub is the central nervous system for agent networks built with [NeuralProtocol](https://github.com/firecode16/neural-protocol).  
It provides:

- **Registry** – keeps track of all connected agents (by logical name and unique hash).
- **Router** – forwards signals to the correct destination (unicast, broadcast, or round‑robin to multiple agents with the same name).
- **Synaptic Database** – persists Hebbian learning (synapse strength) between agent restarts.
- **Message Queue** – stores signals for offline agents and delivers them upon reconnection.
- **Monitor** – real‑time metrics and heartbeats (with optional web dashboard).
- **Security** – optional SSL/TLS (WSS) for production.
- **Federation (Fase 2)** – dynamic discovery and presence between hubs.  
  *Hubs now exchange agent lists in real time, enabling optimized routing and availability checks.*
- **JSON‑RPC 2.0 API** – expose hub functionality (status, discovery, signal transmission, remote agent queries) via a standard RPC interface, with versioned methods for future evolution.

All this while remaining **stateless from the agents' perspective**: they just connect, send logical names, and the hub does the rest.

---

## Features

✅ **Automatic agent registration** – agents identify themselves on connect.  
✅ **Round‑robin load balancing** – distribute signals among multiple agents with the same logical name (e.g., 3 `"sales"` agents).  
✅ **Offline queuing** – messages are stored for disconnected agents (TTL‑based expiration).  
✅ **Persistent synapses** – SQLite database stores synaptic weights across hub restarts.  
✅ **Built‑in WebSocket (RFC 6455)** – pure asyncio implementation, no external dependencies.  
✅ **WSS (WebSocket Secure)** – enable SSL with a single flag.  
✅ **Heartbeat & metrics** – periodic status logs and a `status` control message.  
✅ **Resilience** – agents automatically reconnect with exponential backoff.  
✅ **Optional Web Dashboard** – real‑time monitoring of agents, synapses, traffic, and pending queues.  
✅ **Federation (Fase 2)** – dynamic discovery, presence tracking, and heartbeat between hubs.  
✅ **JSON‑RPC 2.0 API** – rich RPC interface with versioned methods (`v1.hub.status`, `v1.agent.list`, `v1.remote_agent.discover`, etc.).  
✅ **Zero config by default** – works out‑of‑the‑box with `127.0.0.1:8765`.

---

## Requirements

- Python 3.9 or higher
- [neural‑protocol](https://github.com/firecode16/neural-protocol) (installed automatically as a dependency)
- For the dashboard: `aiohttp` (installed via `[dashboard]` extra)

---

## Installation

```bash
# Basic installation (without dashboard)
pip install neural-hub

# With dashboard support (recommended for monitoring)
pip install neural-hub[dashboard]
```

Or install from source for development:

```bash
git clone https://github.com/firecode16/neural-hub.git
cd neural-hub
pip install -e .[dashboard]   # includes aiohttp
```

---

## Quick Start

### 1. Start the hub (default: ws://127.0.0.1:8765)

```bash
neural-hub
```

Or using the module directly:

```bash
python -m neural_hub.scripts.run_hub
```

You'll see heartbeat messages every 30 seconds:

```
[14:23:03] HUB | 🧠 NeuralHub (persistente) escuchando en ws://127.0.0.1:8765 con db neural_hub_8765.db
[14:23:33] HUB | 💓 Heartbeat | agentes=0 señales=0 bytes=0 pendientes=0 remotos=0 uptime=30s
```

### 2. (Optional) Launch the monitoring dashboard

```bash
neural-hub --dashboard-port 8080
```

Now visit [http://127.0.0.1:8080](http://127.0.0.1:8080) to see real‑time metrics, connected agents, synapse strengths, and a live signal stream.

### 3. Connect agents

Agents must use the `WSNeuralAgent` class from `neural-protocol`. Here's a simple example:

```python
from neural_protocol.agent.base_ws import WSNeuralAgent
from neural_protocol.core.signal import NeuralSignalType
import asyncio

class MyAgent(WSNeuralAgent):
    async def handle_signal(self, signal):
        print(f"Received: {signal}")

async def main():
    agent = MyAgent("worker", hub_host="127.0.0.1", hub_port=8765)
    await agent.start()
    await agent.transmit("another_agent", NeuralSignalType.NOREPINEPHRINE, {"task": "process"})
    await asyncio.sleep(5)
    await agent.stop()

asyncio.run(main())
```

### 4. See it in action

Run the included round‑robin demo (spawns 3 sales agents and sends 6 signals):

```bash
python -m neural_hub.scripts.run_roundrobin_demo
```

While it runs, watch the dashboard update in real time. Output in the terminal:

```
📊 Señales recibidas por cada agente de ventas:
  Ventas-1 recibió 2 señales
  Ventas-2 recibió 2 señales
  Ventas-3 recibió 2 señales
```

---

## 📡 JSON‑RPC 2.0 API

The hub exposes a rich JSON‑RPC 2.0 API over the same WebSocket connection (and future HTTP transport). This allows external tools, monitoring systems, or agents themselves to interact programmatically with the hub.

### Versioning

Methods can include a version prefix (e.g., `v1.agent.list`). If no prefix is given, the hub assumes `v1`. This allows future evolution without breaking existing clients.

**Supported versions:** `v1`

### Methods

| Method | Versions | Description | Parameters | Returns |
|--------|----------|-------------|------------|---------|
| `hub.status` | v1 | Get hub status and statistics | (none) | Hub status object |
| `hub.ping` | v1 | Health check | (none) | `{"pong": true, "ts": timestamp}` |
| `agent.list` | v1 | List all connected agents | (none) | `{"agents": [agent_info, ...]}` |
| `agent.discover` | v1 | Get info about a specific agent | `{"name": agent_id}` | `{"agent_id": str, "neural_hash": str, "online": bool}` |
| `agent.transmit` | v1 | Transmit a neural signal on behalf of an agent | `{"target": str, "signal_type": str, "payload": object}` | `{"delivered": true, "msg_id": str}` |
| **`remote_agent.discover`** | v1 | Check availability of a remote agent (from federated hubs) | `{"name": "nombre@dominio"}` | `{"exists": bool, "online": bool, "last_seen": float}` |
| **`hub.remote_agents`** | v1 | List all known remote agents from federated hubs | (optional `{"domain": "..."}`) | `{"agents": [{"name": str, "domain": str, "hash": str, "online": bool, "last_seen": float}]}` |

### Example Usage

#### From a Python agent (using `WSNeuralAgent`)

```python
# Check if a remote agent is available before sending
info = await agent.jsonrpc_call("remote_agent.discover", {"name": "vendedor@empresa-b.com"})
if info["online"]:
    await agent.transmit("vendedor@empresa-b.com", NeuralSignalType.NOREPINEPHRINE, {...})
else:
    print("Remote agent is offline")

# List all remote agents
remotes = await agent.jsonrpc_call("hub.remote_agents")
for ra in remotes["agents"]:
    print(f"{ra['name']}@{ra['domain']} - online: {ra['online']}")
```

#### From any WebSocket client (e.g., JavaScript)

```javascript
const ws = new WebSocket("ws://localhost:8765");
ws.onopen = () => {
  const request = {
    jsonrpc: "2.0",
    method: "remote_agent.discover",
    params: { name: "vendedor@empresa-b.com" },
    id: 1
  };
  ws.send(JSON.stringify(request));
};
ws.onmessage = (event) => {
  const response = JSON.parse(event.data);
  console.log(response.result);
};
```

### Error Codes

| Code | Meaning |
|------|---------|
| -32700 | Parse error (invalid JSON) |
| -32600 | Invalid Request (missing jsonrpc field, wrong version) |
| -32601 | Method not found |
| -32602 | Invalid params (missing required field or wrong type) |
| -32603 | Internal error |

---

## 🌐 Federación entre Hubs (Fase 2: Descubrimiento Dinámico)

Ahora los hubs pueden intercambiar información en tiempo real sobre los agentes disponibles, permitiendo enrutamiento optimizado y consultas de presencia antes de enviar señales.

### Novedades en Fase 2

- **Intercambio automático de listas de agentes** (`HUB_PEER_UPDATE`): cuando un agente se conecta o desconecta, todos los hubs remotos son notificados.
- **Heartbeat entre hubs**: cada conexión hub-hub incluye ping/pong periódico para detectar fallos rápidamente (timeout configurable).
- **Verificación de disponibilidad**: antes de reenviar una señal, el hub local consulta su registro remoto; si el agente destino no existe o está offline, la señal no se envía.
- **API JSON‑RPC extendida**: nuevos métodos para consultar agentes remotos.

### Configuración (idéntica a Fase 1)

Cada hub define sus vecinos en un archivo JSON. Ejemplo `remotes.json`:

```json
{
    "empresa-b.com": {
        "url": "wss://hub.empresa-b.com:8765",
        "token": "secreto123",
        "enabled": true,
        "autoconnect": true
    }
}
```

Inicia el hub con:

```bash
neural-hub --port 8765 --domain empresa-a.com --remote-hubs remotes.json
```

Una vez conectados, los hubs comenzarán a intercambiar automáticamente sus listas de agentes.

### Uso desde agentes

Los agentes pueden consultar disponibilidad remota antes de enviar:

```python
# Consultar disponibilidad
info = await agent.jsonrpc_call("remote_agent.discover", {"name": "vendedor@empresa-b.com"})
if info["online"]:
    # Enviar señal solo si está disponible
    await agent.transmit("vendedor@empresa-b.com", NeuralSignalType.NOREPINEPHRINE, {...})
```

El hub también rechazará automáticamente envíos a agentes remotos no disponibles (sin necesidad de consulta previa).

---

## 📚 Example Projects

For a complete, real-world example of federated agents in a B2B scenario (purchase-sales-invoicing), check out the **[federacion-demo](https://github.com/firecode16/federacion-demo)** repository. It demonstrates two companies with agents `comprador`, `vendedor`, and `facturacion` communicating across hubs.

---

## Advanced Usage

### Dashboard API Endpoint

The dashboard serves a simple REST API at `/api/status`. You can query it directly:

```bash
curl http://127.0.0.1:8080/api/status
```

It returns a JSON with current hub state (agents, synapses, stats, and remote agents count).

### Using SSL/WSS in Production

1. **Generate SSL certificates** (self‑signed for testing, real CA for production):

```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes
```

2. **Start the hub with SSL**:

```bash
neural-hub --ssl --cert cert.pem --key key.pem --port 8765
```

3. **Connect agents with SSL**:

```python
agent = MyAgent("worker", hub_host="127.0.0.1", hub_port=8765, use_ssl=True)
```

The hub will now use `wss://`.

### Persistent Database

By default, the hub creates a SQLite file `neural_hub_<port>.db`. You can specify a custom path in code:

```python
hub = NeuralHub(db_path="/path/to/my.db")
```

The database stores:
- All synapses (`src_tgt`, strength, counts)
- Pending messages (until delivered or expired)

### Changing Host / Port

```bash
neural-hub --host 0.0.0.0 --port 9000
```

Now agents must connect to `ws://<your-ip>:9000` (or `wss://...` with SSL). The dashboard (if enabled) will be available at `http://<your-ip>:8080`.

### Graceful Shutdown

The hub handles `Ctrl+C` cleanly – you'll see:

```
🛑 Interrupción recibida, cerrando hub...
👋 Hub detenido correctamente.
```

---

## Architecture Overview

```
┌─────────────────┐     WebSocket      ┌─────────────────┐
│   Agent A       │◄──────────────────►│                 │
│  (soporte)      │                     │                 │
└─────────────────┘                     │                 │
                                        │   NeuralHub     │
┌─────────────────┐     WebSocket      │  - Registry     │
│   Agent B       │◄──────────────────►│  - Router       │
│  (ventas #1)    │                     │  - Synapse DB   │
└─────────────────┘                     │  - Queue        │
                                        │  - Monitor      │
┌─────────────────┐     WebSocket      │  - Dashboard    │
│   Agent C       │◄──────────────────►│    (optional)   │
│  (ventas #2)    │                     └────────┬────────┘
└─────────────────┘                              │
                                                  │  Federation (Fase 2)
                                           ┌──────▼──────┐
                                           │  Remote Hub │
                                           │ (empresa-b) │
                                           └─────────────┘
                                                 │
                                      (intercambio de listas
                                       y heartbeats periódicos)
```

- All communication is bidirectional WebSocket (binary frames for signals, JSON for control and JSON‑RPC).
- Hubs exchange `HUB_PEER_UPDATE` messages to keep remote agent registries in sync.
- Heartbeat (ping/pong) ensures quick detection of failed connections.
- JSON‑RPC methods allow querying remote agent presence.

---

## Robustness & Performance

- **Automatic reconnection** – both agents and hub‑to‑hub connections use exponential backoff.
- **Message persistence** – signals for offline agents are stored in SQLite and delivered on reconnection.
- **TTL expiration** – old pending messages are automatically purged.
- **Heartbeat monitoring** – hubs log regular status updates; dashboard shows live metrics.
- **Federation heartbeat** – active ping/pong between hubs detects failures within seconds.
- **JSON‑RPC resilience** – malformed requests are rejected with proper error codes; internal exceptions are caught and reported.
- **Throughput**: A single hub handles thousands of signals per second. Federation adds minimal overhead.
- **Latency**: <1ms local, 1‑50ms over network, plus network RTT for federated hops.

---

## Scripts Included

| Script                          | Description                                                                 |
|---------------------------------|-----------------------------------------------------------------------------|
| `neural_hub/scripts/run_hub.py` | Main entry point. Supports `--port`, `--host`, `--domain`, `--ssl`, `--cert`, `--key`, `--dashboard-port`, `--remote-hubs`. |
| `neural_hub/scripts/run_roundrobin_demo.py` | Demonstrates round‑robin with 3 sales agents.                             |

Run any script with `python -m neural_hub.scripts.<script_name>`.

---

## Testing

Run the test suite:

```bash
python -m unittest discover tests
```

Tests cover:
- Agent registration and discovery.
- Signal routing (unicast, broadcast, round‑robin).
- Offline queuing and delivery.
- Synapse persistence.
- **Federation Fase 2** (hub registration, peer updates, heartbeat, remote agent discovery).
- **JSON‑RPC API** (all methods, error handling, versioning, remote queries).

---

## Integrating with Your Agents

Your agents should inherit from `neural_protocol.agent.base_ws.WSNeuralAgent`.  
The only requirement is to call `await agent.start()` and implement `handle_signal`.

To use federation, simply pass the `domain` parameter when creating the agent:

```python
agent = MyAgent(agent_id="comprador", domain="empresa-a.com", hub_host="localhost", hub_port=8765)
```

The agent will automatically include its domain during registration, allowing the hub to route replies correctly.

To query remote agent presence (new in Fase 2), use the JSON‑RPC methods:

```python
info = await agent.jsonrpc_call("remote_agent.discover", {"name": "vendedor@empresa-b.com"})
if info["online"]:
    await agent.transmit("vendedor@empresa-b.com", NeuralSignalType.NOREPINEPHRINE, {...})
```

For a complete example of custom agents in a federated scenario, see the **[federacion-demo](https://github.com/firecode16/federacion-demo)** project.

---

## Roadmap

### ✅ Fase 1: Conexión básica entre hubs (completada)
- Configuración manual de hubs remotos.
- Conexión persistente con reconexión automática.
- Autenticación mediante token compartido.
- Reenvío de señales con TTL.
- **JSON‑RPC 2.0 API básica** (`hub.status`, `agent.list`, `agent.transmit`).

### ✅ Fase 2: Descubrimiento dinámico y presencia (completada)
- Intercambio de listas de agentes entre hubs (`HUB_PEER_UPDATE`).
- Enrutamiento optimizado (el hub sabe de antemano si un destino remoto existe).
- Heartbeats entre hubs y detección de fallos mejorada.
- **Ampliación de la API JSON‑RPC** para soportar consultas de presencia remota (`remote_agent.discover`, `hub.remote_agents`).

### ⏳ Fase 3: Alta disponibilidad y balanceo (próximo)
- Múltiples hubs por dominio (clúster).
- Resolución de conflictos de nombres.
- Sincronización de estado entre réplicas.
- **Versionado completo de API** y documentación OpenAPI.

Contributions and ideas are welcome!

---

## License

MIT © 2026 Fredy Hernandez

---

**Built for agents that think together.**  
Use with [neural‑protocol](https://github.com/firecode16/neural-protocol) to create resilient, self‑learning multi‑agent systems.
