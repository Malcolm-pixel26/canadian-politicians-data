# canadian-politicians-data

Scripts that pull public data on Canadian elected officials (federal MPs,
provincial/territorial legislators, and municipal councillors and mayors)
into Excel/CSV/JSON. They keep a running history in a master workbook
and total up federal campaign contributions per politician.

Data comes from free, public, no-key APIs and open data:

- [Represent](https://represent.opennorth.ca/) (Open North) aggregates
  officials at every level of government, so these scripts don't need
  to scrape each legislature's website. It currently covers the House of
  Commons, 12 of the 13 provincial/territorial legislatures (there is no
  Nunavut set) and 108 municipal councils, about 3,800 people in all.
- [OpenParliament](https://openparliament.ca/api/) provides each MP's
  first elected date and profile link.
- [House of Commons open data](https://www.ourcommons.ca/en/open-data)
  provides each MP's current committee memberships.
- [Elections Canada open data](https://www.elections.ca/content.aspx?section=fin&dir=oda&document=index&lang=e)
  provides federal campaign contributions.

Requires Python 3.8+ and one package for Excel output:

```bash
pip install openpyxl
```

(Everything else uses only the Python standard library.)

Prefer a double-clickable .exe instead of running Python scripts? See
[BUILD_EXES.md](BUILD_EXES.md). `cli_politicians.py` and
`cli_budgets.py` bundle everything below into two Windows executables.

What changed in this version, and the evidence behind each change, is
written up in [docs/IMPROVEMENTS.md](docs/IMPROVEMENTS.md).

## Files

- `represent_common.py`: shared code. It talks to the APIs (throttled,
  with retries), classifies each legislature/council by level of
  government, writes CSV/JSON/XLSX, and manages the persistent master
  workbook and changelog.
- `openparliament_enrich.py`: adds first elected date and OpenParliament
  profile URL (from OpenParliament) and current committee memberships
  (from the House of Commons) to federal MPs.
- `scrape_federal.py`: Members of Parliament (House of Commons).
- `scrape_provincial_territorial.py`: MPPs/MLAs/MNAs/MHAs for every
  provincial/territorial legislature Represent covers.
- `scrape_local.py`: municipal councillors and mayors.
- `scrape_all.py`: runs all three, one after another.
- `lookup_politician.py`: look up one named politician on demand,
  without a full scrape.
- `scrape_budget_federal.py`: federal campaign contribution totals per
  politician, from Elections Canada's public political financing data.
  Writes the **Federal** tab of `MASTER_CANADA_POLITICIAN_BUDGET.xlsx`.
- `scrape_budget_provincial_territorial.py`: same idea for
  provincial/territorial politicians. Writes the
  **Provincial_Territorial** tab. The tab and matching plumbing are in
  place, but no jurisdiction's data source is wired in yet (see the
  script's own docstring for why, and where to plug one in).
- `scrape_budget_local.py`: same idea for local (municipal)
  politicians. Writes the **Local** tab, with the same "plumbing ready,
  no source wired in yet" status as the provincial script.
- `scrape_all_budgets.py`: runs all three budget scripts, one after
  another, updating all three tabs of `MASTER_CANADA_POLITICIAN_BUDGET.xlsx`
  in one go.
- `budget_common.py`: the column layout and matching logic shared by
  the three budget scripts.
- `tests/`: automated tests (see [Development](#development)).

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

Every script accepts `--help`, and all of them accept
`--output-dir DIR` to write somewhere other than your Desktop (see
[Output](#output)). Mistyped options are rejected with an error rather
than silently ignored.

| Command | What it does |
|---|---|
| `python3 scrape_federal.py --no-enrich` | skip the OpenParliament / House of Commons extras |
| `python3 scrape_local.py --filter Toronto` | only councils whose name contains "Toronto" |
| `python3 scrape_local.py --province QC` | only councils in one province/territory (two-letter code) |
| `python3 scrape_all.py --province ON --no-enrich` | `--filter`/`--province` go to the local scraper, `--no-enrich` to the federal one |

A full run is quick. The local scraper downloads all ~3,800
representatives in a handful of requests, so it finishes in seconds
rather than the 10-20+ minutes it used to take. The federal scraper
spends a few minutes fetching one OpenParliament record and one House of
Commons record per MP, in parallel and politely rate-limited.
`--no-enrich` skips that step.

## Building standalone .exe files (Windows)

`cli_politicians.py` and `cli_budgets.py` bundle the scripts above into
two dispatcher programs. You pick which underlying script runs with a
subcommand, and everything after that is passed straight through.
Compiled with PyInstaller (see [BUILD_EXES.md](BUILD_EXES.md) for the
one-time setup and build command), they become two standalone .exe
files that don't need Python installed to run:

```
politician_scrapers.exe federal
politician_scrapers.exe federal --no-enrich
politician_scrapers.exe provincial
politician_scrapers.exe local
politician_scrapers.exe local --filter Toronto
politician_scrapers.exe local --province BC
politician_scrapers.exe all
politician_scrapers.exe lookup "Jane Doe"
politician_scrapers.exe lookup "Jane Doe" --live --category federal

budget_scrapers.exe federal
budget_scrapers.exe federal --refresh
budget_scrapers.exe federal --since 2015
budget_scrapers.exe federal --reviewed
budget_scrapers.exe provincial
budget_scrapers.exe provincial --province ontario
budget_scrapers.exe local
budget_scrapers.exe local --municipality toronto
budget_scrapers.exe all
```

Run either exe with no arguments to print this list again, or add
`--help` after a subcommand to see its options. Every flag here works
exactly the same as it does when running the `.py` files directly with
`python3`. Both exes exit with a non-zero status if a run fails, so
they can be scheduled and monitored.

## Output

By default, per-category snapshots are written to a folder called
`output` on your Desktop. On Windows this is the Desktop Explorer
actually shows, including one that OneDrive has moved. Use
`--output-dir DIR`, or set the `CA_POLITICIANS_OUTPUT_DIR` environment
variable, to put it anywhere else.

```
~/Desktop/output/
  master.xlsx                        <- persistent, updated every run (see below)
  master.backup.xlsx                 <- the previous version of master.xlsx
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
    quebec-assemblee-nationale.xlsx
    quebec-assemblee-nationale.csv
    ...
  local/
    all_local.xlsx
    all_local.csv
    all_local.json
```

Each `.xlsx` has a bold header row, frozen top row, autofilter, and
auto-sized columns, so it's ready to open and sort/filter in Excel
without any cleanup. CSV and JSON versions are included too in case
you want to pipe the data into another tool. Files are written to a
temporary file first and then swapped in, so an interrupted run never
leaves a half-written file behind. If a file is open in Excel (which
locks it on Windows), you're asked to close it and retry; when nobody
is at the keyboard, the new data is saved next to it as
`<name> (unsaved <timestamp>).xlsx` instead of being lost.

Each row includes the following, where available (not every
jurisdiction publishes every field):

| Column | Notes |
|---|---|
| `category`, `representative_set` | level of government, and which legislature/council |
| `name`, `first_name`, `last_name`, `elected_office`, `district_name`, `party_name` | |
| `email`, `phone`, `postal_address` | the legislature/city-hall office |
| `url`, `personal_url`, `photo_url`, `gender`, `source_url` | `url` is the person's page on the legislature's own site |
| `province_territory` | two-letter code, for every level of government (council names alone are ambiguous; there are Richmond and Stratford councils in different provinces) |
| `constituency_phone`, `constituency_address` | the local office constituents actually contact. For MPs, `phone`/`postal_address` are the Ottawa office, which is the same for all 343 of them |
| `roles`, `preferred_languages` | e.g. "Parliamentary Secretary to the Prime Minister"; MPs' official languages |
| `extra_info` | anything else Represent publishes for the person, e.g. a constituency email |
| `first_elected`, `committees`, `openparliament_url` | federal only (see [Notes](#notes--things-to-know)) |

New columns are added at the end, so existing column positions in the
CSVs never move.

## The master file (`master.xlsx`)

Unlike the per-category files (which are just a fresh snapshot each
run), `master.xlsx` is a single workbook that's **updated in place**
every time you run any of the scrapers, so it accumulates history
instead of being overwritten. It has one tab per level of government,
plus a shared changelog:

- **Federal / Provincial_Territorial / Local sheets:** one row per
  person for that level of government, with `first_seen`, `last_seen`,
  `status` (`active`/`removed`) and a permanent `record_id`. People who
  disappear from a re-scraped category (e.g. after an election) are
  marked `removed` rather than deleted, so you don't lose the record. If
  they come back later, the same row is reactivated.
- **Changelog sheet:** a shared, append-only log of every
  add/update/removal/reactivation from every run across all three tabs,
  newest first. It records who changed, what field, old value -> new
  value, and when, including fields that went blank (e.g. an email
  address that was removed).

Each scraper only ever touches its own tab (`scrape_federal.py` only
reads/writes the `Federal` tab, `scrape_local.py` only touches `Local`,
and so on), so the three scrapers can run in any order, as often as you
like, without ever clobbering each other's data.

How people are tracked across runs: Represent has no stable person ID,
and its `url` field (the person's page on the legislature's website) is
blank for many councillors and changes whenever a site is redesigned.
So a fresh scrape is matched to existing rows by the same URL, then the
same name, then the same seat (district + office) and last name, each
only when that's unambiguous. A council that starts publishing profile
pages, a legislature website redesign, or a "Bill" becoming "William"
is recorded as an update to the same person instead of everyone being
"removed" and re-"added" with their history reset.

The scrapers are careful about what counts as a removal:

- With `--filter`/`--province`, only the councils actually scraped that
  run are checked for removals.
- If a council fails to download, its people are left alone.
- If a council downloads fine but comes back with nobody in it, that's
  treated as an upstream glitch rather than a mass resignation, and a
  warning is printed.

Workbooks written by older versions of these scripts are read by column
header, so they keep working. New columns are filled in on the next run
without flooding the changelog. The previous version of `master.xlsx`
is kept as `master.backup.xlsx`.

## `lookup_politician.py`: look up one person on demand

```bash
# Fast: searches your already-scraped master.xlsx
python3 lookup_politician.py "Jane Doe"

# Live: queries Represent directly, every level of government at once (a few requests)
python3 lookup_politician.py "Jane Doe" --live

# Optionally narrow it
python3 lookup_politician.py "Jane Doe" --live --category federal
python3 lookup_politician.py "Jane Doe" --live --category local --place Toronto
```

Names match on word prefixes in any order, ignoring accents and case.
"Legault", "francois leg" and "LEGAULT François" all find François
Legault; "Ann" finds Ann or Anne but not Joanne. Federal matches also
get OpenParliament / House of Commons enrichment pulled live
(committees, first elected date), since that's cheap for a single
person (`--no-enrich` skips it).

This only pulls from Represent, OpenParliament and the House of
Commons. All three publish these people's official, public-facing
legislative contact info as part of their role as elected officials.
It doesn't search social media, news coverage, or anything about a
person's private life.

## Budget scripts: campaign funding per politician

Three scripts, mirroring the federal/provincial-territorial/local split
of the politician scrapers, each write their own tab of the shared
`~/Desktop/output/MASTER_CANADA_POLITICIAN_BUDGET.xlsx` workbook:

| Script | Tab it writes | Coverage |
|---|---|---|
| `scrape_budget_federal.py` | `Federal` | Fully implemented, from Elections Canada Open Data |
| `scrape_budget_provincial_territorial.py` | `Provincial_Territorial` | Plumbing ready, no jurisdiction wired in yet |
| `scrape_budget_local.py` | `Local` | Plumbing ready, no municipality wired in yet |

Run any of them independently, in any order, as often as you like.
Each only touches its own tab (and its own row on the workbook's
`Notes` tab), so re-running one never wipes out the others. Or run all
three at once:

```bash
python3 scrape_all_budgets.py
```

Each row has the same columns regardless of tab, so they're easy to
compare/sort side by side: `name`, `category`, `party_name`,
`representative_set`, `province_territory`, `recipient_type`,
`total_contributions` (monetary), `non_monetary_contributions`
(in-kind), `contribution_count`, `avg_contribution`, `date_range`,
`electoral_districts`, `match_status`. Rows are matched against your
existing `master.xlsx`, so run the politician scrapers first.

### `scrape_budget_federal.py`

```bash
python3 scrape_budget_federal.py                # uses a cached copy if <7 days old
python3 scrape_budget_federal.py --refresh       # force a fresh download
python3 scrape_budget_federal.py --since 2019    # only elections / fiscal periods dated 2019 or later
python3 scrape_budget_federal.py --reviewed      # Elections Canada's corrected ("as reviewed") dataset
```

Covers **federal** political financing, from Elections Canada's public
Open Data (contributions to candidates, parties, electoral district
associations and leadership/nomination contestants since 2004, updated
weekly upstream). Amounts are **contributions received** (fundraising)
as publicly disclosed under the Canada Elections Act, not government
office budgets or spending.

Matching: Elections Canada's `Recipient` column is written "Last,
First" ("Whelan, Susan"), so matching on it directly never found
anyone. The recipient first/last-name columns are used instead. If the
full name doesn't match, the first word of the first name plus the last
name is tried when that's unambiguous, so "Jasraj Hallan" still finds
"Jasraj Singh Hallan". Such matches say `matched (first + last name
only -- check)`. A person's contributions as a candidate, leadership
contestant and nomination contestant are combined, and `recipient_type`
says which. A match whose current riding isn't among the districts they
received contributions in is flagged `riding differs -- check`, which
catches a different person with the same name. Recipients that match
no one (parties, associations, past candidates) are listed too, so no
funding data silently disappears.

The default "as submitted" file is ~158 MB compressed; `--reviewed`
uses the ~80 MB "as reviewed" file, which includes Elections Canada's
corrections but can lag behind recent filings. Either is cached under
`~/Desktop/output/_cache/` and only re-downloaded if it's more than a
week old (or you pass `--refresh`); a failed or truncated download is
retried and never cached. Column names are matched by their normalized
text (see `FIELD_CANDIDATES` in `scrape_budget_federal.py`). If
Elections Canada ever renames a column the script needs, it stops and
prints the file's real header instead of silently summing the wrong
column.

### `scrape_budget_provincial_territorial.py` and `scrape_budget_local.py`

Unlike the federal government, there's no single, unified source for
either of these. Each of the 10 provinces + 3 territories runs its own
elections body with its own campaign-finance disclosure system, and
most don't offer a bulk/CSV download the way Elections Canada does.
Several (e.g. Elections Ontario) only publish contributions through a
paginated, JavaScript-rendered search tool with no underlying data
file. Municipal campaign finance is even less standardized: most of
Canada's several hundred municipalities don't publish structured
contribution data at all.

So both scripts currently write their tab with every matching
politician from `master.xlsx` listed as `not covered`, ready to switch
to real numbers as soon as a jurisdiction-specific fetcher is added.
Each has an extension point at the top of the file,
`PROVINCE_FETCHERS` / `MUNICIPAL_FETCHERS`: a dict you add a function
to (returning the "contribution totals" shape described in
`budget_common.py`) to plug in a specific province, territory, or
municipality's real data once you've found and verified its actual
data source. Once a fetcher is added you can run just that one with
`--province <slug>` or `--municipality <slug>`.

## Notes / things to know

- **Rate limits:** Represent's free tier allows 60 requests/minute
  (HTTP 503 past that). Every request goes through one throttled
  function (~54/minute to Represent), transient failures are retried
  with backoff, and a full scrape needs only a handful of requests
  anyway. If a source is unreachable altogether, the scripts give up
  after a few quick attempts and say so, instead of retrying for hours.
- **Coverage:** Represent currently has no Nunavut legislature, and
  `scrape_provincial_territorial.py` prints any jurisdiction it can't
  find. It covers 108 municipal councils, mostly larger cities and
  their regions.
- **Federal enrichment:** `scrape_federal.py` adds it by default; pass
  `--no-enrich` to skip it (faster, or if you don't have network access
  to openparliament.ca / ourcommons.ca). Committees come from the House
  of Commons' own per-MP data, joined by the MP's official person ID
  (Represent's `url` for an MP is their ourcommons.ca profile).
  OpenParliament is matched by name, then by riding + last name,
  and each match is confirmed against the same person ID. An MP who
  can't be matched just gets blank enrichment fields. A later
  `--no-enrich` run keeps the enrichment already in `master.xlsx`. To
  see OpenParliament's raw data for one MP, run
  `python3 -c "from openparliament_enrich import debug_dump_one; debug_dump_one('Some MP Name')"`.
- **Data quality:** Represent pulls from each government's own open
  data/scrapers, so freshness varies by jurisdiction. A few smaller
  municipalities may be out of date or missing. Check `source_url` in
  the output if you need to verify a specific entry.
- **Classification logic:** the API doesn't label each legislature
  with a "level of government" field, so `represent_common.py`
  classifies federal/provincial/local by keyword matching on the
  legislature's name (e.g. "Legislature"/"Legislative Assembly" =
  provincial, "Council"/"Conseil" = municipal). This correctly
  classifies all 121 sets Represent currently publishes (a test checks
  that), but if Open North renames something unusually, it's worth
  spot-checking the output.
- **Scope:** all data here is official, public contact/office
  information published by Represent, OpenParliament and the House of
  Commons as part of these people's public role as elected officials,
  plus federal contribution totals published by Elections Canada. It is
  not personal information about their private lives.

## Development

The tests use only the standard library (plus openpyxl), run offline in
about two seconds, and use trimmed copies of real API responses in
`tests/fixtures/`:

```bash
python3 -m unittest discover -s tests -v
```

The network and the clock are faked (`tests/support.py`), so the tests
check rate limiting and retry timing without waiting in real time.
