# Civic Circle — public GitHub Pages edition

A public Canadian representative directory with search, filters, shareable profiles, a district map and browser-local watchlists. No sign-in, API key, backend server or paid hosting subscription is required. The application uses local assets and published JSON; it does not call upstream data providers from visitors' browsers.

## Publish on GitHub Pages

1. Merge the public-site pull request into `main` after its checks pass.
2. Open the repository's **Settings → Pages** and set **Source** to **GitHub Actions**.
3. Open **Actions → Civic Circle public website → Run workflow** on `main`.
4. The deployment job reports the live URL after a successful deployment. For this repository, the expected address is `https://malcolm-pixel26.github.io/canadian-politicians-data/`. That address is not live merely because the source files exist.

Future changes to `public_site/` on `main` rebuild and deploy automatically. Use **Run workflow** to refresh the data without a code change. There is no scheduled collection job. Pull requests run tests but never publish.

The workflow collects federal MPs and senators, builds a federal district map, validates and allowlists public fields, and uploads the static bundle. Both **House of Commons** and **Senate of Canada** must contain active records. Invalid data or missing required groups fail the build; the previous successful Pages deployment stays in place. This is a coverage check, not a guarantee that every individual source record is current.

## Run locally

From the repository root, with Python 3.9+:

```bash
pip install openpyxl
python scrape_federal.py --no-enrich --output-dir /tmp/civic-source
python make_maps.py --level federal --output-dir /tmp/civic-source
python -m public_site.build --master /tmp/civic-source/master.xlsx --map /tmp/civic-source/maps/federal_map.geojson --output public-dist --require-set 'House of Commons' --require-set 'Senate of Canada'
python -m http.server 8000 --directory public-dist --bind 127.0.0.1
```

Open `http://127.0.0.1:8000`. Serve the files over HTTP; opening the HTML directly from disk blocks JSON fetching in many browsers. The optional `--map` argument can be omitted when no boundaries are available; the map page states that clearly.

The builder can also read a reviewed master workbook containing provincial or local tabs. The initial GitHub workflow deliberately collects federal data only. Zero regional or government-level counts mean **not present in this published dataset**, not an absence of representatives in Canada.

## Privacy and limits

- Profiles contain official public-role information from the collector. The builder uses an explicit field allowlist; account details, internal notes and arbitrary future workbook columns are not exported.
- Watchlists stay in `localStorage` in the visitor's browser. They are not uploaded, publicly shared, or synchronized between devices. Clearing browser data removes them. Browser profiles shared by multiple people also share this watchlist.
- A shared profile URL identifies only the representative. It does not contain a watchlist or grant account access.
- If storage is unavailable, a watchlist works only for that visit and the interface says so.
- Historical records can be displayed if included in the supplied workbook. A fresh scheduled or manual collection into an empty output directory does not reconstruct past history. Saved IDs missing from a newer dataset remain saved and are reported as absent.
- This edition does not include server-backed accounts, admin uploads, voting histories or financial records. Those require a separate backend or additional publication pipeline.
- Publication time is shown separately from each profile's last-observed value. Data providers can be incomplete or stale. Verify important information with the source link.
- Geographic boundaries may include northern waters and may not match every representative. The directory remains usable when map coverage is incomplete.

## Tests

```bash
python -m unittest public_site.test_build -v
node --check public_site/app.js
pip install playwright
python -m playwright install chromium
python -m public_site.browser_smoke
```

Set `CHROMIUM_PATH=/path/to/chromium` to use an installed browser. The browser test uses temporary synthetic records, serves the app under a repository subpath, and removes its data afterward. It checks public deep links, back/forward navigation, mobile layout, literal source text, unsafe links, search/pagination, keyboard map access and watchlist isolation between browsers.

See [the public-launch decision graph](../docs/PUBLIC_LAUNCH_DECISIONS.md) for the architecture choices and release gates. Application code follows the repository license; upstream data remains subject to each provider's terms.
