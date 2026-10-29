"""Deterministic synthetic accounting fixtures, never claims about live APIs."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import sqlite3
import time

import pytest

from widemind.control import ControlStore, JournalState
from widemind.tools.contracts import Quote, ToolError, ToolResult, Usage


@pytest.fixture
def store(tmp_path):
    store = ControlStore(tmp_path / "private")
    store.create_run("test",100)
    return store


def prepare(store, action="one", maximum=60, **kwargs):
    return store.prepare(action_id=action,run_id="test",job_id=None,tool="fixture",version=1,
                         arguments=kwargs.pop("arguments",{}),config={},policy={},
                         quote=Quote(maximum,"fixture",maximum>0),**kwargs)


def test_concurrent_reservations_and_atomic_settlement(store):
    def reserve(i):
        try:
            prepare(store,str(i),60)
            return True
        except ToolError as exc:
            assert exc.code == "budget_exhausted"
            return False
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(reserve,range(32)))
    assert sum(results) == 1
    assert store.balance("test")["spendable_microusd"] == 40


def test_uncertain_usage_charged_once_and_unused_reservation_retained(store):
    prepare(store)
    claim = store.claim("one",1)
    JournalState(store,claim).dispatched("one",{"provider":"synthetic"})
    receipt = store.finish("one",claim,"uncertain",usage=Usage(20,{"tokens":2},provisional=True))
    assert (receipt["charged_microusd"],receipt["reserved_microusd"]) == (20,40)
    assert receipt["result"]["usage"]["units"] == {"tokens":2}
    assert store.balance("test")["spendable_microusd"] == 40
    with pytest.raises(ToolError,match="Reconcile"):
        prepare(store,"two",1)
    # Restart does not release unknown liability.
    restarted = ControlStore(store.root)
    claim = restarted.claim("one",1,reconciliation=True)
    restarted.finish("one",claim,"uncertain",usage=Usage(20))
    assert restarted.balance("test")["ledger_microusd"] == 80
    claim = restarted.claim("one",1,reconciliation=True)
    restarted.finish("one",claim,"succeeded",result=ToolResult({"fixture":True},Usage(25)))
    assert restarted.balance("test")["spendable_microusd"] == 75
    with restarted.connection() as db:
        assert [r[0] for r in db.execute("SELECT amount FROM ledger WHERE kind='provider_charge' ORDER BY id")] == [-20,-5]


def test_unknown_bill_never_becomes_free_and_free_tools_remain_usable(store):
    prepare(store)
    claim = store.claim("one",1)
    JournalState(store,claim).dispatched("one",{})
    receipt = store.finish("one",claim,"failed")
    assert receipt["status"] == "uncertain"
    assert receipt["reserved_microusd"] == 60
    prepare(store,"free",0)
    claim = store.claim("free",1)
    store.finish("free",claim,"succeeded",result=ToolResult({}))


def test_over_quote_liability_is_posted_not_hidden(store):
    prepare(store)
    claim = store.claim("one",1)
    JournalState(store,claim).dispatched("one",{})
    store.finish("one",claim,"succeeded",usage=Usage(120))
    assert store.balance("test")["spendable_microusd"] == -20
    with pytest.raises(ToolError,match="Insufficient"):
        prepare(store,"two",1)


def test_append_only_ledger_and_idempotent_opening_funding(store):
    store.create_run("test",1_000_000)
    assert store.balance("test")["ledger_microusd"] == 100
    with store.connection(True) as db:
        with pytest.raises(sqlite3.IntegrityError,match="append-only"):
            db.execute("UPDATE ledger SET amount=999")
        with pytest.raises(sqlite3.IntegrityError,match="append-only"):
            db.execute("DELETE FROM ledger")


def test_action_replay_conflict_lease_and_expired_recovery(store):
    prepare(store)
    _,fresh = prepare(store)
    assert fresh is False
    with pytest.raises(ToolError,match="different inputs"):
        prepare(store,arguments={"different":True})
    token = store.claim("one",1)
    with pytest.raises(ToolError,match="Another worker"):
        store.claim("one",1)
    with store.connection(True) as db:
        db.execute("UPDATE actions SET lease=? WHERE id='one'",(time.time()-1,))
    assert store.claim("one",1) is None
    assert store.receipt("one")["status"] == "uncertain"
    with pytest.raises(ToolError,match="missing or expired"):
        store.authorize("one",token)


def test_state_merges_are_durable_intents_precede_dispatch_and_private(store):
    prepare(store)
    token = store.claim("one",1)
    state = JournalState(store,token)
    state.update("one",{"provider_id":"private-correlation"})
    state.update("one",{"another":1})
    state.dispatched("one",{"content_hash":"abc"})
    assert state.load("one") == {"provider_id":"private-correlation","another":1}
    assert json.loads(store.get("one")["intents"]) == [{"content_hash":"abc"}]
    assert "private-correlation" not in json.dumps(store.receipt("one"))
    with pytest.raises(ToolError,match="current lease"):
        JournalState(store,"wrong").update("one",{"override":True})


@pytest.mark.parametrize("amount",[True,False,0.5,-1,10**30])
def test_money_is_exact_bounded_integer(store,amount):
    with pytest.raises(ToolError):
        prepare(store,maximum=amount)


def test_pause_cancel_revoke_execution_but_keep_reconciliation(store):
    prepare(store)
    token = store.claim("one",1)
    store.pause("test")
    with pytest.raises(ToolError,match="stopped"):
        store.authorize("one",token)
    store.authorize("one",token,reconciliation=True)
    store.cancel("one")
    assert store.get("one")["cancelled"]
