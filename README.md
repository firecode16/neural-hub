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
- **Federation (NEW!)** – connect multiple hubs together for cross‑organisational B2B communication.  
  *Phase 1 complete: static configuration, token‑based authentication, and signal forwarding.*

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
✅ **Federation (Fase 1)** – connect hubs across domains with static configuration and token authentication.  
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
[14:23:33] HUB | 💓 Heartbeat | agentes=0 señales=0 bytes=0 pendientes=0 uptime=30s
```

### 2. (Optional) Launch the monitoring dashboard

```bash
neural-hub --dashboard-port 8080
```

Now visit [http://127.0.0.1:8080](http://127.0.0.1:8080) to see real‑time metrics, connected agents, synapse strengths, and a live signal stream.

### 3. Connect agents

Agents must use the `WSNeuralAgent` classes from `neural-protocol`. Example:

```python
from neural_protocol.agents.support_ws import WSSupportAgent
from neural_protocol.agents.sales_ws import WSSalesAgent
from neural_protocol.core.signal import NeuralSignalType
import asyncio

async def main():
    support = WSSupportAgent()
    sales = WSSalesAgent()
    
    await support.start()
    await sales.start()
    
    # Send a signal to "sales" – the hub will round‑robin if multiple sales agents exist
    await support.transmit("ventas", NeuralSignalType.NOREPINEPHRINE, {"task": "new_lead"})
    
    await asyncio.sleep(5)
    await support.stop()
    await sales.stop()

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

## 🌐 Federación entre Hubs (Fase 1)

Ahora puedes conectar múltiples hubs para permitir la comunicación entre agentes de diferentes dominios (por ejemplo, `ventas@empresa-b.com`).  
La Fase 1 proporciona:

- **Configuración estática** de hubs remotos mediante un archivo JSON.
- **Autenticación con token compartido** – cada hub valida al otro antes de aceptar mensajes.
- **Reenvío de señales** – cuando un agente envía un mensaje a `nombre@dominio`, el hub local lo reenvía al hub remoto correspondiente.
- **Conexiones persistentes** entre hubs con reconexión automática.
- **TTL y control de bucles** – las señales incluyen un contador TTL para evitar bucles infinitos.

### Configuración de hubs remotos

Cada hub puede definir sus vecinos en un archivo JSON. Ejemplo `remotes.json`:

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

Luego inicia el hub con el parámetro `--remote-hubs`:

```bash
neural-hub --port 8765 --domain empresa-a.com --remote-hubs remotes.json
```

El parámetro `--domain` establece la identidad del hub (debe coincidir con la clave que otros hubs usan en sus configuraciones).

### Uso desde los agentes

Los agentes pueden enviar mensajes a destinos remotos usando la notación `nombre@dominio`. Ejemplo:

```python
await agente.transmit("vendedor@empresa-b.com", NeuralSignalType.NOREPINEPHRINE, {...})
```

El hub local se encarga de todo el enrutamiento.

---

## Advanced Usage

### Dashboard API Endpoint

The dashboard serves a simple REST API at `/api/status`. You can query it directly:

```bash
curl http://127.0.0.1:8080/api/status
```

It returns a JSON with current hub state (agents, synapses, stats). This is what the frontend uses to update the display.

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
agent = WSSupportAgent(use_ssl=True)  # or pass an SSL context
```

The hub will now use `wss://`. Agents accept self‑signed certificates by default (for development) – override by providing a custom `ssl_context`.

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

No tracebacks, no hanging processes.

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
                                                  │  Federation
                                           ┌──────▼──────┐
                                           │  Remote Hub │
                                           │ (empresa-b) │
                                           └─────────────┘
```

- All communication is bidirectional WebSocket (binary frames for signals, JSON for control).
- The hub never initiates messages – it only reacts to incoming data.
- Synapses are stored as `"source_hash:target_hash"` keys with floating‑point strength.
- The pending queue is per‑target‑hash and survives hub restarts (thanks to SQLite).
- The optional dashboard runs on a separate HTTP port, serving a single‑page application and a REST API.
- Federation adds persistent connections between hubs, with automatic reconnection and token authentication.

---

## Robustness & Performance

- **Automatic reconnection** – both agents and hub‑to‑hub connections use exponential backoff.
- **Message persistence** – signals for offline agents are stored in SQLite and delivered on reconnection.
- **TTL expiration** – old pending messages are automatically purged.
- **Heartbeat monitoring** – hubs log regular status updates; dashboard shows live metrics.
- **Throughput**: A single hub handles thousands of signals per second. Federation adds minimal overhead (JSON wrapping of forwarded signals).
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
- **Federation basics** (hub registration, forwarding).

---

## Integrating with Your Agents

Your agents should inherit from `neural_protocol.agent.base_ws.WSNeuralAgent` (or use the pre‑built `WSSupportAgent`, `WSSalesAgent`, `WSBillingAgent`).  
The only requirement is to call `await agent.start()` and implement `handle_signal`.

To use federation, simply pass the `domain` parameter when creating the agent:

```python
agent = MyAgent(agent_id="comprador", domain="empresa-a.com", hub_host="localhost", hub_port=8765)
```

The agent will automatically include its domain during registration, allowing the hub to route replies correctly.

---

## Roadmap

### ✅ Fase 1: Conexión básica entre hubs (completada)
- Configuración manual de hubs remotos.
- Conexión persistente con reconexión automática.
- Autenticación mediante token compartido.
- Reenvío de señales con TTL.

### 🔄 Fase 2: Descubrimiento dinámico y presencia (próximo)
- Intercambio de listas de agentes entre hubs (`HUB_PEER_UPDATE`).
- Enrutamiento optimizado (el hub sabe de antemano si un destino remoto existe).
- Heartbeats entre hubs y detección de fallos mejorada.

### ⏳ Fase 3: Alta disponibilidad y balanceo
- Múltiples hubs por dominio (clúster).
- Resolución de conflictos de nombres.
- Sincronización de estado entre réplicas.

Contributions and ideas are welcome!

---

## License

MIT © 2026 Firecode16

---

**Built for agents that think together.**  
Use with [neural‑protocol](https://github.com/firecode16/neural-protocol) to create resilient, self‑learning multi‑agent systems.
