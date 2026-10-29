"""Private artifact/version views; caller must first prove request/order ownership."""
import hashlib
import hmac
import json
from .services import text
from .store import identity, encode


def published_version(business, order, version=None):
    if version is not None and (type(version) is not int or version < 1):
        raise ValueError("invalid artifact version")
    row = business.store.one("""SELECT * FROM artifact_versions WHERE run_id=? AND order_id=?
        AND published_at IS NOT NULL AND (? IS NULL OR version=?) ORDER BY version DESC LIMIT 1""",
        (business.run_id, order["id"], version, version))
    if row is None:raise ValueError("published artifact version is unavailable")
    return row


def read_delivery(business, order, payload):
    row = published_version(business, order, payload.get("version"))
    artifact = json.loads(row["artifact"])
    filename = payload.get("filename", artifact["filename"])
    files = [f for f in artifact["files"] if f["filename"] == filename]
    if len(files) != 1:raise ValueError("artifact filename unavailable")
    file = files[0]
    return {"filename": file["filename"], "mediaType": file["media_type"], "encoding": file["encoding"],
            "content": file["content"], "sha256": file["sha256"], "version": row["version"]}


def read_manifest(business, order, payload):
    return json.loads(published_version(business, order, payload.get("version"))["manifest"])


def order_details(business, order):
    job = business.store.one("SELECT * FROM creative_jobs WHERE order_id=? AND run_id=?", (order["id"], business.run_id))
    versions = business.store.all("SELECT version,manifest,created_at FROM artifact_versions WHERE order_id=? AND run_id=? AND published_at IS NOT NULL ORDER BY version", (order["id"], business.run_id))
    if not job:return {}
    scope = json.loads(order["scope"])
    revision = job["revision"]
    available = bool(job["state"] == "complete" and job["revision_deadline"] and
        job["revision_deadline"] > business.clock() and revision < scope["max_revisions"] and order["status"] in {"delivered", "completed"} and business._active())
    def version_view(row):
        manifest = json.loads(row["manifest"])
        return {"version": row["version"], "createdAt": row["created_at"], "manifestAvailable": True,
            "files": [{"filename": f["filename"], "mediaType": f["media_type"], "role": f["role"], "sha256": f["sha256"], "size": f["size"]} for f in manifest["files"]],
            "checks": manifest["checks"]}
    return {"artifactVersions": [version_view(v) for v in versions],
        "revision": {"used": revision, "limit": scope["max_revisions"], "remaining": max(0, scope["max_revisions"] - revision),
                     "available": available, "deadline": job["revision_deadline"]},
        "fulfillment": {"stage": job["state"], "status": job["state"], "attempt": job["attempt"] + 1,
                        "errors": json.loads(job["errors"]), "scopeFrozen": True, "budgetMicros": str(order["cost_micro_usd"])}}


def scope_change(business, request, order, payload):
    request_id = text(payload.get("requestId"), "scope-change request ID", 200)
    feedback = text(payload.get("feedback"), "scope-change explanation", 2000)
    scope = payload.get("scope")
    service = business.services.get(order["service"])
    if not getattr(service, "enhanced", False):raise ValueError("use a new request for template scope changes")
    service.validate_scope(scope)
    key = identity(business.run_id, order["id"], request_id)
    document = encode({"kind": "scope_change", "feedback": feedback, "scope": scope})
    previous = business.store.one("SELECT * FROM customer_change_requests WHERE id=?", (key,))
    if previous and previous["request_json"] != document:raise ValueError("change request ID reused with different contents")
    # Derive a separate high-entropy capability without retaining raw tokens.
    token = hmac.new(payload["token"].encode(), ("scope-change:" + key).encode(), hashlib.sha256).hexdigest()
    hashed = hashlib.sha256(token.encode()).hexdigest()
    external = "scope-change-" + key
    new_key = identity(business.run_id, external)
    opportunity = identity(business.run_id, "customer-portal", external)
    with business.store.transaction():
        old = business.store.one("SELECT * FROM host_requests WHERE request_id=?", (new_key,))
        if old and (old["token_hash"] != hashed or old["revoked_at"]):raise ValueError("scope-change access revoked or conflicting")
        business.store.db.execute("INSERT OR IGNORE INTO host_requests VALUES (?,?,?,?,?,?,NULL)",
            (new_key, business.run_id, opportunity, request["email"], hashed, business.clock()))
    actual = business.receive(source="customer-portal", external_id=external, service=order["service"],
        scope=scope, customer_email=request["email"])
    if actual != opportunity:raise ValueError("scope-change identity mismatch")
    result = {"opportunityId": opportunity, "requestId": external, "requiresNewAcceptance": True}
    with business.store.transaction():
        current = business.store.one("SELECT request_json FROM customer_change_requests WHERE id=?", (key,))
        if current and current["request_json"] != document:
            raise ValueError("change request ID reused concurrently with different contents")
        business.store.db.execute("INSERT OR IGNORE INTO customer_change_requests VALUES (?,?,?,?,?,?)",
            (key, business.run_id, order["id"], document, encode(result), business.clock()))
        business.store.memory(business.run_id, opportunity, "scope_change", {"parent_order": order["id"], "feedback": feedback}, business.clock(), order["customer_id"])
    return {**result, "customerToken": token}
