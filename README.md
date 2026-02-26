# 🧠 NeuralHub — Servidor Central para NeuralProtocol

NeuralHub es el servidor central que actúa como "tálamo" de la red NeuralProtocol. Todos los agentes se conectan a él mediante WebSocket, y el hub se encarga de enrutar las señales, mantener el registro de agentes, la plasticidad sináptica y una cola para mensajes dirigidos a agentes offline.

## Instalación

```bash
pip install neural-hub