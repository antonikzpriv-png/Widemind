"""WSGI integration boundary. Only redacted activity is public; no direct tool route."""
import hmac
import json
import re
from importlib.resources import files
from .store import encode
from .policy import Policy
from .views import console_view, public_view


class API:
    def __init__(self, factory, *, operator_token, contact_email=None):
        if not isinstance(operator_token, str) or len(operator_token) < 32:
            raise ValueError("a separate operator bearer token of at least 32 characters is required")
        self.factory, self.token = factory, operator_token
        self.contact_email = contact_email

    def __call__(self, environ, start_response):
        status, document, business = "200 OK", {}, None
        try:
            method = environ.get("REQUEST_METHOD", "GET")
            path = environ.get("PATH_INFO", "")
            if method == "GET" and (path in {"/", "/console", "/console/"} or path.startswith("/static/")):
                return self._static(start_response, path)
            public = method == "GET" and path in {"/api/v1/activity", "/api/v1/public"}
            if method == "POST" and path == "/api/v1/stripe/webhook":
                return self._webhook(environ, start_response)
            if not public:
                supplied = environ.get("HTTP_AUTHORIZATION", "")
                if not hmac.compare_digest(supplied, f"Bearer {self.token}"):
                    return self._respond(start_response, "401 Unauthorized", {"error": "authentication required"})
            if method not in {"GET", "POST"}:
                return self._respond(start_response, "405 Method Not Allowed", {"error": "unsupported method"})
            payload = {}
            if method == "POST":
                if environ.get("CONTENT_TYPE", "").split(";")[0] != "application/json":
                    raise ValueError("JSON required")
                length = int(environ.get("CONTENT_LENGTH") or "0")
                if not 1 <= length <= 65536:
                    raise ValueError("body must be 1–65536 bytes")
                body = environ["wsgi.input"].read(length)
                if len(body) != length:
                    raise ValueError("incomplete request body")
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("JSON object required")
            business = self.factory()
            if method == "GET" and path == "/api/v1/public":
                document = public_view(business, self.contact_email)
            elif public:
                document = {"events": business.public_activity(), "mode": business.mode}
            elif method == "GET" and path == "/api/v1/console":
                document = console_view(business)
            elif method == "POST" and path == "/api/v1/runtime":
                document = business.control_runtime(**payload)
            elif method == "GET" and path == "/api/v1/memory":
                document = {"memories": business.recall()}
            elif method == "POST" and path == "/api/v1/resolve-hold":
                business.resolve_hold(**payload)
                document = {"recorded": True}
            elif method == "GET" and path == "/api/v1/business":
                document = business.diagnostics()
            elif method == "POST" and path == "/api/v1/opportunities":
                document = {"opportunity_id": business.receive(**payload)}
                status = "202 Accepted"
            elif method == "POST" and path == "/api/v1/events":
                # Authenticated adapters send notification hints. Payment amounts
                # cannot be set here; the worker always queries economy.
                document = {"event_id": business.notify(**payload)}
                status = "202 Accepted"
            elif method == "POST" and path == "/api/v1/accept":
                document = {"order_id": business.accept(**payload)}
            elif method == "POST" and path == "/api/v1/policy":
                business.configure(Policy(**payload))
                document = {"saved": True}
            elif method == "POST" and path == "/api/v1/reconsider":
                business.reconsider(**payload)
                document = {"scheduled": True}
            elif method == "POST" and path == "/api/v1/opt-out":
                business.opt_out(**payload)
                document = {"recorded": True}
            elif method == "POST" and re.fullmatch(r"/api/v1/effects/[a-f0-9]{40}/retry", path):
                document = {"action_id": business.retry_effect(path.split("/")[-2], **payload)}
            elif method == "GET" and re.fullmatch(r"/api/v1/orders/[a-f0-9]{40}", path):
                document = business.order(path.split("/")[-1])
            else:
                status, document = "404 Not Found", {"error": "not found"}
        except (ValueError, TypeError, KeyError, UnicodeError):
            status, document = "400 Bad Request", {"error": "invalid request or unavailable transition"}
        except Exception:
            # Provider errors must never put credentials/private contents into HTTP logs.
            status, document = "503 Service Unavailable", {"error": "business dependency unavailable"}
        finally:
            if business is not None:
                business.close()
        return self._respond(start_response, status, document)

    def _webhook(self, environ, start_response):
        business = None
        try:
            length = int(environ.get("CONTENT_LENGTH") or "0")
            if not 1 <= length <= 1_000_000:
                raise ValueError("invalid webhook body length")
            raw = environ["wsgi.input"].read(length)
            if len(raw) != length:
                raise ValueError("incomplete webhook")
            signature = environ.get("HTTP_STRIPE_SIGNATURE", "")
            business = self.factory()
            handler = getattr(business.economy, "handle_webhook", None)
            if business.mode != "live" or not callable(handler):
                raise ValueError("verified webhook handler not configured")
            jobs = handler(raw, signature)
            event = json.loads(raw)
            external = str(event["id"])
            for job in jobs:
                # Money stays in economy; business receives hints only.
                if business.store.one("SELECT id FROM orders WHERE id=? AND run_id=?", (job, business.run_id)):
                    business.notify(source="stripe", external_id=external + ":" + job,
                                    kind="payment.changed", payload={"id": job})
            return self._respond(start_response, "200 OK", {"received": True})
        except (ValueError, TypeError, KeyError, UnicodeError):
            return self._respond(start_response, "400 Bad Request", {"error": "invalid webhook"})
        except Exception:
            return self._respond(start_response, "503 Service Unavailable", {"error": "webhook dependency unavailable"})
        finally:
            if business is not None:
                business.close()

    @staticmethod
    def _static(start_response, path):
        name = {"/": "index.html", "/console": "console.html", "/console/": "console.html"}.get(path)
        if name is None:
            name = path.removeprefix("/static/")
            if not re.fullmatch(r"[A-Za-z0-9_-]+\.(js|css|svg|woff2)", name):
                return API._respond(start_response, "404 Not Found", {"error": "not found"})
        try:
            payload = files("widemind.web").joinpath("static", name).read_bytes()
        except (FileNotFoundError, ModuleNotFoundError, IsADirectoryError):
            return API._respond(start_response, "404 Not Found", {"error": "not found"})
        media = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
                 "svg": "image/svg+xml", "woff2": "font/woff2"}[name.rsplit(".", 1)[1]]
        start_response("200 OK", [("Content-Type", media), ("Content-Length", str(len(payload))),
            ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; font-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")])
        return [payload]

    @staticmethod
    def _respond(start_response, status, document):
        data = encode(document).encode()
        start_response(status, [("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(data))), ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff")])
        return [data]
