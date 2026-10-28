"""The supported economy process entry point."""
from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Run WideMind's trusted economy service")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default loopback)")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    from .config import Settings
    try:
        Settings.from_env()
    except ValueError as exc:
        parser.exit(2, f"Economy configuration error: {exc}\n")
    import uvicorn
    uvicorn.run("widemind.economy.app:create_app", factory=True, host=args.host, port=args.port,
                access_log=False)


if __name__ == "__main__":
    main()
