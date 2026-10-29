"""WSGI integration boundary. Only redacted activity is public; no direct tool route."""
import hmac
import json
import re
from .store import encode
from .policy import Policy


class API:
    def __init__(self, factory, *, operator_token):
        if not isinstance(operator_token, str) or len(operator_token) < 32:
            raise ValueError("a separate operator bearer token of at least 32 characters is required")
        self.factory, self.token = factory, operator_token

    def __call__(self, environ, start_response):
        status, document, business = "200 OK", {}, None
        try:
            method = environ.get("REQUEST_METHOD", "GET")
            path = environ.get("PATH_INFO", "")
            public = method == "GET" and path == "/api/v1/activity"
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
            if public:
                document = {"events": business.public_activity(), "mode": business.mode}
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
                business.store.close()
        return self._respond(start_response, status, document)

    @staticmethod
    def _respond(start_response, status, document):
        data = encode(document).encode()
        start_response(status, [("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(data))), ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff")])
        return [data]
