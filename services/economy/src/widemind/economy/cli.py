"""Supported economy HTTP, reconciliation-worker and recovery entry points."""
from __future__ import annotations

import argparse
import json
import signal
import threading


def _integer_between(lower: int, upper: int):
    def parse(value: str) -> int:
        try:
            result = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"Expected an integer between {lower} and {upper}") from None
        if not lower <= result <= upper:
            raise argparse.ArgumentTypeError(f"Expected an integer between {lower} and {upper}")
        return result
    return parse


def main() -> None:
    parser = argparse.ArgumentParser(description="Run WideMind's trusted economy service")
    # Preserve the original no-subcommand server invocation.
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default loopback)")
    parser.add_argument("--port", type=_integer_between(1, 65535), default=8080)
    commands = parser.add_subparsers(dest="command")
    worker_command = commands.add_parser("worker", help="Run durable, model-free Stripe reconciliation")
    worker_command.add_argument("--once", action="store_true", help="Scan pending payments and process one bounded batch, then exit")
    worker_command.add_argument("--run-id", help="Run to scan (defaults to WIDEMIND_AGENT_RUN_ID)")
    worker_command.add_argument("--batch-size", type=_integer_between(1, 100), default=10)
    worker_command.add_argument("--poll-seconds", type=_integer_between(1, 3600), default=5)
    worker_command.add_argument("--scan-interval", type=_integer_between(60, 86400), default=300,
                                help="Minimum seconds between successful pending-payment polls")
    recovery_command = commands.add_parser("recover-checkout", help="Read Stripe to recover an uncertain existing checkout; never creates a new session")
    recovery_command.add_argument("request_id")
    args = parser.parse_args()
    from .config import Settings
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        parser.exit(2, f"Economy configuration error: {exc}\n")
    if args.command is None:
        import uvicorn
        uvicorn.run("widemind.economy.app:create_app", factory=True, host=args.host, port=args.port,
                    access_log=False)
        return

    from .service import EconomyError, EconomyService
    adapter = None
    try:
        if not settings.payments_enabled:
            parser.exit(2, "Economy configuration error: configure the Stripe account, mode, credentials and redirects for this command\n")
        service = EconomyService(settings.database_path)
        adapter = settings.create_payments(service)
        if args.command == "recover-checkout":
            print(json.dumps(adapter.recover_checkout(args.request_id), sort_keys=True))
            return

        from .reconciliation import ReconciliationQueue, ReconciliationWorker
        queue = ReconciliationQueue(service)
        worker = ReconciliationWorker(queue, adapter)
        run_id = args.run_id or settings.agent_run_id
        # Resolve the run locally before entering a persistent process. Waiting
        # and idle polls never invoke a model or initiate a payment/paid tool.
        service.balance(run_id)
        stop = threading.Event()
        previous_handlers = {}
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, lambda _signum, _frame: stop.set())
        try:
            while not stop.is_set():
                worker.schedule_due_payments(run_id, interval_seconds=args.scan_interval, limit=args.batch_size)
                result = worker.run_once(limit=args.batch_size)
                print(json.dumps(result, sort_keys=True), flush=True)
                if args.once:
                    break
                stop.wait(args.poll_seconds)
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
    except EconomyError as exc:
        parser.exit(1, f"Economy operation failed ({exc.code}): {exc}\n")
    finally:
        if adapter is not None:
            adapter.close()


if __name__ == "__main__":
    main()
