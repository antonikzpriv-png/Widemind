"""Private operator CLI. No credentials or customer data are public projections."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from .gateway import Gateway
from .tools.contracts import ToolError


def main(argv=None):
    parser = argparse.ArgumentParser(description="WideMind private tools gateway")
    parser.add_argument("--config", required=True)
    commands = parser.add_subparsers(dest="command",required=True)
    commands.add_parser("status")
    init = commands.add_parser("init")
    init.add_argument("--run-id",required=True)
    secret = commands.add_parser("secret")
    secret.add_argument("operation",choices=["set"])
    secret.add_argument("name")
    secret.add_argument("--from-env")
    execute = commands.add_parser("execute")
    execute.add_argument("tool")
    execute.add_argument("--run-id",required=True)
    execute.add_argument("--action-id",required=True)
    execute.add_argument("--job-id")
    execute.add_argument("--input-file",required=True)
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("action_id")
    cancel = commands.add_parser("cancel")
    cancel.add_argument("action_id")
    for name in ("pause","resume"):
        pause = commands.add_parser(name)
        pause.add_argument("run_id")
    balance = commands.add_parser("balance")
    balance.add_argument("run_id")
    args = parser.parse_args(argv)
    try:
        gateway = Gateway.from_config(args.config)
        if args.command == "status":
            result = gateway.status()
        elif args.command == "init":
            result = gateway.store.create_run(args.run_id)
        elif args.command == "secret":
            if args.from_env:
                value = os.environ.get(args.from_env)
                if value is None:
                    raise ToolError("unavailable", "The named environment variable is not set.")
            elif sys.stdin.isatty():
                value = getpass.getpass("Secret value (hidden): ")
            else:
                raise ToolError("invalid_input", "Use an interactive hidden prompt or --from-env with a private environment variable.")
            gateway.secrets.set(args.name,value)
            result = {"stored":args.name,"visibility":"private"}
        elif args.command == "execute":
            path = Path(args.input_file)
            with path.open("rb") as stream:
                data = stream.read(8_000_001)
            if len(data) > 8_000_000:
                raise ToolError("invalid_input", "Request file exceeds 8 MB.")
            inputs = json.loads(data)
            result = gateway.execute(args.tool,inputs,run_id=args.run_id,action_id=args.action_id,job_id=args.job_id)
        elif args.command == "reconcile":
            result = gateway.reconcile(args.action_id)
        elif args.command == "balance":
            result = gateway.store.balance(args.run_id)
        elif args.command == "cancel":
            gateway.store.cancel(args.action_id)
            result = gateway.store.receipt(args.action_id)
        else:
            gateway.store.pause(args.run_id,args.command=="pause")
            result = gateway.store.balance(args.run_id)
        print(json.dumps(result,sort_keys=True,allow_nan=False))
        return 0 if result.get("status") not in {"failed","unavailable","cancelled","uncertain"} else 1
    except ToolError as exc:
        print(json.dumps({"error":exc.code,"message":exc.message}),file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('{"error":"cancelled","message":"Operator cancelled the command; inspect its receipt before retrying."}',file=sys.stderr)
        return 130
    except Exception:
        print('{"error":"configuration","message":"Check private config, file permissions and input JSON; details were not printed to avoid exposing private data."}',file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
