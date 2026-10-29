"""Accounting regressions from final integration review; no real provider calls."""
import pytest

from widemind.gateway import Gateway
from widemind.tools.contracts import Quote, ToolResult, ToolSpec, Usage


class MeasuredFixture:
    spec = ToolSpec("audit.measured", "Synthetic known-charge fixture", "external")

    def __init__(self, *, deep_result=False, huge_usage_key=False):
        self.calls, self.deep_result, self.huge_usage_key = 0, deep_result, huge_usage_key

    def availability(self, config, secrets):
        return {"available": True}

    def quote(self, arguments, config):
        return Quote(100, "synthetic-security-audit", True)

    def execute(self, arguments, ctx):
        ctx.mark_dispatched({"provider": "synthetic-security-audit", "live_calls": 0})
        self.calls += 1
        data, units = {"result": "synthetic"}, {"synthetic_requests": 1}
        if self.deep_result:
            data = []
            tail = data
            for _ in range(1500):
                child = []
                tail.append(child)
                tail = child
        if self.huge_usage_key:
            units = {"untrusted-key-" + "x" * 4_100_000: 1}
        return ToolResult(data, Usage(7, units))


@pytest.mark.parametrize("options", [{"deep_result": True}, {"huge_usage_key": True}],
                         ids=["deep-provider-result", "oversized-usage-envelope"])
def test_presentation_failure_keeps_measured_cost_in_independently_bounded_receipt(tmp_path, options):
    adapter = MeasuredFixture(**options)
    runtime = Gateway({"control_dir": str(tmp_path / "private"),
                       "workspace_dir": str(tmp_path / "workspace"),
                       "policy": {"profile": "sandbox"},
                       "tools": {"audit.measured": {"enabled": True}}},
                      adapters=[adapter], transport=object())
    runtime.store.create_run("security-audit", 1000)
    receipt = runtime.execute("audit.measured", {}, run_id="security-audit", action_id="known-cost")
    assert receipt["status"] == "uncertain"
    assert receipt["charged_microusd"] == 7
    assert runtime.store.balance("security-audit")["ledger_microusd"] == 993
    assert receipt["reserved_microusd"] == 93
    # A presentation failure must not be repaired by executing the paid request
    # again under the same action identity.
    replay = runtime.execute("audit.measured", {}, run_id="security-audit", action_id="known-cost")
    assert replay["charged_microusd"] == 7
    assert adapter.calls == 1
