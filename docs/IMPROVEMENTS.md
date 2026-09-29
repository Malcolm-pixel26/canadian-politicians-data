# Improvements to the politician scrapers: a Graph-of-Thoughts review

This documents how the v4 scripts (commit `f312d68`, imported verbatim
from `ca-politician-scrapers-fixedv4.zip`) were improved, and the
evidence behind every change.

**Method.** The review used the *Complex Problem Solving* Graph-of-Thoughts
template: **Problem-Decompose → Hypothesis-Gen → Test-Design →
Evidence-Gather (looping back to refine hypotheses) → Solution-Synthesis**.
It fit better than the architecture template. The code already existed,
so "improve it" was best answered by finding what is actually wrong or
missing, proving it, and fixing it, rather than by picking a new
architecture.

Evidence came from three places:

1. Live API responses: Represent, OpenParliament, ourcommons.ca and
   elections.ca were fetched on 2026-09-28 through an external fetcher,
   because the build sandbox's network policy blocks those hosts.
2. Offline reproductions against the v4 code.
3. The new automated test suite.

---

## 1. Problem decomposition tree

```
Improve the scraper
├── A. Correctness: does it produce the right data?
│   ├── A1 federal run completes and reaches master.xlsx?
│   ├── A2 enrichment columns ever filled?
│   └── A3 campaign contributions ever matched to politicians?
├── B. Reliability: does a long run finish?
│   ├── B1 rate limit respected?
│   ├── B2 transient network errors survived?
│   └── B3 locked/interrupted file writes?
├── C. Data integrity: is master.xlsx history true over time?
│   ├── C1 is a person's identity stable across runs?
│   ├── C2 are changes (including blanks) recorded?
│   └── C3 can a bad or partial run corrupt history?
├── D. Completeness: is the public data that's available captured?
│   ├── D1 contact info (constituency offices)
│   ├── D2 province/territory
│   └── D3 other published fields (roles, languages, extras)
├── E. Performance / API citizenship
└── F. Usability & maintainability (CLI, docs accuracy, duplication, tests)
```

## 2. Hypotheses, tests and evidence (ranked by impact)

| # | Hypothesis | Test | Evidence | Verdict |
|---|---|---|---|---|
| H1 | The default federal run crashes before updating master.xlsx | Run v4 `scrape_federal.main()` with mocked APIs | `ValueError: dict contains fields not in fieldnames: 'committees', 'openparliament_url', 'first_elected'`; master.xlsx never written | **Confirmed, critical** |
| H2 | Federal contributions never match anyone | Feed v4 `aggregate_contributions` a row in Elections Canada's real format | The `Recipient` column is "Last, First" ("Whelan, Susan"): key `whelan susan` vs master `susan whelan`, so 0% of candidates match | **Confirmed, critical** |
| H3 | The `committees` column is always blank | Run v4 `_extract_membership_info` on a live OpenParliament record | Its `memberships` are House seat terms ("Conservative MP for Edmonton Manning"), never committees, so it returns `[]` for every MP | **Confirmed, high** |
| H4 | The local scrape trips Represent's rate limit | Simulate 108 councils at 250 ms latency | The delay only ran between pages of one council, so the peak was **109 requests/min** (limit 60, over which Represent answers 503). After 3 × 3 s retries the run died | **Confirmed, high** |
| H5 | Dropped connections kill the run | Raise `RemoteDisconnected` from `urlopen` | It derives from `OSError`, not `URLError`, so it escaped the retry loop | **Confirmed, high** |
| H6 | The master key is unstable | Live data + an upsert simulation | The key is `url`, the legislature website page, which is **blank for many councillors** (every one sampled in Saskatoon, Huron, Senneville, Wellesley, Lambton and Toronto) and changes on site redesigns. A council starting to publish URLs produced `removed` + `added` with history reset | **Confirmed, high** |
| H7 | A field going blank is never recorded | Upsert an MP whose email disappears | 0 changes recorded; the stale email is kept forever | **Confirmed, medium** |
| H8 | Adding any column would corrupt existing master.xlsx files | Code read | `read_master_tab` mapped columns by position | **Confirmed, blocks D1–D3** |
| H9 | Name-only OpenParliament matching misses MPs | Compare live names | Represent "Jasraj Hallan" vs OpenParliament "Jasraj Singh Hallan" | **Confirmed, medium** |
| H10 | Every MP's address is the same | Live House of Commons record | v4 kept only the legislature office, "House of Commons, Ottawa ON K1A 0A6", for all 343 MPs; constituency offices were dropped | **Confirmed, medium** |
| H11 | There is no province column | Live data | Council names are ambiguous (Richmond BC / Richmond Hill ON, Stratford PE); the province *is* encoded in each set's `data_url` (`ca_on_hamilton`) and each MP's riding code (`…/48007/` → AB) | **Confirmed, medium** |
| H12 | `recipient_type` is always blank | Real header | The column is named `Political Entity`; none of the v4 candidates matched it | **Confirmed, medium** |
| H13 | Budget column matching is fragile | Real header | Underscore candidates vs space-separated headers; the substring fallback picked the *first* column containing "date"/"amount" | **Confirmed, medium** |
| H14 | Scraping is far slower than needed | Represent docs + live `total_count` | 121 sets, 3,810 people; the documented bulk endpoint (`/representatives/?limit=1000`) gets everyone in **4 requests** | **Confirmed** |
| H15 | Typos silently change behaviour | Code read | Hand-rolled `sys.argv` scanning: `--filtr Toronto` ran the full scrape; `--since abc` crashed with a traceback | **Confirmed, low** |
| H16 | The README is inaccurate | Live data | "Several hundred councils" (actually 108), "all 10 provinces + 3 territories" (Represent has no Nunavut), "10–20+ minutes" | **Confirmed, low** |
| H17 | `include_former` never worked | Live OpenParliament filter list | The API has no `current` filter; "all MPs" is `include=all` | **Confirmed, low** |
| R1 | `classify_set` misfiles some legislatures | Run it over all 121 live set names | 1 federal / 12 provincial / 108 local, none "other" | **Refuted**, kept as a regression test |
| R2 | Councils share one URL for all members | Sampled councils | URLs are *blank*, not shared (H6) | **Refuted** (guarded against anyway) |

