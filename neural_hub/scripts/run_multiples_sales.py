#!/usr/bin/env python3
"""
Prueba de round-robin: múltiples agentes de ventas.
El hub distribuye las señales de soporte entre ellos de forma circular.
Ahora con persistencia y round-robin mejorado.
"""
import asyncio
import sys
from neural_hub.server import NeuralHub
from neural_protocol.agents.support_ws import WSSupportAgent
from neural_protocol.agents.sales_ws import WSSalesAgent
from neural_protocol.core.signal import NeuralSignalType

async def main():
    # Iniciar hub (modo integrado para prueba)
    hub = NeuralHub(host="127.0.0.1", port=8765, db_path="test_roundrobin.db")
    await hub.start()
    print("🧠 Hub iniciado en puerto 8765 con persistencia")

    # Conectar un agente de soporte
    support = WSSupportAgent()
    await support.start()
    print("✅ Soporte conectado")

    # Conectar múltiples agentes de ventas (3)
    sales_agents = []
    for i in range(3):
        agent = WSSalesAgent()
        await agent.start()
        sales_agents.append(agent)
        print(f"✅ Ventas-{i+1} conectado (hash: {agent.identity.neural_hash[:8]})")

    # Esperar un momento para que se estabilicen
    await asyncio.sleep(1)

    # Ahora, en lugar de usar transmit directamente, vamos a obtener la lista de hashes de ventas
    # desde el hub y hacer round-robin manualmente.
    # Para ello, enviamos un mensaje de control al hub pidiendo los hashes de "ventas".
    # El hub ya tiene un nuevo comando "get_agents_by_name" implementado en server.py.
    # Pero como no modificamos el agente base, lo haremos directamente con una conexión temporal.

    from neural_protocol.transport.websocket import connect_websocket, ctrl_msg, parse_ctrl

    conn = await connect_websocket("127.0.0.1", 8765)
    await conn.send(ctrl_msg("get_agents_by_name", name="ventas"))
    resp = await conn.recv()
    ctrl = parse_ctrl(resp)
    ventas_hashes = ctrl.get("hashes", [])
    await conn.close()

    print(f"\n📋 Hashes de agentes 'ventas' según hub: {ventas_hashes}")

    if not ventas_hashes:
        print("No hay agentes de ventas, abortando.")
        return

    # Enviar varias señales desde soporte, eligiendo un hash cada vez en round-robin
    print("\n📤 Enviando 6 señales a 'ventas' (round-robin manual):")
    for i in range(6):
        # Seleccionar hash por round-robin
        idx = i % len(ventas_hashes)
        target_hash = ventas_hashes[idx]
        # Construir señal manualmente (sin usar transmit del agente, porque transmit resuelve nombre)
        from neural_protocol.core.signal import NeuralSignal
        signal = NeuralSignal(
            signal_type=NeuralSignalType.NOREPINEPHRINE,
            source=support.identity.neural_hash,
            target=target_hash,  # enviamos directamente al hash
            payload={"ticket_id": f"TKT-{i+1:03d}", "msg": f"Señal {i+1}"}
        )
        # Enviar usando el transporte del agente (support.transport)
        await support.transport.send(signal)
        print(f"  Señal {i+1} → ventas-{idx+1} ({target_hash[:8]})")
        await asyncio.sleep(0.2)

    # Mostrar cuántas señales recibió cada agente de ventas
    print("\n📊 Estadísticas de recepción por agente de ventas:")
    for idx, agent in enumerate(sales_agents):
        print(f"  Ventas-{idx+1} (#{agent.identity.neural_hash[:8]}) recibió {len(agent._memory)} señales")
        for j, sig in enumerate(agent._memory):
            print(f"    {j+1}. {sig.payload['msg']}")

    # Mantener abierto un momento para ver los logs
    await asyncio.sleep(3)

    # Cerrar todo
    for agent in sales_agents:
        await agent.stop()
    await support.stop()
    await hub.stop()

if __name__ == "__main__":
    asyncio.run(main())