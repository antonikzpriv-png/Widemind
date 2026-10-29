"""Regression proofs from the second accounting audit; synthetic costs only."""
import pytest

from widemind.economy.service import EconomyError, EconomyService


def test_provisional_cost_cannot_be_refunded_twice(tmp_path):
    service = EconomyService(tmp_path/'economy.sqlite3')
    service.create_run('run')
    rid = service.reserve('run','action',1_000_000,'reserve','fixture','account',{'max_tokens':100})['reservation_id']
    service.start_reservation(rid)
    service.settle(rid,1_000_000,'fixture','account','charge',outcome='unknown')
    entry = service.ledger('run')[-1]
    with pytest.raises(EconomyError, match='Finalize'):
        service.correct_entry('run',entry['entry_id'],1_000_000,'incorrect provisional correction','bad-correction')
    service.settle(rid,0,'fixture','account','charge')
    assert service.balance('run')['posted_micro_usd'] == 5_000_000
    assert service.balance('run')['reserved_micro_usd'] == 0
    # Verified final charges still support explicitly authorized corrections.
    corrected = service.correct_entry('run',entry['entry_id'],-1,'documented final correction','allowed-correction')
    service.correct_entry('run',corrected['entry_id'],1,'reverse correction','reverse-correction')
    assert service.balance('run')['posted_micro_usd'] == 5_000_000


def test_completed_checkout_recovery_without_checkout_url_never_changes_binding(tmp_path):
    service = EconomyService(tmp_path/'economy.sqlite3')
    service.create_run('run')
    service.record_checkout('run','stripe','acct_fixture','test','request','job',1_000_000,{})
    result = service.finish_checkout('stripe','acct_fixture','test','request','cs_completed',None)
    assert result['payment_id'] == 'cs_completed' and result['url'] is None
    assert service.finish_checkout('stripe','acct_fixture','test','request','cs_completed',None) == result
    with pytest.raises(EconomyError):
        service.finish_checkout('stripe','acct_fixture','test','request','cs_other',None)
    assert service.balance('run')['posted_micro_usd'] == 5_000_000


def test_missing_checkout_url_does_not_erase_known_url(tmp_path):
    service = EconomyService(tmp_path/'economy.sqlite3')
    service.create_run('run')
    service.record_checkout('run','stripe','acct_fixture','test','request','job',1_000_000,{})
    service.finish_checkout('stripe','acct_fixture','test','request','cs_known','https://checkout.stripe.com/fixture')
    result = service.finish_checkout('stripe','acct_fixture','test','request','cs_known',None)
    assert result['url'] == 'https://checkout.stripe.com/fixture'


def test_direct_service_cursor_bounds_fail_cleanly(tmp_path):
    service = EconomyService(tmp_path/'economy.sqlite3')
    service.create_run('run')
    with pytest.raises(EconomyError) as error:
        service.events('run',2**100)
    assert error.value.status == 422
