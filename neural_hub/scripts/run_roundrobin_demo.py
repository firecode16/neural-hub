#!/usr/bin/env python3
"""
Prueba de round-robin automático en el hub.
Múltiples agentes de ventas reciben señales de soporte distribuidas equitativamente.
"""
import asyncio
import argparse
import ssl
from neural_hub.server import NeuralHub
from neural_protocol.agents.support_ws import WSSupportAgent
from neural_protocol.agents.sales_ws import WSSalesAgent
from neural_protocol.core.signal import NeuralSignalType

async def main():
    parser = argparse.ArgumentParser(description="Round-robin demo")
    parser.add_argument("--ssl", action="store_true", help="Use WSS")
    parser.add_argument("--cert", type=str, default="cert.pem", help="SSL certificate (for client, optional)")
    parser.add_argument("--key", type=str, default="key.pem", help="SSL key (for client, optional)")
    args = parser.parse_args()

    # Configurar SSL para el hub si se pide
    ssl_context = None
    if args.ssl:
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(args.cert, args.key)

    # Iniciar hub
    hub = NeuralHub(host="127.0.0.1", port=8765, db_path="test_roundrobin.db")
    await hub.start(ssl_context)
    print(f"🧠 Hub iniciado en {'wss' if args.ssl else 'ws'}://127.0.0.1:8765")

    # Conectar soporte (con SSL si aplica)
    support = WSSupportAgent(use_ssl=args.ssl)
    await support.start()
    print("✅ Soporte conectado")

    # Conectar 3 agentes de ventas (con SSL si aplica)
    sales_agents = []
    for i in range(3):
        agent = WSSalesAgent(use_ssl=args.ssl)
        await agent.start()
        sales_agents.append(agent)
        print(f"✅ Ventas-{i+1} conectado")

    await asyncio.sleep(1)

    # Enviar 6 señales desde soporte al nombre "ventas"
    print("\n📤 Enviando 6 señales a 'ventas' (el hub hará round-robin):")
    for i in range(6):
        await support.transmit(
            "ventas",  # nombre lógico, no hash
            NeuralSignalType.NOREPINEPHRINE,
            {"ticket_id": f"TKT-{i+1:03d}", "msg": f"Señal {i+1}"}
        )
        await asyncio.sleep(0.2)

    # Esperar un poco para que se procesen
    await asyncio.sleep(1)

    # Mostrar estadísticas
    print("\n📊 Señales recibidas por cada agente de ventas:")
    for idx, agent in enumerate(sales_agents):
        print(f"  Ventas-{idx+1} recibió {len(agent._memory)} señales")
        for j, sig in enumerate(agent._memory):
            preview = str(sig.payload)[:60]
            print(f"    {j+1}. {sig.signal_type.name} - {preview}")

    # Cerrar
    for agent in sales_agents:
        await agent.stop()
    await support.stop()
    await hub.stop()

if __name__ == "__main__":
    asyncio.run(main())