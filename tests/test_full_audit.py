"""Explicit offline regressions from the full-build audit."""
import json
import unittest

import test_business as original
from widemind.business.contracts import Outcome
from widemind.business.store import identity
from widemind.business.views import console_view
from widemind.demo import drain


class FullBuildAuditTests(unittest.TestCase):
    def setUp(self):
        self.h = original.BusinessTests()
        self.h.setUp()

    def tearDown(self):
        self.h.tearDown()

    def test_authorized_offer_retry_actually_schedules_its_owner(self):
        h = self.h
        execute = h.adapters.execute
        h.adapters.execute = lambda **kw: Outcome('failed',reason='known unsent fixture failure') if kw['action']=='gmail.send' else execute(**kw)
        h.request()
        drain(h.business)
        effect=h.store.one("SELECT * FROM effects WHERE action='gmail.send'")
        self.assertEqual(effect['state'],'failed')
        h.adapters.execute=execute
        h.business.retry_effect(effect['id'],evidence_reference='operator repaired fixture transport')
        drain(h.business)
        self.assertEqual(h.store.one('SELECT status FROM quotes')['status'],'offered')
        self.assertEqual(h.count('gmail.send'),1)

    def test_verified_offer_reconciliation_schedules_progress_even_without_an_order(self):
        h=self.h
        h.adapters.unknown_actions.add('gmail.send')
        h.request()
        drain(h.business)
        effect=h.store.one("SELECT * FROM effects WHERE state='unknown'")
        h.business.notify(source='audit',external_id='reconcile-offer',kind='reconcile',payload={'id':effect['id']})
        drain(h.business)
        self.assertEqual(h.store.one('SELECT status FROM quotes')['status'],'offered')
        self.assertEqual(h.count('gmail.send'),1)

    def test_reservation_recovery_does_not_extend_the_delivery_deadline(self):
        h=self.h
        job=h.order()
        first_authorization=h.now
        h.adapters.unknown_actions.add('reserve')
        h.paid(job)
        drain(h.business)
        self.assertEqual(h.business.order(job)['status'],'awaiting_payment')
        h.now+=3600
        h.wake(job)
        h.until(job,'funded')
        self.assertEqual(h.business.order(job)['delivery_due_at'],first_authorization+h.business.policy.turnaround_seconds)
        self.assertEqual(h.count('reserve'),1)

    def test_payment_snapshot_exposes_when_it_was_last_observed(self):
        h=self.h
        job=h.order()
        last_observed=h.now
        h.now+=600
        order=h.business.order(job)
        self.assertEqual(order['payment_source'],'cached_order_snapshot')
        self.assertEqual(order['payment_observed_at'],last_observed)
        h.wake(job,'payment.changed')
        drain(h.business)
        self.assertEqual(h.business.order(job)['payment_observed_at'],h.now)

    def test_old_open_commitment_is_counted_and_reachable_after_500_completions(self):
        h=self.h
        job=h.order()
        h.paid(job)
        h.until(job,'ready')
        order=h.store.one('SELECT * FROM orders WHERE id=?',(job,))
        quote=h.store.one('SELECT * FROM quotes WHERE id=?',(order['quote_id'],))
        opportunity=h.store.one('SELECT * FROM opportunities WHERE id=?',(quote['opportunity_id'],))
        # Explicit database fixtures, never live completed jobs or financial evidence.
        def insert(table,values):
            h.store.db.execute('INSERT INTO '+table+' ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',tuple(values.values()))
        with h.store.transaction():
            for index in range(500):
                opp={**opportunity,'id':identity('completed-fixture-opportunity',index),'status':'completed','created_at':h.now+index+1}
                q={**quote,'id':identity('completed-fixture-quote',index),'opportunity_id':opp['id']}
                o={**order,'id':identity('completed-fixture-order',index),'quote_id':q['id'],'status':'completed','created_at':h.now+index+1}
                insert('opportunities',opp);insert('quotes',q);insert('orders',o)
        view=console_view(h.business)
        self.assertEqual(view['counts']['open_commitments'],1)
        self.assertEqual(view['counts']['orders'],501)
        self.assertIn(job,[row['id'] for row in view['orders']])
        self.assertTrue(view['pagination']['orders']['has_more'])
        tail=console_view(h.business,collection='orders',offset=500)
        self.assertEqual(len(tail['orders']),1)
        self.assertFalse(tail['pagination']['orders']['has_more'])

    def test_delivered_work_has_no_remaining_physical_delivery_obligation(self):
        h=self.h
        job=h.order()
        h.paid(job)
        h.until(job,'delivered')
        self.assertFalse(h.business.order(job)['delivery_obligation_open'])
        self.assertEqual(console_view(h.business)['counts']['open_commitments'],0)
        self.assertEqual(json.loads(h.business.order(job)['payment'])['earned_micro_usd'],0)

    def test_console_paging_queries_are_validated_at_the_authenticated_boundary(self):
        import io
        from widemind.business.api import API
        from widemind.business.engine import Business
        from widemind.business.store import Store
        from widemind.demo import DemoAdapters
        h=self.h
        def factory():
            adapter=DemoAdapters(h.directory/'fixtures.sqlite3',mode='test')
            return Business(Store(h.directory/'business.sqlite3'),run_id='test-run',economy=adapter,tools=adapter,
                            runtime=adapter,clock=lambda:h.now,mode='test')
        token='audit-only-operator-token-'*3
        app=API(factory,operator_token=token)
        def call(query):
            response=[]
            body=b''.join(app({'REQUEST_METHOD':'GET','PATH_INFO':'/api/v1/console','QUERY_STRING':query,
                              'HTTP_AUTHORIZATION':'Bearer '+token,'wsgi.input':io.BytesIO()},
                             lambda status,headers:response.append(status)))
            return response[0],json.loads(body)
        self.assertEqual(call('collection=orders&offset=500')[0],'200 OK')
        for query in ('offset=500','collection=secrets','collection=orders&offset=-1',
                      'collection=orders&offset=0&offset=500','collection=orders&offset=9999999999'):
            with self.subTest(query=query):
                self.assertEqual(call(query)[0],'400 Bad Request')

    def test_operator_token_configuration_cannot_silently_make_authentication_impossible(self):
        from widemind.business.api import API
        for token in ('a'*31+'é','a'*32+' ','a'*31+'\n'):
            with self.subTest(kind=repr(token[-1])),self.assertRaises(ValueError):
                API(lambda:None,operator_token=token)
        API(lambda:None,operator_token='a'*32)

    def test_console_records_and_totals_share_one_read_snapshot(self):
        from widemind.business.store import Store
        h=self.h
        job=h.order()
        h.paid(job)
        h.until(job,'ready')
        original=h.business.order
        changed=[]
        def concurrent_completion(order_id):
            row=original(order_id)
            if not changed:
                changed.append(True)
                other=Store(h.directory/'business.sqlite3')
                try:
                    # Explicit concurrency fixture: a writer commits after this
                    # reader fetched a record but before it counts obligations.
                    other.db.execute("UPDATE orders SET status='completed' WHERE id=?",(job,))
                finally:
                    other.close()
            return row
        h.business.order=concurrent_completion
        view=console_view(h.business)
        self.assertEqual(view['orders'][0]['status'],'ready')
        self.assertEqual(view['counts']['open_commitments'],1)
        self.assertEqual(original(job)['status'],'completed')


if __name__=='__main__':
    unittest.main()
