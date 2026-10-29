"""Explicit UI test mode: local fixture HTTP endpoints, no provider or paid calls.

Run: python tests/ui_browser.py (requires Playwright + its Chromium browser).
This is intentionally separate from the standard-library backend unit suite.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import shutil
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ASSETS = Path(__file__).resolve().parents[1] / "widemind" / "web" / "static"
TOKEN = "test-ui-token-only-never-production-0000"
TOPIC = '<img src=x onerror="window.XSS=true"> customer topic'
NOW = 1800000000


def fixture():
    return {
        "mode": "test", "run_id": "ui-test-run", "runtime_state": "active", "controls_available": True,
        "policy": {"goals": ["Deliver useful work"], "capabilities": ["research"], "prices": {"research-brief": 2000000},
                   "experiment_budget_micro_usd": 1000000, "max_job_micro_usd": 250000, "turnaround_seconds": 86400,
                   "quote_valid_seconds": 172800, "max_open_orders": 10, "priority_weight": 10, "margin_weight": 1,
                   "goal_weights": {"research-brief": 1}, "allow_outbound": False},
        "summary": {"receipts_micro_usd": 2000000, "earned_micro_usd": 0, "operating_cost_micro_usd": 125000,
                    "spendable_micro_usd": None, "refunds_micro_usd": 0},
        "opportunities": [{"id": "a" * 40, "service": "research-brief", "status": "offered", "scope": {"topic": TOPIC, "sections": ["Overview"], "min_sources": 2}, "assessment": {"feasible": True, "expected_cost_micro_usd": 250000}, "customer_id": "customer1", "priority": 1, "origin": "inbound", "created_at": NOW}],
        "quotes": [{"id": "q" * 40, "opportunity_id": "a" * 40, "status": "offered", "scope": {"topic": TOPIC}, "price_micro_usd": 2000000, "cost_micro_usd": 250000, "expires_at": NOW + 172800, "turnaround_seconds": 86400}],
        "orders": [{"id": "b" * 40, "customer_id": "customer1", "service": "research-brief", "status": "ready", "scope": {"topic": TOPIC}, "payment": {"status": "settled", "receipts_micro_usd": 2000000, "refunds_micro_usd": 0, "earned_micro_usd": 0}, "payment_source": "cached_order_snapshot", "payment_observed_at": NOW, "delivery_due_at": NOW + 86400, "delivery_obligation_open": True}],
        "customers": [{"id": "customer1", "email": "customer@example.invalid", "opted_out": False}],
        "effects": [{"id": "c" * 40, "job_id": "b" * 40, "action": "gmail.send", "provider": "tools", "state": "unknown", "attempt": 0, "reason": "Delivery response lost"}],
        "events": [{"id": "d" * 40, "kind": "advance", "state": "blocked", "due_at": NOW, "error": "external outcome uncertain"}],
        "integrations": [{"name": "Gmail", "status": "test", "detail": "Explicit fixture adapter"}],
    }


@contextmanager
def fixture_server():
    requests = []
    controls = {"hold_next_console": False, "console_ready": threading.Event(), "console_release": threading.Event(), "console_requests": 0}
    data = fixture()
    data['orders'].append({**data['orders'][0], 'id': 'e' * 40, 'scope': {'topic': 'Older delayed order'}, 'payment_observed_at': None})
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def send(self, content, kind="application/json", status=200):
            content = json.dumps(content).encode() if kind == "application/json" else content
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)
        def do_GET(self):
            path = urlsplit(self.path).path
            if path in {"/", "/console"}:
                return self.send((ASSETS / ("index.html" if path == "/" else "console.html")).read_bytes(), "text/html")
            if path in {"/static/app.js", "/static/style.css"}:
                return self.send((ASSETS / path.rsplit("/", 1)[-1]).read_bytes(), "text/javascript" if path.endswith(".js") else "text/css")
            if path == "/api/v1/public":
                return self.send({"mode": "test", "run_state": "active", "offerings": [{"name": "research-brief", "description": "Explicit fixture service", "price_micro_usd": 2000000}], "activity": [], "finances": data["summary"], "contact_email": "widemind@example.invalid"})
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self.send({"error": "authentication required"}, status=401)
            if path == "/api/v1/console":
                controls['console_requests'] += 1
                snapshot = json.loads(json.dumps(data))
                if controls['hold_next_console']:
                    controls['hold_next_console'] = False
                    controls['console_ready'].set()
                    controls['console_release'].wait(timeout=10)
                return self.send(snapshot)
            if path.startswith("/api/v1/orders/"):
                order = dict(data["orders"][0])
                order["id"] = path.rsplit("/", 1)[-1]
                if order["id"] == "e" * 40:
                    time.sleep(.35)
                    order["scope"] = {"topic": "Older delayed order"}
                order["artifact"] = json.dumps({"filename": "brief.md", "media_type": "text/markdown", "content": "# PRIVATE FIXTURE", "checks": {"scope": True}})
                order["scope"] = json.dumps(order["scope"])
                order["payment"] = json.dumps(order["payment"])
                return self.send(order)
            self.send({"error": "not found"}, status=404)
        def do_POST(self):
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self.send({"error": "authentication required"}, status=401)
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, "body": body})
            if self.path == "/api/v1/runtime":
                data["runtime_state"] = body["state"]
            return self.send({"recorded": True})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, controls
    finally:
        controls['console_release'].set()
        server.shutdown()
        server.server_close()
        thread.join()


def main():
    checks = 0
    def check(condition, message):
        nonlocal checks
        if not condition:
            raise AssertionError(message)
        checks += 1
    with fixture_server() as (url, requests, controls), sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.get_by_text("Explicit fixture service", exact=True).wait_for()
        check(page.locator('#public-mode').is_visible(), "public fixture warning visible")
        check(page.locator('#public-metrics').inner_text().count('Unavailable') == 1, "missing credit is unavailable, not zero")
        page.get_by_role('button', name='Discuss a request', exact=False).click()
        page.locator('input[name="topic"]').fill('Explicit customer request fixture')
        page.get_by_role('button', name='Prepare email draft', exact=True).click()
        draft = page.locator('a[href^="mailto:"]')
        check(draft.count() == 1, "contact request uses dedicated email")
        from urllib.parse import parse_qs
        command = json.loads(parse_qs(urlsplit(draft.get_attribute('href')).query)['body'][0])
        check(command['type'] == 'request' and command['service'] == 'research-brief', 'public draft matches Gmail command contract')
        check(command['scope']['topic'] == 'Explicit customer request fixture', 'public draft preserves requested scope')
        page.locator('textarea[name="source_urls"]').fill('https:example.com/a\nhttps:example.org/b')
        page.get_by_role('button', name='Prepare email draft', exact=True).click()
        check(not page.locator('a[href^="mailto:"]').count(), 'malformed HTTPS sources cannot enter a customer draft')
        page.locator('textarea[name="source_urls"]').fill('https://example.com/a\nhttps://example.com/a')
        page.get_by_role('button', name='Prepare email draft', exact=True).click()
        check('unique' in page.locator('#dialog-content .dialog-message').inner_text(), 'duplicate source URLs are rejected before email composition')
        page.locator('#close-dialog').click()
        for width in [1440, 1024, 390, 320]:
            page.set_viewport_size({"width": width, "height": 950})
            check(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"public no horizontal overflow at {width}")
        page.goto(url + '/console')
        check(not page.locator('#workspace').is_visible(), "private workspace hidden before auth")
        page.locator('#operator-token').fill('wrong-token-which-is-long-enough-0000')
        page.get_by_role('button', name='Connect', exact=False).click()
        page.locator('#login-error').wait_for(state='visible')
        check(not page.locator('#workspace').is_visible(), "invalid token reveals no private records")
        page.locator('#operator-token').fill(TOKEN)
        page.get_by_role('button', name='Connect', exact=False).click()
        page.locator('#workspace').wait_for(state='visible')
        check(page.locator('#operator-token').input_value() == '', "token input cleared immediately")
        check(page.evaluate('localStorage.length === 0 && sessionStorage.length === 0'), "no credentials persisted in browser storage")
        check(page.locator('#console-mode').is_visible(), "private fixture warning visible")
        check(page.locator('#view').get_by_text('Unavailable', exact=True).count() == 1, "unknown spendable balance is explicit")
        for width in [1440, 1024, 390, 320]:
            page.set_viewport_size({"width": width, "height": 950})
            for section in ['overview', 'opportunities', 'orders', 'finances', 'recovery', 'settings']:
                page.locator(f'[data-view="{section}"]').click()
                check(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f"console {section} no overflow at {width}")
        check(page.evaluate('window.XSS !== true'), "customer text never executes markup")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.locator('[data-view="opportunities"]').click()
        page.get_by_role('button', name='Add opportunity').click()
        page.locator('input[name="customer_email"]').fill('actual@example.invalid')
        page.locator('input[name="topic"]').fill('An actual fixture request')
        page.locator('textarea[name="source_urls"]').fill('https://example.com/source\nhttps://example.org/source')
        page.get_by_role('button', name='Record opportunity', exact=True).click()
        page.locator('#detail-dialog').wait_for(state='hidden')
        recorded = next(item for item in requests if item['path'] == '/api/v1/opportunities')
        check(recorded['body']['scope']['source_urls'] == ['https://example.com/source', 'https://example.org/source'], "research URLs forwarded as structured scope")
        page.locator('[data-view="orders"]').click()
        check('cached historical snapshots' in page.locator('#view').inner_text(), 'order financial values are explicitly historical')
        check('Observation time unavailable' in page.locator('#view').inner_text(), 'missing snapshot observation time is explicit')
        page.locator('.table-button').first.click()
        page.get_by_role('button', name='Download private deliverable').wait_for()
        check(page.locator('#dialog-content').inner_text().find('# PRIVATE FIXTURE') >= 0, "authenticated artifact renders as text")
        with page.expect_download() as download_info:
            page.get_by_role('button', name='Download private deliverable').click()
        check(download_info.value.suggested_filename == 'brief.md', "private download filename preserved")
        page.locator('#close-dialog').click()
        # A late response for a closed dialog must not overwrite the newer order.
        page.locator('.table-button').nth(1).click()
        page.locator('#dialog-content .loading-indicator').wait_for()
        page.locator('#close-dialog').click()
        page.locator('.table-button').first.click()
        page.get_by_role('button', name='Download private deliverable').wait_for()
        page.wait_for_timeout(600)
        check(page.locator('#dialog-title').inner_text() == TOPIC, 'late order response cannot replace a newer dialog')
        page.locator('#close-dialog').click()
        # A real deferred HTTP read captures Active before the mutation. The
        # successful command must await a subsequent read and discard that copy.
        reads_before = controls['console_requests']
        controls['hold_next_console'] = True
        page.locator('#refresh').click()
        deadline = time.monotonic() + 5
        while not controls['console_ready'].is_set() and time.monotonic() < deadline:
            page.wait_for_timeout(10)
        check(controls['console_ready'].is_set(), 'concurrent refresh reached the deferred HTTP response')
        page.get_by_role('button', name='Pause', exact=True).click()
        page.locator('input[name="evidence"]').fill('Actual operator pause for testing')
        page.get_by_role('button', name='Record and schedule').click()
        deadline = time.monotonic() + 5
        while not any(item['path'] == '/api/v1/runtime' for item in requests) and time.monotonic() < deadline:
            page.wait_for_timeout(10)
        check(any(item['path'] == '/api/v1/runtime' for item in requests), 'pause command completed while the older read was pending')
        page.wait_for_timeout(50)
        check(page.locator('#detail-dialog').is_visible(), 'command waits for a read newer than its mutation')
        controls['console_release'].set()
        page.locator('#detail-dialog').wait_for(state='hidden')
        check(page.locator('#run-state').inner_text() == 'Paused', "runtime control reflects server state")
        check(controls['console_requests'] >= reads_before + 2, 'a completed command queues a fresh HTTP read after the older refresh')
        page.locator('[data-view="recovery"]').click()
        page.get_by_role('button', name='Reconcile outcome').click()
        page.locator('input[name="evidence"]').fill('Inspect the persisted provider effect')
        page.get_by_role('button', name='Record and schedule').click()
        page.locator('#detail-dialog').wait_for(state='hidden')
        reconcile = next(item for item in requests if item['path'] == '/api/v1/events')
        check(reconcile['body']['kind'] == 'reconcile', "recovery schedules reconciliation rather than direct execution")
        page.get_by_role('button', name='Lock console').click()
        check(not page.locator('#workspace').is_visible(), "locking hides private records")
        check(page.locator('#view').inner_text() == '', "locking removes private DOM content")
        check(page.locator('#run-label').inner_text() == '', 'locking removes the private run identifier')
        check(page.locator('#dialog-title').inner_text() == 'Details', 'closing/locking clears the private dialog title')
        check(not page.locator('#detail-dialog').is_visible(), "locking closes private dialog")
        check(page.evaluate('localStorage.length === 0 && sessionStorage.length === 0'), "locking leaves no stored credentials")
        # Provider-test is not described as an offline fixture demonstration.
        provider = fixture()
        provider.update(execution_profile='provider-test', uses_fixtures=False)
        provider['summary'].update(economy_unavailable=True, source='unavailable', receipts_micro_usd=None, earned_micro_usd=None)
        page.route('**/api/v1/console', lambda route: route.fulfill(content_type='application/json', body=json.dumps(provider)))
        page.locator('#operator-token').fill(TOKEN)
        page.get_by_role('button', name='Connect', exact=False).click()
        page.locator('#workspace').wait_for(state='visible')
        check('external calls enabled' in page.locator('#console-mode').inner_text(), 'provider-test explicitly warns about real external actions')
        check('fixture' not in page.locator('#console-mode').inner_text(), 'provider-test is not mislabeled offline')
        page.locator('[data-view="finances"]').click()
        check('The current economy is unavailable.' in page.locator('#view').inner_text(), 'economy failure does not present order snapshots as current money')
        check('Cached payment' in page.locator('thead').inner_text(), 'order payment status is labeled as cached')
        check('UTC' in page.locator('tbody').inner_text(), 'payment observation timestamp is displayed')
        page.get_by_role('button', name='Lock console').click()
        page.route('**/api/v1/public', lambda route: route.fulfill(status=503, content_type='application/json', body=json.dumps({'error': 'dependency unavailable'})))
        page.goto(url)
        page.locator('#public-error').wait_for(state='visible')
        check('No balance or earnings are assumed' in page.locator('#public-metrics').inner_text(), 'public failure does not display invented metrics')
        check(not errors, f"no browser exceptions: {errors}")
        browser.close()
    print(json.dumps({"mode": "test", "checks": checks, "status": "passed"}))


if __name__ == '__main__':
    main()
