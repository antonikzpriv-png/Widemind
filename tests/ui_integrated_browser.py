"""Integrated browser qualification against an explicitly offline demo instance.

No response routes are mocked. This script mutates the selected demo's business
records via the real UI/API and waits for its independent worker. It refuses
live/provider-test hosts. Credentials are read from a private file and never
printed, placed in URLs, or recorded in the report.

Example (start a fresh `serve-demo --seed` instance first):
python tests/ui_integrated_browser.py --base-url http://127.0.0.1:8091 \
  --token-file /private/demo/operator-token --allow-offline-mutations
"""
import argparse
import json
from pathlib import Path
import shutil
import stat
import time
from urllib.parse import urlsplit
import uuid

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--allow-offline-mutations', action='store_true')
    parser.add_argument('--screenshots', help='Optional private directory for explicit demo screenshots')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    parsed = urlsplit(base)
    if parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or parsed.scheme != 'http' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit('Use a loopback HTTP demo instance without URL credentials or parameters.')
    if not args.allow_offline_mutations:
        raise SystemExit('This integrated test requires explicit --allow-offline-mutations.')
    token_path = Path(args.token_file)
    if stat.S_IMODE(token_path.stat().st_mode) & 0o077:
        raise SystemExit('Operator token file must have owner-only permissions.')
    token = token_path.read_text().strip()
    checks = 0
    def check(condition, description):
        nonlocal checks
        if not condition:
            raise AssertionError(description)
        checks += 1
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            page = context.new_page()
            errors, leak_flags = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            def inspect_request(request):
                if token in request.url:
                    leak_flags.append('credential in URL')
                if urlsplit(request.url).path in {'/', '/api/v1/public', '/static/app.js', '/static/style.css'} and request.headers.get('authorization'):
                    leak_flags.append('credential on public request')
            page.on('request', inspect_request)
            public_response = context.request.get(base + '/api/v1/public')
            check(public_response.status == 200, 'actual public projection is available')
            public = public_response.json()
            if public.get('uses_fixtures') is not True or public.get('execution_profile') != 'demo' or public.get('mode') != 'demo':
                raise SystemExit('Refusing mutations: selected instance is not an explicit offline demo.')
            unauthorized = context.request.get(base + '/api/v1/console')
            check(unauthorized.status == 401, 'private projection requires operator auth')
            private = lambda: context.request.get(base + '/api/v1/console', headers={'Authorization': 'Bearer ' + token}).json()
            initial = private()
            original_policy = initial['policy']
            seeded_complete = [order for order in initial['orders'] if order['status'] == 'completed']
            check(bool(seeded_complete), 'seeded actual workflow contains a completed fixture order')
            check(not any(key in json.dumps(public) for key in ['customer@example.invalid', seeded_complete[0]['id']]), 'public projection excludes private customer/order identity')
            page.goto(base)
            page.locator('#offerings .offering').first.wait_for()
            check('fixture' in page.locator('#public-mode').inner_text().lower(), 'public banner identifies offline fixtures')
            check(bool(page.locator('#public-metrics .metric').count()), 'public renders actual financial projection')
            check('No public completion events' not in page.locator('#public-activity').inner_text(), 'seeded actual completion appears in selected public activity')
            page.get_by_role('button', name='Discuss a request', exact=False).first.click()
            page.locator('input[name="topic"]').fill('Explicit public composer fixture')
            page.locator('textarea[name="source_urls"]').fill('https://docs.python.org/3/library/sqlite3.html')
            page.get_by_role('button', name='Prepare email draft', exact=True).click()
            draft = page.locator('a[href^="mailto:"]').get_attribute('href')
            from urllib.parse import parse_qs, unquote
            command = json.loads(parse_qs(urlsplit(draft).query)['body'][0])
            check(unquote(urlsplit(draft).path) == public['contact_email'], 'actual public composer targets configured WideMind mailbox')
            check(command['type'] == 'request' and command['service'] == 'research-brief', 'actual public draft matches normalized Gmail intake contract')
            check(command['scope']['source_urls'] == ['https://docs.python.org/3/library/sqlite3.html'], 'public request preserves structured source URLs')
            page.locator('#close-dialog').click()
            for width in [1440, 1024, 390, 320]:
                page.set_viewport_size({'width': width, 'height': 950})
                check(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'public no overflow at {width}px')
            page.goto(base + '/console')
            def login():
                page.locator('#operator-token').fill(token)
                page.get_by_role('button', name='Connect', exact=False).click()
                page.locator('#workspace').wait_for(state='visible')
            login()
            check(page.locator('#operator-token').input_value() == '', 'input cleared after login')
            check(page.evaluate('localStorage.length === 0 && sessionStorage.length === 0'), 'token not persisted in browser storage')
            check('fixture' in page.locator('#console-mode').inner_text().lower(), 'console banner identifies offline profile')
            for width in [1440, 1024, 390, 320]:
                page.set_viewport_size({'width': width, 'height': 950})
                for view in ['overview', 'opportunities', 'orders', 'finances', 'recovery', 'settings']:
                    page.locator(f'[data-view="{view}"]').click()
                    check(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{view} no overflow at {width}px')
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.locator('[data-view="orders"]').click()
            page.locator('tbody tr').filter(has_text='Completed').first.locator('.table-button').click()
            page.get_by_role('button', name='Download private deliverable').wait_for()
            check('DEMO' in page.locator('#dialog-content .details-code').last.inner_text(), 'real order endpoint supplies explicit fixture artifact')
            with page.expect_download() as download_info:
                page.get_by_role('button', name='Download private deliverable').click()
            check(download_info.value.suggested_filename.endswith('.md'), 'authenticated actual artifact downloads')
            page.locator('#close-dialog').click()
            def runtime(label):
                page.get_by_role('button', name=label, exact=True).click()
                page.locator('input[name="evidence"]').fill('Explicit offline integrated browser qualification')
                page.get_by_role('button', name='Record and schedule', exact=True).click()
                page.locator('#detail-dialog').wait_for(state='hidden')
            if page.locator('#run-state').inner_text() != 'Paused':
                runtime('Pause')
            check(private()['runtime_state'] == 'paused', 'pause persists through actual runtime adapter')
            page.locator('[data-view="settings"]').click()
            marker = 'Explicit fixture browser policy verification ' + uuid.uuid4().hex[:8]
            page.locator('textarea[name="goals"]').fill(marker)
            page.locator('input[name="capabilities"]').fill('')
            page.get_by_role('button', name='Save operating policy').click()
            page.get_by_text('Operating policy saved.', exact=False).wait_for()
            current = private()
            check(current['policy']['goals'] == [marker] and current['policy']['capabilities'] == [], 'goals and capability configuration persist in real storage')
            page.reload()
            check(not page.locator('#workspace').is_visible(), 'browser reload does not retain authentication')
            login()
            page.locator('[data-view="settings"]').click()
            check(page.locator('textarea[name="goals"]').input_value() == marker, 'saved policy survives browser reload')
            page.locator('textarea[name="goals"]').fill('\n'.join(original_policy['goals']))
            page.locator('input[name="capabilities"]').fill(', '.join(original_policy['capabilities']))
            page.get_by_role('button', name='Save operating policy').click()
            page.get_by_text('Operating policy saved.', exact=False).wait_for()
            check(private()['policy'] == original_policy, 'initial policy restored without disturbing other fields')
            page.locator('[data-view="opportunities"]').click()
            page.get_by_role('button', name='Add opportunity', exact=False).click()
            customer = 'ui-fixture-' + uuid.uuid4().hex[:10] + '@example.invalid'
            topic = 'EXPLICIT UI FIXTURE ' + uuid.uuid4().hex[:10]
            page.locator('input[name="customer_email"]').fill(customer)
            page.locator('input[name="topic"]').fill(topic)
            page.locator('textarea[name="source_urls"]').fill('https://docs.python.org/3/library/sqlite3.html')
            page.get_by_role('button', name='Record opportunity', exact=True).click()
            page.locator('#detail-dialog').wait_for(state='hidden')
            opportunity = next(item for item in private()['opportunities'] if item['scope'].get('topic') == topic)
            check(opportunity['status'] == 'received', 'paused worker leaves new opportunity durably queued')
            check(opportunity['scope']['source_urls'] == ['https://docs.python.org/3/library/sqlite3.html'], 'source URLs survive real intake storage')
            runtime('Resume')
            check(private()['runtime_state'] == 'active', 'resume persists through actual runtime adapter')
            def wait_for(predicate, description, limit=38):
                stop = time.monotonic() + limit
                while time.monotonic() < stop:
                    snapshot = private()
                    if predicate(snapshot):
                        return snapshot
                    page.wait_for_timeout(1000)
                raise AssertionError(description)
            snapshot = wait_for(lambda data: any(item['id'] == opportunity['id'] and item['status'] == 'offered' for item in data['opportunities']), 'independent worker did not offer the fixture opportunity')
            check(True, 'independent worker creates and confirms actual fixture offer')
            page.get_by_role('button', name='Refresh', exact=False).click()
            page.get_by_role('button', name=topic, exact=False).first.click()
            page.get_by_role('button', name='Record customer acceptance', exact=True).click()
            check(page.locator('input[name="customer_email"]').input_value() == customer, 'quote acceptance uses matching persisted customer')
            page.locator('input[name="evidence_reference"]').fill('Explicit controlled fixture acceptance from UI qualification')
            page.get_by_role('button', name='Record acceptance', exact=True).click()
            page.locator('#detail-dialog').wait_for(state='hidden')
            snapshot = wait_for(lambda data: any(item['customer_id'] == opportunity['customer_id'] and item['status'] == 'awaiting_payment' for item in data['orders']), 'independent worker did not wait for verified payment')
            order = next(item for item in snapshot['orders'] if item['customer_id'] == opportunity['customer_id'])
            check(order['payment']['earned_micro_usd'] == 0 and order['payment']['receipts_micro_usd'] == 0, 'UI acceptance cannot fabricate payment or earned revenue')
            page.get_by_role('button', name='Refresh', exact=False).click()
            page.locator('[data-view="orders"]').click()
            page.get_by_role('button', name=topic, exact=False).first.click()
            page.get_by_role('button', name='Schedule a wakeup', exact=True).click()
            page.locator('input[name="evidence"]').fill('Explicit fixture recovery wakeup while payment remains pending')
            page.get_by_role('button', name='Record and schedule', exact=True).click()
            page.locator('#detail-dialog').wait_for(state='hidden')
            page.locator('[data-view="recovery"]').click()
            check(page.locator('.data-table tbody tr').count() > 0, 'recovery view shows real durable event records')
            check(next(item for item in private()['orders'] if item['id'] == order['id'])['status'] != 'completed', 'recovery wakeup cannot bypass pending payment')
            runtime('Stop')
            check(private()['runtime_state'] == 'stopped', 'stop state persists independently of webpage')
            runtime('Resume')
            check(private()['runtime_state'] == 'active', 'operator can explicitly resume the stopped fixture run')
            if args.screenshots:
                directory = Path(args.screenshots)
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                page.locator('[data-view="overview"]').click()
                page.screenshot(path=str(directory / 'console-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 320, 'height': 844})
                page.screenshot(path=str(directory / 'console-320.png'), full_page=True)
            page.get_by_role('button', name='Lock console', exact=True).click()
            check(not page.locator('#workspace').is_visible(), 'lock hides authenticated workspace')
            check(page.locator('#view').inner_text() == '' and page.locator('#run-label').inner_text() == '', 'lock clears private view and run identifiers')
            check(page.evaluate('localStorage.length === 0 && sessionStorage.length === 0'), 'lock leaves no browser-stored credentials')
            check(not leak_flags, 'credentials never appear in public URLs or requests')
            check(not errors, 'no JavaScript runtime exceptions during integrated flow')
            browser.close()
        print(json.dumps({'mode': 'explicit-offline-demo', 'fixture_routes': False, 'checks': checks, 'status': 'passed'}))
    except Exception as error:
        detail = str(error).replace(token, '[redacted]')
        print(json.dumps({'mode': 'explicit-offline-demo', 'fixture_routes': False, 'checks': checks, 'status': 'failed', 'error': detail}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
