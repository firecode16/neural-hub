#!/usr/bin/env python3
"""
Demo de resiliencia: un agente se desconecta, se envían señales, y al reconectarlas recibe.
"""
import asyncio
import sys
from neural_hub.server import NeuralHub
from neural_protocol.agents.support_ws import WSSupportAgent
from neural_protocol.agents.sales_ws import WSSalesAgent
from neural_protocol.core.signal import NeuralSignalType

async def main():
    # Iniciar hub en un puerto específico
    hub = NeuralHub(host="127.0.0.1", port=8766)
    await hub.start()
    print("🧠 Hub iniciado en puerto 8766")

    # Conectar agentes
    support = WSSupportAgent(hub_host="127.0.0.1", hub_port=8766)
    sales   = WSSalesAgent(hub_host="127.0.0.1", hub_port=8766)

    await support.start()
    await sales.start()
    print("✅ Agentes conectados")

    # Enviar señal normal
    print("\n📤 Soporte envía señal a Ventas (online)")
    await support.transmit(
        "ventas",
        NeuralSignalType.NOREPINEPHRINE,
        {"ticket_id": "TKT-001", "test": "normal"}
    )
    await asyncio.sleep(0.2)

    # Simular caída de Ventas
    print("\n⚡ Ventas se desconecta...")
    await sales._conn.close()
    await asyncio.sleep(1)

    # Enviar señal mientras está caído
    print("\n📤 Soporte envía otra señal (Ventas offline) → se encola en el hub")
    await support.transmit(
        "ventas",
        NeuralSignalType.NOREPINEPHRINE,
        {"ticket_id": "TKT-002", "test": "offline"}
    )
    await asyncio.sleep(0.2)

    # Reconectar Ventas
    print("\n🔄 Ventas se reconecta...")
    # El agente WSSalesAgent ya tiene reconexión automática; forzamos reintento cerrando la conexión anterior
    # (ya está cerrada, el loop de reconexión debería actuar)
    await asyncio.sleep(5)  # esperar a que reconecte (backoff de 1s, luego 2s...)

    # Verificar que recibió las señales encoladas
    print(f"\n📊 Señales recibidas por Ventas: {len(sales._memory)}")
    for i, sig in enumerate(sales._memory):
        print(f"  {i+1}. {sig.signal_type.name} - {sig.payload}")

    # Cerrar todo
    await support.stop()
    await sales.stop()
    await hub.stop()

if __name__ == "__main__":
    asyncio.run(main())