import argparse
import importlib
import os
from wsgiref.simple_server import make_server
from .business.api import API
from .business.store import Store, encode
from .worker import serve


def host_factory():
    spec = os.environ.get("WIDEMIND_HOST_FACTORY", "widemind.host:business_factory")
    if ":" not in spec:
        raise SystemExit("Configure WIDEMIND_HOST_FACTORY=your_package:business_factory (see docs/BUSINESS.md). See the production host setup requirements.")
    module, attribute = spec.split(":", 1)
    factory = getattr(importlib.import_module(module), attribute)
    def create():
        business = factory()
        if business.mode != "live" and not (business.mode == "test" and getattr(business, "execution_profile", None) == "provider-test" and getattr(business, "uses_fixtures", True) is False):
            business.close()
            raise ValueError("host commands require a real live/provider-test profile; use demo for fixtures")
        return business
    return create


def main():
    parser = argparse.ArgumentParser(description="WideMind business module")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="explicit offline fixture demonstration")
    demo.add_argument("--directory", required=True, help="private demo output directory")
    preview = sub.add_parser("serve-demo", help="explicit offline website preview with a separate persistent worker")
    preview.add_argument("--directory", required=True)
    preview.add_argument("--port", type=int, default=8080)
    preview.add_argument("--seed", action="store_true", help="run the labelled fixture workflow once before serving")
    preview.add_argument("--token-file", required=True, help="owner-only file containing the demo operator token")
    adopt = sub.add_parser("adopt-run", help="classify a reviewed pre-audit database run; no external effects")
    adopt.add_argument("--database", required=True)
    adopt.add_argument("--run-id", required=True)
    adopt.add_argument("--mode", required=True, choices=("live", "demo", "test"))
    adopt.add_argument("--evidence-reference", required=True)
    initialize = sub.add_parser("initialize", help="explicit production/test-provider run setup; never creates a customer payment")
    initialize.add_argument("--evidence-reference", required=True)
    sub.add_parser("check-config", help="report safe local provider readiness; makes no provider calls")
    sync = sub.add_parser("sync-payments", help="enqueue bounded verified-payment reconciliation; no customer money is invented")
    sync.add_argument("--request-id", required=True)
    inbox = sub.add_parser("sync-inbox", help="enqueue a configured Gmail sync through the gateway")
    inbox.add_argument("--request-id", required=True)
    inbox.add_argument("--cursor")
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
    if args.command in {"initialize", "check-config"}:
        from .host import initialize_live, readiness
        if args.command == "initialize":
            print(encode(initialize_live(evidence_reference=args.evidence_reference)))
        else:
            print(encode(readiness()))
        return
    if args.command == "serve-demo":
        from .preview import serve_demo
        serve_demo(args.directory, port=args.port, token_file=args.token_file, seed=args.seed)
        return
    factory = host_factory()
    if args.command in {"sync-payments", "sync-inbox"}:
        business = factory()
        try:
            event_id = business.notify(source="operator-cli", external_id=args.request_id,
                kind="payments.reconcile" if args.command == "sync-payments" else "gmail.sync",
                payload={} if args.command == "sync-payments" else {"cursor": args.cursor})
            print(encode({"event_id": event_id, "queued": True}))
        finally:
            business.close()
        return
    if args.command == "worker":
        business = factory()
        path = business.store.db.execute("PRAGMA database_list").fetchone()[2]
        try:
            serve(business, database_path=path, once=args.once)
        finally:
            business.close()
    else:
        app = API(factory, operator_token=os.environ.get("WIDEMIND_OPERATOR_TOKEN"),
                  contact_email=os.environ.get("WIDEMIND_GMAIL_ADDRESS"))
        with make_server("127.0.0.1", args.port, app) as server:
            server.serve_forever()


if __name__ == "__main__":
    main()
