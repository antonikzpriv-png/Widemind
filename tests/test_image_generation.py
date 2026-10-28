"""No live provider calls: synthetic raw Images payloads + controlled local HTTP."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from widemind.tools.contracts import ToolError, Unavailable, Uncertain
from widemind.tools.image_generation import ImageGenerationAdapter

from test_ai import (FixtureTransport, LocalHTTPTransport, MemorySecrets, MemoryState,
                     MemoryWorkspace, config, context)


# Public-domain 1x1 PNG, generated locally. Not an inference recording.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/a9sAAAAASUVORK5CYII=")


def image_config(**overrides):
    cfg = config(model="configured-image-model", size="1024x1024", quality="low", max_images=2,
                 max_image_bytes=1000, max_response_bytes=10_000,
                 charge_bound={"certified": True, "model": "configured-image-model", "size": "1024x1024",
                               "quality": "low", "max_prompt_bytes": 32_000,
                               "maximum_microusd_per_image": 30_000, "evidence": "synthetic test fixture bound"})
    cfg.update(overrides)
    return cfg


def image_response(n=1, **overrides):
    result = {"created": 1620000000, "data": [{"b64_json": base64.b64encode(PNG).decode()} for _ in range(n)],
              "size": "1024x1024", "quality": "low", "output_format": "png", "background": "opaque",
              "usage": {"input_tokens": 50, "output_tokens": 100, "total_tokens": 150,
                        "input_tokens_details": {"text_tokens": 50, "image_tokens": 0}}}
    result.update(overrides)
    return result


def image_context(payload=None, **kwargs):
    return context(image_config(), FixtureTransport(payload or image_response()), **kwargs)


def test_image_quote_exact_and_native_usage_receipt():
    adapter = ImageGenerationAdapter()
    assert adapter.quote({"prompt": "A leaf", "n": 2}, image_config()).maximum_microusd == 60_000
    ctx = image_context(image_response(2))
    result = adapter.execute({"prompt": "A leaf", "n": 2, "output_prefix": "artwork/leaf"}, ctx)
    assert result.usage.units == {"images": 2, "input_tokens": 50, "output_tokens": 100, "total_tokens": 150}
    assert result.usage.cost_microusd == (50 * 123456 + 100 * 654321 + 999999) // 1000000
    assert result.usage.provisional and result.trust == "untrusted"
    assert len(result.artifacts) == 2 and all(path.startswith("artwork/leaf-") for path in ctx.workspace.files)
    assert all(data == PNG for data in ctx.workspace.files.values())
    request = json.loads(ctx.transport.calls[0][2]["body"])
    assert request["output_format"] == "png" and request["size"] == "1024x1024" and request["quality"] == "low"
    assert "response_format" not in request and "max_output_tokens" not in request
    assert ctx.state.data["image_response_body"] and ctx.state.data["image_request_id"] == "req_synthetic"


def test_bound_requires_exact_owner_certification():
    adapter = ImageGenerationAdapter()
    for overrides in [{"charge_bound": {}}, {"model": "changed-model"}, {"size": "auto"}, {"quality": "auto"}, {"max_input_bytes": 1000}]:
        with pytest.raises(Unavailable):
            adapter.quote({"prompt": "x"}, image_config(**overrides))
    assert not adapter.availability(image_config(), MemorySecrets({}))["available"]


@pytest.mark.parametrize("arguments", [{"prompt": "x", "n": 3}, {"prompt": "x", "n": True},
    {"prompt": "x", "size": "2048x2048"}, {"prompt": "x", "secret_ref": "caller"},
    {"prompt": "x", "max_cost_microusd": 1}, {"prompt": "x", "output_prefix": "../host"},
    {"prompt": "x", "output_prefix": "/absolute"}, {"prompt": "x", "output_prefix": "a//b"}])
def test_unbounded_or_caller_control_configuration_rejected(arguments):
    ctx = image_context()
    with pytest.raises(ToolError):
        ImageGenerationAdapter().execute(arguments, ctx)
    assert not ctx.transport.calls and not ctx.workspace.writes


def test_saved_image_response_recovers_without_another_paid_request():
    ctx = image_context()
    adapter = ImageGenerationAdapter()
    result = adapter.execute({"prompt": "x"}, ctx)
    assert adapter.execute({"prompt": "x"}, ctx) == result
    assert len(ctx.transport.calls) == 1 and ctx.workspace.writes == 1


def test_crash_after_file_write_recovers_from_receipt():
    class InterruptedWorkspace(MemoryWorkspace):
        def write_bytes(self, path, data, *, overwrite=False):
            super().write_bytes(path, data, overwrite=overwrite)
            raise OSError("simulated crash after durable write")

    ctx = image_context(workspace=InterruptedWorkspace())
    adapter = ImageGenerationAdapter()
    with pytest.raises(Uncertain) as failure:
        adapter.execute({"prompt": "x"}, ctx)
    assert failure.value.usage.cost_microusd > 0
    recovery = adapter.reconcile({"prompt": "x"}, ctx)
    assert recovery.outcome == "completed" and len(recovery.result.artifacts) == 1
    assert ctx.workspace.writes == 1 and len(ctx.transport.calls) == 1


def test_missing_usage_does_not_become_free_images():
    ctx = image_context(image_response(usage=None))
    with pytest.raises(Uncertain) as failure:
        ImageGenerationAdapter().execute({"prompt": "x"}, ctx)
    assert failure.value.usage.cost_microusd is None and failure.value.usage.units["images"] == 1
    assert ctx.state.data["image_response_body"] and ctx.workspace.writes == 0
    assert ImageGenerationAdapter().reconcile({"prompt": "x"}, ctx).outcome == "uncertain"


def test_image_reconciliation_preserves_known_billable_failure_usage():
    ctx = image_context(image_response(data=[]))
    adapter = ImageGenerationAdapter()
    with pytest.raises(Uncertain):
        adapter.execute({"prompt": "x"}, ctx)
    recovery = adapter.reconcile({"prompt": "x"}, ctx)
    assert recovery.outcome == "uncertain" and recovery.result.usage.cost_microusd > 0
    assert len(ctx.transport.calls) == 1


def test_unknown_image_request_is_never_repeated_or_retrieved():
    ctx = context(image_config(), FixtureTransport(TimeoutError("controlled timeout")))
    adapter = ImageGenerationAdapter()
    for _ in range(2):
        with pytest.raises(Uncertain):
            adapter.execute({"prompt": "x"}, ctx)
    assert adapter.reconcile({"prompt": "x"}, ctx).outcome == "uncertain"
    assert len(ctx.transport.calls) == 1


@pytest.mark.parametrize("data", [[{"url": "https://private.example/asset.png"}], [{"b64_json": "not base64"}],
    [{"b64_json": base64.b64encode(b"not png").decode()}], [],
    [{"b64_json": base64.b64encode(PNG).decode()}] * 2])
def test_invalid_provider_images_keep_paid_usage(data):
    ctx = image_context(image_response(data=data))
    with pytest.raises(Uncertain) as failure:
        ImageGenerationAdapter().execute({"prompt": "x"}, ctx)
    assert failure.value.usage.cost_microusd > 0 and ctx.state.data["image_response_body"]
    assert ctx.workspace.writes == 0


def test_fewer_images_are_partial_work_with_measured_usage():
    ctx = image_context()
    result = ImageGenerationAdapter().execute({"prompt": "x", "n": 2}, ctx)
    assert result.data["partial"] and result.data["images"] == 1 and result.usage.units["images"] == 1


def test_artifact_conflict_does_not_overwrite_or_regenerate():
    ctx = image_context()
    adapter = ImageGenerationAdapter()
    result = adapter.execute({"prompt": "x"}, ctx)
    path = result.artifacts[0]["path"]
    ctx.workspace.files[path] = b"different existing content"
    assert adapter.reconcile({"prompt": "x"}, ctx).outcome == "uncertain"
    assert ctx.workspace.files[path] == b"different existing content" and len(ctx.transport.calls) == 1


def test_cancelled_image_work_checks_before_any_paid_request():
    ctx = image_context(cancelled=lambda: True)
    with pytest.raises(ToolError, match="cancelled"):
        ImageGenerationAdapter().execute({"prompt": "x"}, ctx)
    assert not ctx.transport.calls and not ctx.workspace.writes


def test_controlled_http_images_contract():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            payload = json.dumps(image_response()).encode()
            self.send_response(200)
            self.send_header("x-request-id", "req_image_local")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg = image_config(endpoint=f"http://127.0.0.1:{server.server_port}/v1", allow_local_http=True)
        ctx = context(cfg, LocalHTTPTransport())
        result = ImageGenerationAdapter().execute({"prompt": "controlled fixture"}, ctx)
        assert result.usage.provider_request_id == "req_image_local" and len(result.artifacts) == 1
        assert received[0][0] == "/v1/images/generations" and received[0][1]["prompt"] == "controlled fixture"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