**Loop-backs.** Evidence gathering changed the plan several times:

- The Represent docs showed the bulk endpoint (H14) and that the rate
  limit answers 503.
- The OpenParliament detail record showed why committees were always
  empty (H3). It also exposed `other_info.parl_mp_id`, which enables an
  ID cross-check.
- Searching for a committee source found the House of Commons' own
  per-MP roles XML. Represent's `url` for an MP *is* that profile, so
  committees can be joined by ID with no name matching.
- The Elections Canada sample exposed the "Last, First" format (H2) and
  the `Political Entity` column (H12). It also showed there's a smaller
  "as reviewed" dataset.

**Could not be verified from the sandbox** (handled defensively instead):

- The Elections Canada file's text encoding: each line falls back from
  UTF-8 to cp1252.
- Whether the zip ever holds more than one CSV: all CSVs in it are now
  read.
- ourcommons.ca's rate limits: requests are throttled to 2/s, and the
  pass stops after 3 consecutive failures.

## 3. Validated solution

**Correctness**
- The CSV writer ignores keys it wasn't asked for, and the federal
  snapshot now includes the enrichment columns (H1).
- Committees come from `…/Members/en/<mp>(<id>)/xml`, current roles
  only, labelled "(Chair)" etc. (H3).
- OpenParliament matching falls back to riding + last name, and every
  match is confirmed with the House of Commons person ID (H9).
  `include=all` is fixed (H17).
- Contributions are keyed "first last" from the recipient first/last
  columns. People's candidate, leadership and nomination money is
  combined; `Political Entity` becomes `recipient_type`. Columns are
  matched by normalized exact name with no substring guessing, and a
  missing column stops the run and prints the real header (H2, H12,
  H13).
- Budget matching tries the full name, then "first word of first name
  + last name" when unambiguous. It flags `riding differs -- check`
  when a match's riding never appears in their contributions, and adds
  non-monetary totals, a date range and districts. An optional
  `--reviewed` dataset is available.

**Reliability** (`represent_common.http_get`)
- One throttled entry point for every request: ~54/min to Represent,
  per host, and thread-safe (H4).
- Retries now cover `RemoteDisconnected`, `IncompleteRead` and invalid
  JSON (H5). 429/5xx back off 5→40 s and honour `Retry-After`; 404 is
  not retried.
- Connection failures fail fast (3 quick attempts). One-request-per-item
  loops stop after 3 consecutive failures, and the scripts print a clear
  "couldn't reach …" message. Without this, an offline run could spend
  hours retrying.
