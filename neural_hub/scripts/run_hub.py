#!/usr/bin/env python3
"""
Entry point para ejecutar el NeuralHub.
Uso: python -m neural_hub.scripts.run_hub [--port PORT] [--host HOST] [--ssl] [--cert CERT] [--key KEY] [--dashboard-port PORT]
"""
import argparse
import asyncio
import ssl
import sys
from neural_hub.server import NeuralHub

async def main():
    parser = argparse.ArgumentParser(description="NeuralHub server")
    parser.add_argument("--port", type=int, default=8765, help="Puerto para WebSocket")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host a escuchar")
    parser.add_argument("--ssl", action="store_true", help="Habilitar WSS (WebSocket Secure)")
    parser.add_argument("--cert", type=str, default="cert.pem", help="Archivo de certificado SSL (PEM)")
    parser.add_argument("--key", type=str, default="key.pem", help="Archivo de clave SSL (PEM)")
    parser.add_argument("--dashboard-port", type=int, default=None, help="Puerto para el dashboard web (opcional)")
    args = parser.parse_args()

    ssl_context = None
    if args.ssl:
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(args.cert, args.key)

    hub = NeuralHub(host=args.host, port=args.port, db_path=f"neural_hub_{args.port}.db")
    proto = "wss" if args.ssl else "ws"
    print(f"Starting NeuralHub on {proto}://{args.host}:{args.port}")
    if args.dashboard_port:
        print(f"Dashboard will be available at http://{args.host}:{args.dashboard_port}")

    try:
        await hub.start(ssl_context, dashboard_port=args.dashboard_port)
        print("Hub iniciado, esperando conexiones... (presiona Ctrl+C para detener)")
        # Mantener el hub corriendo
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n🛑 Interrupción recibida, cerrando hub...")
    except Exception as e:
        print(f"❌ Error inesperado: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await hub.stop()
        print("👋 Hub detenido correctamente.")
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())