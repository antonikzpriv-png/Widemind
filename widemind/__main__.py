import argparse
import importlib
import os
from wsgiref.simple_server import make_server
from .business.api import API
from .business.store import Store, encode
from .worker import serve


def host_factory():
    spec = os.environ.get("WIDEMIND_HOST_FACTORY", "")
    if ":" not in spec:
        raise SystemExit("Configure WIDEMIND_HOST_FACTORY=your_package:business_factory (see docs/BUSINESS.md). No live adapters are bundled.")
    module, attribute = spec.split(":", 1)
    factory = getattr(importlib.import_module(module), attribute)
    def create():
        business = factory()
        if business.mode != "live":
            business.store.close()
            raise ValueError("host commands require live mode; use demo for fixtures")
        return business
    return create


def main():
    parser = argparse.ArgumentParser(description="WideMind business module")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="explicit offline fixture demonstration")
    demo.add_argument("--directory", required=True, help="private demo output directory")
    adopt = sub.add_parser("adopt-run", help="classify a reviewed pre-audit database run; no external effects")
    adopt.add_argument("--database", required=True)
    adopt.add_argument("--run-id", required=True)
    adopt.add_argument("--mode", required=True, choices=("live", "demo", "test"))
    adopt.add_argument("--evidence-reference", required=True)
    worker = sub.add_parser("worker", help="host-injected persistent worker")
    worker.add_argument("--once", action="store_true")
    api = sub.add_parser("api", help="loopback API; TLS/authenticated reverse proxy required in deployment")
    api.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if args.command == "adopt-run":
        store = Store(args.database)
        try:
            store.adopt_run_mode(args.run_id.strip(), args.mode, args.evidence_reference)
        finally:
            store.close()
        print(encode({"run_id": args.run_id.strip(), "mode": args.mode, "adopted": True}))
        return
    if args.command == "demo":
        from .demo import demonstrate
        print(encode(demonstrate(args.directory)))
        return
    factory = host_factory()
    if args.command == "worker":
        business = factory()
        path = business.store.db.execute("PRAGMA database_list").fetchone()[2]
        try:
            serve(business, database_path=path, once=args.once)
        finally:
            business.store.close()
    else:
        app = API(factory, operator_token=os.environ.get("WIDEMIND_OPERATOR_TOKEN"))
        with make_server("127.0.0.1", args.port, app) as server:
            server.serve_forever()


if __name__ == "__main__":
    main()
