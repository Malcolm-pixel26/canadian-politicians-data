"""Exercise the public site at a GitHub Pages-style repository subpath."""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
from openpyxl import Workbook
from playwright.sync_api import sync_playwright, expect
from public_site.build import build


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Federal'
        sheet.append(['record_id', 'name', 'status', 'province_territory', 'district_name', 'url', 'last_seen'])
        sheet.append(['alice', 'Alice Example', 'active', 'ON', 'District One', 'https://example.com/alice', '2026-10-25 12:00:00 UTC'])
        sheet.append(['former', 'Bob Former', 'removed', 'QC', 'Former district', 'javascript:alert(1)', '2026-10-24 12:00:00 UTC'])
        for i in range(24):
            sheet.append([str(i), f'Person {i:02}', 'active', 'ON', f'Riding {i}', '', ''])
        sheet.append(['markup', '<img src=x onerror=alert(1)>', 'active', 'BC', 'Markup district', '', ''])
        master = root / 'master.xlsx'
        workbook.save(master)
        boundary = root / 'map.json'
        boundary.write_text(json.dumps({'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'geometry': {'type': 'Polygon', 'coordinates': [[[-80,43],[-79,43],[-79,44],[-80,44],[-80,43]]]}, 'properties': {'district': 'District One', 'province_territory': 'ON'}}]}))
        build(master, boundary, root / 'canadian-politicians-data')
        server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(root)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}/canadian-politicians-data/'
        try:
            with sync_playwright() as p:
                options = {'headless': True}
                if os.environ.get('CHROMIUM_PATH'):
                    options['executable_path'] = os.environ['CHROMIUM_PATH']
                browser = p.chromium.launch(**options)
                context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin)
                expect(page.get_by_role('heading', name='Your country. A closer connection.')).to_be_visible()
                page.get_by_role('button', name='Representatives', exact=True).click()
                expect(page.locator('#result-count')).to_contain_text('26 representatives')
                expect(page.locator('.person-card')).to_have_count(24)
                page.get_by_role('button', name='Next', exact=True).click()
                expect(page.locator('.person-card')).to_have_count(2)
                page.get_by_label('Search', exact=True).fill('Alice')
                page.get_by_role('button', name='Search', exact=True).click()
                expect(page.locator('.person-card')).to_have_count(1)
                page.get_by_role('button', name='Alice Example', exact=True).click()
                expect(page.get_by_role('dialog', name='Alice Example')).to_be_visible()
                shared = page.get_by_label('Share this public profile').input_value()
                assert '/canadian-politicians-data/?person=' in shared
                page.get_by_role('button', name='Save to my watchlist').click()
                page.go_back()
                expect(page.get_by_role('dialog')).to_have_count(0)
                page.go_forward()
                expect(page.get_by_role('dialog')).to_be_visible()
                page.reload()
                expect(page.get_by_role('button', name='Saved · Remove from watchlist')).to_be_visible()
                page.keyboard.press('Escape')
                page.get_by_role('button', name='My watchlist', exact=False).click()
                expect(page.locator('.person-card')).to_have_count(1)
                page.get_by_role('button', name='District map', exact=True).click()
                expect(page.locator('#district-map path')).to_have_count(1)
                page.get_by_role('button', name='Explore District One').focus()
                page.keyboard.press('Enter')
                expect(page.get_by_label('Search', exact=True)).to_have_value('District One')
                expect(page.locator('.person-card')).to_have_count(1)
                mobile = browser.new_context(viewport={'width': 390, 'height': 844})
                friend = mobile.new_page()
                friend.on('pageerror', lambda error: errors.append(str(error)))
                friend.goto(shared)
                expect(friend.get_by_role('dialog', name='Alice Example')).to_be_visible()
                expect(friend.get_by_role('button', name='Save to my watchlist')).to_be_visible()
                friend.keyboard.press('Escape')
                assert friend.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
                friend.get_by_role('button', name='Representatives', exact=True).click()
                friend.get_by_label('Record status').select_option('removed')
                friend.get_by_role('button', name='Search', exact=True).click()
                friend.get_by_role('button', name='Bob Former', exact=True).click()
                expect(friend.get_by_role('dialog')).to_contain_text('no longer listed')
                expect(friend.locator('#profile-source a')).to_have_count(0)
                friend.keyboard.press('Escape')
                friend.get_by_role('button', name='Reset', exact=True).click()
                friend.get_by_label('Search', exact=True).fill('<img')
                friend.get_by_role('button', name='Search', exact=True).click()
                expect(friend.locator('.person-card')).to_have_count(1)
                expect(friend.locator('.person-card img')).to_have_count(0)
                assert not errors, errors
                browser.close()
                print('Public browser checks passed: subpath hosting, search/pagination, public deep links, private device watchlists, map keyboard navigation, mobile layout, unsafe-link rejection and escaped source text.')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
