"""Local, explicitly offline website + independently scheduled fixture worker."""
import multiprocessing
import os
from pathlib import Path
import secrets
from wsgiref.simple_server import make_server, WSGIRequestHandler
from .business.api import API
from .demo import demonstrate, make_demo
from .worker import serve


class QuietHandler(WSGIRequestHandler):
    # Customer/command URLs and bearer headers do not belong in demo access logs.
    def log_message(self, format, *args):
        pass


def _worker(directory):
    business, _ = make_demo(directory)
    try:
        serve(business, database_path=Path(directory) / "business.sqlite3")
    finally:
        business.close()


def serve_demo(directory, *, port, token_file, seed=False):
    directory = Path(directory).resolve()
    token_path = Path(token_file).resolve()
    if seed:
        demonstrate(directory)
    else:
        business, _ = make_demo(directory)
        business.close()
    token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if token_path.exists():
        token = token_path.read_text().strip()
        if len(token) < 32:
            raise ValueError("existing demo token file must contain at least 32 characters")
        os.chmod(token_path, 0o600)
    else:
        token = secrets.token_urlsafe(32)
        fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(token)
    def factory():
        business, _ = make_demo(directory)
        return business
    app = API(factory, operator_token=token, contact_email="customer@example.invalid")
    context = multiprocessing.get_context("spawn")
    worker = context.Process(target=_worker, args=(str(directory),), name="widemind-demo-worker")
    worker.start()
    try:
        print(f"Offline demo UI on loopback port {port}; operator token is in the configured private file. No live effects.", flush=True)
        with make_server("127.0.0.1", port, app, handler_class=QuietHandler) as server:
            server.serve_forever()
    finally:
        # Only the child this preview created; persisted effects survive restart.
        if worker.is_alive():
            worker.terminate()
        worker.join(timeout=5)
