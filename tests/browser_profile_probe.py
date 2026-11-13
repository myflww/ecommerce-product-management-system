"""Opt-in real-browser profile probe, entirely synthetic and offline.

Run with the repository Python. It uses a headless installed Edge/Chrome and
fulfills every document locally; it never opens a real shop or an existing
browser profile. A synthetic login cookie must survive an engine close/reopen.
"""
from __future__ import annotations

import json
from decimal import Decimal
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import browser_price_engine as engine

FIXTURE = 'https://midoo-profile-fixture.example/g-12345'
DOCUMENT = '<html><head><title>Offline SKU fixture</title></head><body><main><h1>SKU-RED</h1><p class="price">Current price USD 15.20</p></main></body></html>'


def wait_state(owner, job, states, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = engine.snapshot(owner, job['id'])
        if value['state'] in states:
            return value
        if value['state'] == 'error':
            raise AssertionError(value['message'])
        time.sleep(.05)
    raise AssertionError('Synthetic browser fixture timed out')


def main():
    original_factory = engine._playwright_factory
    seen = []

    class HeadlessProbe:
        def __enter__(self):
            self.manager = original_factory()
            actual = self.manager.__enter__()
            def launch(**options):
                # Production still opens a visible sandboxed browser. Only
                # this isolated program removes the window for the probe.
                options['headless'] = True
                return actual.chromium.launch_persistent_context(**options)
            return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))

        def __exit__(self, *args):
            return self.manager.__exit__(*args)

    def route(request):
        if not request.request.url.startswith(FIXTURE):
            request.abort('blockedbyclient')
            return
        login = 'fixture_login=1' in request.request.url
        authenticated = 'fixture_login=1' in request.request.headers.get('cookie', '')
        seen.append((login, authenticated))
        headers = {'Content-Type': 'text/html; charset=utf-8'}
        if login:
            headers['Set-Cookie'] = 'fixture_login=1; Max-Age=3600; Path=/; Secure; SameSite=Lax'
        request.fulfill(status=200, headers=headers,
                        body=DOCUMENT if login or authenticated else '<html><body>Please sign in to view price</body></html>')

    def guard(url):
        if urlsplit(url).hostname != 'midoo-profile-fixture.example':
            raise RuntimeError('Offline profile probe refused a non-fixture URL')

    engine._playwright_factory = HeadlessProbe
    engine._safe_route = route
    engine._network_guard = guard
    qa = ROOT / 'qa-data'
    qa.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='browser-profile-probe-', dir=qa) as folder:
        assert Path(folder).resolve().is_relative_to(qa.resolve())
        engine.init_engine(Path(folder))
        target = {'listing_id':'synthetic-red','source_url':FIXTURE + '?fixture_login=1',
                  'currency':'USD','region':'','variant_hint':'SKU-RED','revision':1,
                  'platform':'Fixture','store':'Fixture shop','_profile_user':'synthetic-user'}
        try:
            first = engine.start('session-one', target)
            wait_state('session-one', first, {'ready'})
            engine.close('session-one', first['id'])
            assert engine._jobs[first['id']]['_done'].wait(35)
            target['source_url'] = FIXTURE
            second = engine.start('session-two', target)
            wait_state('session-two', second, {'ready'})
            engine.read('session-two', second['id'])
            quote = wait_state('session-two', second, {'preview', 'needs_action'})
            assert quote['state'] == 'preview', quote['message']
            assert any(Decimal(item['price']) == Decimal('15.20') and item['currency'] == 'USD' for item in quote['candidates']), quote['candidates']
            assert seen[0] == (True, False) and seen[-1] == (False, True)
            engine.close('session-two', second['id'])
            assert engine._jobs[second['id']]['_done'].wait(35)
            print(json.dumps({'ok':True, 'synthetic_cookie_survived_restart':True,
                              'new_web_session_reuses_profile':True, 'quote_from_reopened_browser':'USD 15.20',
                              'real_shop_requests':0, 'real_credentials_used':False}))
        finally:
            engine.shutdown()


if __name__ == '__main__':
    main()
