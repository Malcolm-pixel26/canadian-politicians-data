# Canadian Politician Scrapers

Pulls data on Canadian elected officials from the [Represent API](https://represent.opennorth.ca/)
(built and maintained by Open North), which is a free, public API that
already aggregates federal, provincial/territorial, and municipal
elected officials across Canada — so these scripts don't need to scrape
each legislature's website individually. Federal MPs are additionally
enriched with public data from the [OpenParliament API](https://openparliament.ca/api/)
(committee memberships, first-elected date).

Requires Python 3 and one package for Excel output:

```bash
pip install openpyxl
```

(Everything else uses only the Python standard library.)

Prefer a double-clickable .exe instead of running Python scripts? See
[BUILD_EXES.md](BUILD_EXES.md) -- `cli_politicians.py` and
`cli_budgets.py` bundle everything below into two Windows executables.

## Files

- `represent_common.py` — shared code: talks to the API, classifies
  each legislature/council by level of government, writes CSV/JSON,
  and manages the persistent master workbook + changelog.
- `openparliament_enrich.py` — adds committee memberships / first
  elected date to federal MPs by matching names against OpenParliament.
- `scrape_federal.py` — Members of Parliament (House of Commons).
- `scrape_provincial_territorial.py` — MPPs/MLAs/MNAs/MHAs for all 10
  provinces + 3 territories.
- `scrape_local.py` — municipal councillors and mayors (several
  hundred councils; slowest script).
- `scrape_all.py` — runs all three, one after another.
- `lookup_politician.py` — look up one named politician on demand,
  without a full scrape.
- `scrape_budget_federal.py` — federal campaign contribution totals per
  politician, from Elections Canada's public political financing data.
  Writes the **Federal** tab of `MASTER_CANADA_POLITICIAN_BUDGET.xlsx`.
- `scrape_budget_provincial_territorial.py` — same idea for
  provincial/territorial politicians. Writes the
  **Provincial_Territorial** tab. Ships with the tab/matching plumbing
  in place but no jurisdiction's data source wired in yet (see the
  script's own docstring for why, and where to plug one in).
- `scrape_budget_local.py` — same idea for local (municipal)
  politicians. Writes the **Local** tab. Same "plumbing ready, no
  source wired in yet" status as the provincial script.
- `scrape_all_budgets.py` — runs all three budget scripts, one after
  another, updating all three tabs of `MASTER_CANADA_POLITICIAN_BUDGET.xlsx`
  in one go.

## Usage

Keep all files in the same folder, then from a terminal in that folder:

```bash
python3 scrape_federal.py
python3 scrape_provincial_territorial.py
python3 scrape_local.py
```

or run everything at once:

```bash
python3 scrape_all.py
```

## Building standalone .exe files (Windows)

`cli_politicians.py` and `cli_budgets.py` bundle the scripts above into
two dispatcher programs -- pick which underlying script runs with a
subcommand, everything after that is passed straight through. Compiled
with PyInstaller (see [BUILD_EXES.md](BUILD_EXES.md) for the one-time
setup and build command), they become two standalone .exe files that
don't need Python installed to run:

```
politician_scrapers.exe federal
politician_scrapers.exe federal --no-enrich
politician_scrapers.exe provincial
politician_scrapers.exe local
politician_scrapers.exe local --filter Toronto
politician_scrapers.exe all
politician_scrapers.exe lookup "Jane Doe"
politician_scrapers.exe lookup "Jane Doe" --live --category federal

budget_scrapers.exe federal
budget_scrapers.exe federal --refresh
budget_scrapers.exe federal --since 2015
budget_scrapers.exe provincial
budget_scrapers.exe provincial --province ontario
budget_scrapers.exe local
budget_scrapers.exe local --municipality toronto
budget_scrapers.exe all
```

Run either exe with no arguments to print this list again. Every flag
here works exactly the same as it does when running the `.py` files
directly with `python3`.

## Output

Per-category snapshots are written to a folder called `output` on your
Desktop:

```
~/Desktop/output/
  master.xlsx                        <- persistent, updated every run (see below)
  MASTER_CANADA_POLITICIAN_BUDGET.xlsx  <- funding data, one tab per level of government (see below)
  federal/
    federal_mps.xlsx
    federal_mps.csv
    federal_mps.json
  provincial_territorial/
    all_provincial_territorial.xlsx
    all_provincial_territorial.csv
    all_provincial_territorial.json
    ontario-legislature.xlsx         (one file per legislature)
    ontario-legislature.csv
    quebec-national-assembly.xlsx
    quebec-national-assembly.csv
    ...
  local/
    all_local.xlsx
    all_local.csv
    all_local.json
```

Each `.xlsx` has a bold header row, frozen top row, autofilter, and
auto-sized columns, so it's ready to open and sort/filter in Excel
without any cleanup. CSV and JSON versions are included too in case
you want to pipe the data into another tool.

Each row includes: name, elected office/title, district, party,
email, phone, mailing address, and profile URL, where available (not
every jurisdiction publishes every field).

## The master file (`master.xlsx`)

Unlike the per-category files (which are just a fresh snapshot each
run), `master.xlsx` is a single workbook that's **updated in place**
every time you run any of the scrapers, so it accumulates history
instead of being overwritten. It has one tab per level of government,
plus a shared changelog:

- **Federal / Provincial_Territorial / Local sheets** — one row per
  person for that level of government, with `first_seen`, `last_seen`,
  and `status` (`active`/`removed`) columns. People who disappear from
  a re-scraped category (e.g. after an election) are marked `removed`
  rather than deleted, so you don't lose the record.
- **Changelog sheet** — a shared, append-only log of every
  add/update/removal from every run across all three tabs, newest
  first: who changed, what field, old value -> new value, and when.

Each scraper only ever touches its own tab — `scrape_federal.py` only
reads/writes the `Federal` tab, `scrape_local.py` only touches `Local`,
etc. — so the three scrapers can run in any order, as often as you
like, without ever clobbering each other's data. Same logic applies to
`scrape_local.py --filter <name>`: only the councils actually scraped
that run are checked for removals within the `Local` tab.

## `lookup_politician.py` — look up one person on demand

```bash
# Fast: searches your already-scraped master.xlsx
python3 lookup_politician.py "Jane Doe"

# Live: skips the cache and queries Represent directly (must narrow scope)
python3 lookup_politician.py "Jane Doe" --live --category federal
python3 lookup_politician.py "Jane Doe" --live --category local --place Toronto
```

Federal matches also get OpenParliament enrichment pulled live (committee
memberships, first elected date), since that's cheap for a single person.

This only pulls from Represent and OpenParliament — both publish these
people's official, public-facing legislative contact info as part of
their role as elected officials. It doesn't search social media, news
coverage, or anything about a person's private life.

## Budget scripts — campaign funding per politician

Three scripts, mirroring the federal/provincial-territorial/local split
of the politician scrapers, each one writing its own tab of the shared
`~/Desktop/output/MASTER_CANADA_POLITICIAN_BUDGET.xlsx` workbook:

| Script | Tab it writes | Coverage |
|---|---|---|
| `scrape_budget_federal.py` | `Federal` | Fully implemented — Elections Canada Open Data |
| `scrape_budget_provincial_territorial.py` | `Provincial_Territorial` | Plumbing ready, no jurisdiction wired in yet |
| `scrape_budget_local.py` | `Local` | Plumbing ready, no municipality wired in yet |

Run any of them independently, in any order, as often as you like —
each only touches its own tab (and its own row on the workbook's
`Notes` tab), so re-running one never wipes out the others. Or run all
three at once:

```bash
python3 scrape_all_budgets.py
```

Each row has the same columns regardless of tab, so they're easy to
compare/sort side by side: `name`, `category`, `party_name`,
`representative_set`, `recipient_type`, `total_contributions`,
`contribution_count`, `avg_contribution`, `match_status`. Rows are
matched against your existing `master.xlsx` by name.

### `scrape_budget_federal.py`

```bash
python3 scrape_budget_federal.py                # uses a cached copy if <7 days old
python3 scrape_budget_federal.py --refresh       # force a fresh download
python3 scrape_budget_federal.py --since 2019    # only count contributions from 2019 on
```

Covers **federal** political financing, from Elections Canada's public
Open Data (contributions to candidates, parties, and electoral
district associations since 2004, updated weekly upstream). Amounts
are **contributions received** (fundraising) as publicly disclosed
under the Canada Elections Act — not government office budgets or
spending.

The source file is ~150+ MB compressed, so it's cached locally under
`~/Desktop/output/_cache/` and only re-downloaded if it's more than a
week old (or you pass `--refresh`). Elections Canada doesn't publish a
formal schema doc for this CSV, so column matching is done defensively
by keyword (see `FIELD_CANDIDATES` in `scrape_budget_federal.py`) — the
script prints the actual header it found each run, so you can add the
real column name there if a future format change breaks the match.

### `scrape_budget_provincial_territorial.py` and `scrape_budget_local.py`

Unlike the federal government, there's no single, unified source for
either of these. Each of the 10 provinces + 3 territories runs its own
elections body with its own campaign-finance disclosure system, and
most don't offer a bulk/CSV download the way Elections Canada does —
several (e.g. Elections Ontario) only publish contributions through a
paginated, JavaScript-rendered search tool with no underlying data
file. Municipal campaign finance is even less standardized: most of
Canada's several hundred municipalities don't publish structured
contribution data at all.

So both scripts currently write their tab with every matching
politician from `master.xlsx` listed as `not covered`, ready to switch
to real numbers as soon as a jurisdiction-specific fetcher is added.
Each has an extension point at the top of the file —
`PROVINCE_FETCHERS` / `MUNICIPAL_FETCHERS` — a dict you add a function
to (same input/output shape as `aggregate_contributions()` in
`scrape_budget_federal.py`) to plug in a specific province, territory,
or municipality's real data once you've found and verified its actual
data source. Once a fetcher is added you can run just that one with
`--province <slug>` or `--municipality <slug>`.

## Notes / things to know

- **Rate limits:** Represent's free tier allows 60 requests/minute.
  These scripts throttle themselves to stay under that, so
  `scrape_local.py` in particular can take 10–20+ minutes since it has
  to query several hundred municipal councils.
- **Filtering local results:** you can narrow `scrape_local.py` to
  councils whose name matches a keyword, e.g.
  `python3 scrape_local.py --filter Toronto`.
- **Federal enrichment:** `scrape_federal.py` fetches OpenParliament
  data by default; pass `--no-enrich` to skip it (faster, or if you
  don't have network access to openparliament.ca). Matching is by
  normalized name, so a rare nickname/spelling mismatch may come back
  with blank enrichment fields rather than crashing the run.
  OpenParliament's exact JSON field names for committee memberships
  aren't formally documented; if enrichment fields come back
  consistently empty, run
  `python3 -c "from openparliament_enrich import debug_dump_one; debug_dump_one('Some MP Name')"`
  to see the raw response and adjust `_extract_membership_info` in
  `openparliament_enrich.py` if the shape has changed.
- **Data quality:** Represent pulls from each government's own open
  data/scrapers, so freshness varies by jurisdiction — a few smaller
  municipalities may be out of date or missing. Check `source_url` in
  the output if you need to verify a specific entry.
- **Classification logic:** the API doesn't label each legislature
  with a "level of government" field, so `represent_common.py`
  classifies federal/provincial/local by keyword matching on the
  legislature's name (e.g. "Legislature"/"Legislative Assembly" =
  provincial, "Council"/"Conseil" = municipal). This is reliable in
  practice but if Open North renames something unusually, it's worth
  spot-checking the output.
- **Scope:** all data here is official, public contact/office
  information published by Represent and OpenParliament as part of
  these people's public role as elected officials — not personal
  information about their private lives.
