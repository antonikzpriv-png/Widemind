"""Real client composition with fictional credentials; all network is forbidden."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from widemind.host import business_factory, initialize_live
from widemind.business.views import console_view


class HostCompositionTests(unittest.TestCase):
    def test_all_real_clients_construct_paused_without_any_network_or_implicit_funding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                'WIDEMIND_RUN_ID':'host-composition-fixture','WIDEMIND_MODE':'test',
                'WIDEMIND_BUSINESS_DB':str(root/'business'/'business.sqlite3'),
                'WIDEMIND_RUNTIME_DB':str(root/'control'/'runtime.sqlite3'),
                'WIDEMIND_ECONOMY_DB':str(root/'control'/'economy.sqlite3'),
                'WIDEMIND_TOOLS_DB':str(root/'control'/'tools.sqlite3'),
                'WIDEMIND_WORKSPACE':str(root/'workspace'),
                'WIDEMIND_GMAIL_ADDRESS':'widemind@example.invalid',
                'WIDEMIND_ALLOWED_RECIPIENTS':'["controlled@example.invalid"]',
                'WIDEMIND_OPERATOR_TOKEN':'explicit-fixture-token-'*3,
                'GMAIL_CLIENT_ID':'OFFLINE_CLIENT','GMAIL_CLIENT_SECRET':'OFFLINE_SECRET',
                'GMAIL_REFRESH_TOKEN':'OFFLINE_REFRESH','OPENAI_API_KEY':'OFFLINE_OPENAI_KEY',
                'STRIPE_SECRET_KEY':'sk_test_OFFLINE_FIXTURE','STRIPE_WEBHOOK_SECRET':'whsec_OFFLINE_FIXTURE',
                'STRIPE_SUCCESS_URL':'https://example.invalid/success','STRIPE_CANCEL_URL':'https://example.invalid/cancel',
                'OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS':'400000',
                'OPENAI_OUTPUT_MICRO_USD_PER_MILLION_TOKENS':'1600000',
                'OPENAI_CACHED_INPUT_MICRO_USD_PER_MILLION_TOKENS':'100000',
                'WIDEMIND_RESEARCH_SOURCE_URLS':'["https://example.com/a","https://example.com/b"]',
            }
            with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('host construction must not request providers')):
                setup = initialize_live(environ=env,evidence_reference='explicit offline fixture opening grant')
                self.assertEqual(setup['runtime_state'],'paused')
                for _ in range(2):
                    business = business_factory(env)
                    try:
                        view = console_view(business)
                        self.assertEqual(view['execution_profile'],'provider-test')
                        self.assertFalse(view['uses_fixtures'])  # Real classes; only supplied credentials are fictional.
                        self.assertEqual(view['runtime_state'],'paused')
                        self.assertEqual(view['summary']['opening_micro_usd'],5000000)
                        self.assertEqual(view['summary']['receipts_micro_usd'],0)
                        self.assertEqual(view['summary']['earned_micro_usd'],0)
                        self.assertEqual(view['summary']['spendable_micro_usd'],5000000)
                        self.assertTrue(view['controls_available'])
                        self.assertTrue(all(row['status']=='configured_unverified' for row in view['integrations']))
                    finally:
                        business.close()


if __name__=='__main__':
    unittest.main()