- A council that fails to download is skipped and reported, not fatal,
  and its people are never marked removed.
- Every file is written to a temp file and swapped in atomically. A
  file locked by Excel prompts for a retry, or else saves to
  `… (unsaved <timestamp>)`. master.xlsx keeps a `.backup.xlsx`.
- The Elections Canada download is retried and validated before caching.

**Data integrity** (`upsert_master`)
- Permanent `record_id` per person. Rows are matched by URL, then name,
  then seat + last name, each only when unambiguous (H6). Rows from v4
  workbooks get their old key as their `record_id`, so their changelog
  history stays linked.
- Represent fields are authoritative, blanks included (H7). Enrichment
  fields only change when enrichment actually reached that MP, so a
  `--no-enrich` run doesn't wipe them but a real "left all committees"
  is recorded.
- Columns are read by header (H8). Columns this version adds are
  back-filled silently instead of logging ~3,800 changes.
- Removal detection runs only for sets actually downloaded. A set that
  comes back empty is treated as an upstream glitch; a returning person
  is `reactivated`.

**Completeness** (all appended after the v4 columns, so CSV positions don't move)
- `province_territory` for every level (H11), `constituency_phone`,
  `constituency_address` (H10), `roles`, `preferred_languages`, and
  `extra_info` for everything else Represent publishes.

**Performance**
- The bulk download is used for many sets: the local scrape went from
  ~110+ requests and 10–20 min to **5 requests** (the set list plus 4
  pages of 1,000 people) (H14). Downloads are memoized, so
  `scrape_all.py` fetches everything once. Enrichment runs its two
  sources in parallel.

**Usability & maintainability**
- `argparse` everywhere: `--help`, typos rejected (H15), `--output-dir`
  / `CA_POLITICIANS_OUTPUT_DIR`, and a `--province` filter for local.
  Exit codes propagate through the .exe dispatchers.
- The Windows Desktop is read from the registry, so output lands on the
  OneDrive-redirected Desktop the user actually sees. Console output
  can't crash on unencodable characters.
- Live lookup searches every level in a few requests (no `--category`
  needed) with accent-insensitive, order-free prefix matching.
- The three duplicated budget builders are consolidated into
  `budget_common.py`.
- README corrected (H16). `.gitattributes` keeps `build_exes.bat` in
  CRLF (cmd.exe can mis-handle `goto` labels in LF-only batch files).

## 4. Verification

| Check | Result |
|---|---|
| v4 code against 10 reproduction scenarios (H1–H7, H9, H10, H12) | 10/10 defects reproduced |
| The same 10 scenarios against the new code | 10/10 fixed (e.g. peak 55 req/min on the forced per-council path; 5 requests on the default path at real data size) |
| `python3 -m unittest discover -s tests` | 89 tests pass offline in ~2 s. They use trimmed real API responses (`tests/fixtures/`) and a fake clock, so rate limits and backoff are measured, not waited for |
| Python 3.8 syntax check | All files parse with `feature_version=(3, 8)` |

**First live run: what to look for** (the sandbox couldn't reach the
real APIs, so do this once on your machine):

1. `python3 scrape_federal.py` should report 343 MPs, OpenParliament
   "enriched ~343/343 (N matched by riding …)" and House of Commons
   "committee data for ~343/343". If committees come back empty for
   everyone, check one MP's `<url>/xml` in a browser. (Since added: it
   also reports the senators, about 95 of the Senate's 105 seats while
   some are vacant.)
2. `python3 scrape_local.py` should say "Downloading all
   representatives in bulk…" and finish in seconds.
3. `python3 scrape_budget_federal.py` prints the real CSV header, then
   "Matched X/343 MP(s)". Expect most sitting MPs to
   match. If the header has changed, the run stops and tells you which
   column name to add to `FIELD_CANDIDATES`.

## 5. Not done (candidates for follow-up)

- **Provincial/municipal contribution fetchers.** The extension points
  are ready, but each source must be verified against its real site
  first, which wasn't possible from here.
- ~~**Nunavut.**~~ Done since: `nunavut.py` reads assembly.nu.ca, and
  `senate.py` adds the Senate, which Represent doesn't cover either.
- **Scheduled runs / CI.** The test suite is ready to run in GitHub
  Actions on every push.
