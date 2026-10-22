import sys

from .config import ConfigError


def main() -> int:
    sys.stderr.reconfigure(encoding="utf-8")   # mensajes legibles sea cual sea la consola
    try:
        from .server import create_server
        server = create_server()
    except ConfigError as e:
        print(f"[netbeans-autopilot] Configuración no válida: {e}", file=sys.stderr)
        return 2
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
