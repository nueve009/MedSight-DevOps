# MedSight — Change Log

A running record of project changes, decisions and open items, so later work
can pick up where the last session left off. Newest entry first.

---

## 2026-11-10 - Review console: tokens signed with published keys

**Bug found on the first real sign-in.** The project signs sessions with ES256,
and `.env` also had the older `SUPABASE_JWT_SECRET`. `review_auth` used the
secret whenever it was set, so every token was refused as `invalid_token`. Each
token's own signing method now picks the key, with HS256 tied to the secret and
RS256/ES256 tied to the published keys, so a public key can't be used as a
shared secret. The log now gives a precise reason (`bad_signature`,
`no_matching_kid`, `alg_not_allowed`, `bad_audience`, `bad_issuer`, ...), and a
30-second clock difference is allowed.

---

## 2026-11-10 - Review console: two-step sign-in is a setting

**Decided by Mon.** The authenticator step wasn't asked for and was rejecting
sign-ins during setup. `REVIEW_REQUIRE_MFA` now controls it: on by default, so a
hosted console stays strict, and off in `scripts/review_console.py` unless given
`--require-mfa`. Everything else is still checked. The page is told which mode
to use. Storage rules are pasted by hand (`kb/README.md`, "Two-step sign-in
setting"), since applied migrations are never edited. Turning it back on before
hosting is on the checklist in `docs/runbook.md`.

---

## 2026-11-10 - Review console, phase 1 (runs locally)

**Asked by Mon:** a web tool for the reviewers that can update the dataset
without a deploy. Approvals go to Supabase and the deployed backend serves them
within `KB_REFRESH_S`.

**Added**
- `migrations/0006_review_console.sql` (**not yet run**): `reviewers`,
  `kb_reviews` (append-only), `kb_evidence`, `kb_fda_lookups`, `kb_settings`,
  `review_state` and `last_edited_by`, role `medsight_review`. The Storage
  bucket is a commented block to run by hand, because Storage tables exist only
  in a Supabase project.
- `scripts/fda_lookup.py --push --by NAME`: draft rows into `kb_fda_lookups`.
- `app/review_auth.py`, `app/review_store.py`, `app/review.py`: the API.
- `review/` (plain HTML and JavaScript, no outside code) and
  `scripts/review_console.py`: `python -m scripts.review_console` on 127.0.0.1:8100.
- `PyJWT[crypto]` and `python-multipart` in `requirements.txt`.

**Decisions**
- The approval sends the `updated_at` of the row the reviewer read, so a row
  edited in between is refused rather than approved unseen.
- A draft may leave its FDA values and description empty (they often come later
  from a candidate); approving needs them all.
- Every reviewer may edit any row. Editing makes you its last editor, so you
  can't approve it.
- `0006` has no `schema_version` insert: `scripts/migrate.py` records every file.
- No `Pillow` in the requirements, so uploads are checked by their first bytes
  and not re-encoded. Photo metadata stays in the file.
- `kb_is_review_role()` checks real membership, not `pg_has_role`, so a superuser
  can't pass as the review role once Table Editor approval is off.

**Checked:** 0006 and the store on a throwaway local Postgres 18, as the review
role. The page was clicked through in a browser against that database, with a
stand-in for Supabase Auth: sign-in, two-factor, queue, row page, approve. An
HTML injection attempt in a row showed as plain text.

**Not checked:** real Supabase Auth, Storage and its policies, and first-time
two-factor enrolment on a real project; none of those can be tested locally.
The Vercel hosting is prepared (`review/vercel.json`, `config.example.js`) but
not deployed. `vercel.json` has placeholder hosts to replace.

---

## 2026-11-10 - Web answers say "prescription" when the page does

**Asked by Mon:** a prescription medicine outside the reviewed list should
still be stated as one if the search results say so. A web answer now adds
"According to <site>, this is a prescription medicine." after the web warning,
and `product.prescription` is true. Both the model and the chosen page's own
wording must agree (`web_search.page_states_rx`); a page that also says
non-prescription or OTC doesn't count. Stored as `rx` in the answer's
`product` JSON, so no migration, and web answers cached before this keep no
prescription line until they expire. The ingredient and general tiers are
unchanged: nothing there comes from a source that states it. The scan log's
`web_rx` is `yes`, `no` or `model_only` (the model said so but the page's
wording didn't). It works only with the web tier on
(`WEB_SEARCH_PROVIDER=tavily` and `TAVILY_API_KEY`). The sentence is
new spoken wording, so it still needs the team's approval and a listen on the
phone's text-to-speech (`speech.py`).

---

## 2026-11-10 - FDA portal lookups may be scripted (docs only)

**Decided by Mon.** The old rule "the FDA portal must not be scraped or
automated" is replaced: a local script may draft the FDA values the dataset
needs (`fda_reg_no`, `reg_expiry`, `classification`, `manufacturer`,
`portal_checked_on`) for the products on the team's list only, at least 5 s
between requests, with a clear user agent and cached results. Its output is a
draft in `kb/staging/`; a reviewer still confirms each value on the portal
before approving. Checked first: robots.txt allows every path and no terms of
use were found. Updated `CLAUDE.md`, `kb/README.md`, `.agents/AGENTS.md` and
`docs/runbook.md`. The comment in the applied `migrations/0005_kb_dataset.sql`
still says "never scraped"; it is left as is because applied migrations are
never edited.

Built the same day: `scripts/fda_lookup.py` (tests in
`tests/test_fda_lookup.py`). It calls the portal's own search,
`GET /api/search?q=<brand>`, and reads the Drug CPR results (`cdrr`). It looks
up only the team's list (`staging/brands.txt` plus `tfd_products_draft.csv`),
waits at least 5 s between requests, caches each reply in `staging/fda_cache/`
and stops on HTTP 401, 403, 419 or 429, a redirect, or anything that isn't the
expected JSON. It writes every registration for a brand to
`staging/fda_lookup_draft.csv` without picking one; the reviewer picks by the
number on the box and confirms it on the portal.

## 2026-11-09 — Reviewed dataset in Supabase, approved row by row

**Branch:** `backend/kb-in-supabase`. Asked by Mon: keep the dataset in
Supabase and read it from there, and let the team approve rows in Supabase.

**Why:** `data/kb.sqlite` is git-ignored and the Dockerfile never built it,
so a Railway deploy had an empty reviewed dataset (the runbook said Railway
runs `kb_build.py`; it didn't). Approving in the Table Editor also replaces
typing `checked_by` into a CSV.

**Added**
- `migrations/0005_kb_dataset.sql`: `kb_ingredients`, `kb_products`,
  `kb_passages` (the CSV columns plus `approved`, `approved_at`,
  `review_note`, timestamps) and `kb_review_log`.
  - Trigger `kb_review_guard`: rows are added unapproved; approving needs
    `checked_by` different from `entered_by` and the dataset rules (dose
    patterns, 300 characters, strengths per ingredient, classification,
    unexpired registration, known ingredients, tfd URL); an edit and its
    approval can't be one save; editing an approved row un-approves it and
    clears the checker. Every change is logged with `kb_review_log`.
  - RLS on; `medsight_backend` gets SELECT with a policy `USING (approved)`;
    `anon`/`authenticated` get nothing.
- `app/kb_validate.py`: the row rules, moved out of `scripts/kb_build.py`
  so the server can use them; `servable()` drops broken approved rows and
  everything that depends on them.
- `app/kb_source.py`: reads approved rows in one read-only snapshot,
  `KBRefresher` reloads every `KB_REFRESH_S` (60) and keeps the old dataset
  on failure.
- `KB_BACKEND` (default `CACHE_BACKEND`), `KB_DATABASE_URL`, `KB_REFRESH_S`,
  `KB_ADMIN_DATABASE_URL`.
- `kb_build --push` (CSV drafts into Supabase, unapproved, upsert by key,
  checker columns never written, unchanged rows untouched) and `--pull`
  (approved rows into `data/kb.sqlite`, optionally CSVs).
- `tests/test_kb_supabase.py`.

**Changed**
- Ingredient rule: a names-only ingredient may now have `entered_by` and
  `checked_by` filled (every Supabase row has them); the description fields
  are required once `spoken_description` or `source_url` is filled.
- "has no indications passage" is reported on the product's line, not on
  `passages.csv`.

**Not done / open**
- Supabase records the database owner for every Table Editor change, so
  `checked_by` is a typed name, not a verified identity. A review page with
  per-person keys would fix that if the team needs it.
- No pgvector: matching is exact and fuzzy names on about 30 products held
  in memory; nothing to embed.
- The FTS `search` table exists only in the local `kb.sqlite`.

---

## 2026-11-04 — M7: admin routes, audit log, failure drills, runbook

**Branch:** `backend/m7-admin-runbook`, on top of `backend/m6-web-tier`.

**Added**
- `app/admin.py`: `/admin/answers` (list, read, PATCH, DELETE) and `/admin/audit`.
  - Off unless `ADMIN_ENABLED`; every `/admin` path is a 404 until then.
  - Per-person `ADMIN_KEYS` in `X-Admin-Key`, compared in constant time. An admin key that equals an app key is ignored, with a warning.
  - PATCH changes only `status` and `description`. Anything else, `tier` included, is a 422, so nothing can be made "reviewed".
  - An edited description must be 300 characters or fewer, with no dosing.
- `migrations/0004_audit_log.sql`: who, action, cache key, before and after, when. The backend role can only read and add rows (no UPDATE or DELETE grant).
- Store:
  - `read_answer` (any status, expired too).
  - `admin_update_answer` and `admin_delete_answer`, each writing the change and its audit row in one transaction.
  - `list_audit` and `export_audit`. SQLite has the same table.
  - Admin writes don't fail open: a database error is a 503 with nothing changed.
- `store_admin export` also writes `audit-<time>.jsonl` (plan section 4: nightly export of answers and the audit log).
- `tests/test_drills.py`. Each drill ends in a clear spoken message:
  - Groq down, before and after the parse
  - search down
  - Supabase down (scans answer, and the limits run from memory)
  - a key rotated
  - the daily model budget used up
- `docs/runbook.md`:
  - deploy and rollback
  - removing or fixing a bad answer
  - the weekly web-answer review
  - rotating each key
  - restore
  - adding a reviewed medicine
  - provider outages
  - the manual drills on staging
  - alerts

**Changed**
- `DocsGateMiddleware` became `RouteGateMiddleware`, which also hides `/admin`.

**Verified**
- `pytest`: 391 pass. `test_admin.py` covers:
  - access: disabled, wrong keys, app keys, a shared key
  - approve, reject, fix and delete, each audited
  - a refused change leaving no trace
  - a database outage
- On a local Postgres 16:
  - the store suite and the rate-limit tests pass
  - migration 0004 applies
  - the backend role can add audit rows but is refused UPDATE and DELETE
- `ruff` is clean.

**Open items**
1. DevOps: run `migrate` (0004), set `ADMIN_KEYS` per person, and keep `ADMIN_ENABLED` off on production except while fixing an answer.
2. The pilot gate:
   - 100 labeled scans
   - the drills by hand on staging
   - a teammate following the runbook
   - TalkBack and VoiceOver (app team)
   - the privacy notice

---

## 2026-11-04 — M6: web search tier (fake model and search)

**Branch:** `backend/m6-web-tier`, on top of `backend/m5-smoke-test-docs`.

**Decision (Mon, 2026-11-04):** the web tier uses the same Groq model as parsing, with a Tavily search for the pages. This replaces the plan's Claude Sonnet with web search.

**Added**
- `app/web_search.py`:
  - `ALLOWED_SITES`:
    - TheFilipinoDoctor
    - `*.unilab.com.ph`
    - `labeling.pfizer.com`
    - Hello Doctor
  - `site_name()` enforces the allowlist in code: https only, no port or login part, exact hosts (`www.` allowed), subdomains only for entries starting with `*.`.
  - `tavily_search()`: one basic search with `include_domains`.
  - `lookup()`:
    - Results outside the allowlist are dropped and logged.
    - A page must name the brand and every ingredient (with a few other names, such as acetaminophen).
    - At most 2 pages go to Groq, fenced and capped at 4,000 characters.
    - The JSON reply is gated: found, a valid source, no dosing, 300 characters at most, no "recognize", and every ingredient of a combination named.
- Pipeline:
  - The web tier runs for an unlisted, branded product after the scan rules pass, when there's no cached web answer and no live "not found" marker.
  - Found: cached as `web` / `pending_review` (30-day expiry), shared, and spoken as the web warning, then "According to <site>, ...".
  - Not found or a failed gate: a "not found" marker in the parse cache (`web-miss:` keys, 7 days), then the general tier.
  - A search outage or a used-up cap: the general tier, with nothing cached.
  - A cached general answer is replaced once a web answer is found.
- `Limiter.spend_web_lookup`: 3 a device and 20 a day, counted only when a search runs. A warning is logged at 80%. Past a cap the scan gets the general tier, not a 429.
- Config:
  - `WEB_SEARCH_PROVIDER` and `TAVILY_API_KEY`: the web tier is off unless both are set.
  - `WEB_SEARCH_TIMEOUT_S`
  - `WEB_DEVICE_PER_DAY` and `WEB_DAILY_BUDGET`
  - `WEB_MISS_TTL_DAYS`
- `tests/conftest.py`: `FakeSearch` replaces `web_search.search` in every test. A search with no fake results set fails.

**Verified**
- `pytest`: 351 pass. `tests/test_web_search.py` covers:
  - the allowlist and look-alike hosts
  - the spoken order and the site name
  - pending_review caching and a cache hit with no search
  - expired and rejected answers being searched again
  - each gate, including a page that tries to inject dosing
  - fencing and the page caps
  - "not found" caching, made-up names, and outages
  - the caps per device and overall
  - the API and v1 shapes
  - the Tavily request and its failures
- `ruff` is clean.

**Open items**
1. A Tavily API key (staging and production) and a spend limit on it. Then, a run against the real search.
2. The done-when: two people review the answers for 20 unlisted medicines.
3. The admin queue (approve or reject) is M7.
4. Railway variables for the web tier (DevOps).

---

## 2026-11-04 — M5: smoke test and API docs off (backend part)

**Branch:** `backend/m5-smoke-test-docs`, from `main` after PR #42 (M3 and M4).

**Added**
- `scripts/smoke_test.py --url <base url>`, with the key in `SMOKE_API_KEY`. It runs six checks:
  - `/health` answers ok
  - a wrong key gets 401
  - empty text is a `no_text` rescan
  - a known Biogesic scan answers `ok` with spoken text
  - `/docs` and `/openapi.json` are 404
  - repeated empty scans end in 429 with `Retry-After` and spoken text
- Each run uses a new random device ID. It prints PASS or FAIL per check and exits 1 on any failure.
- `API_DOCS` (default `off`): `/docs`, `/redoc` and `/openapi.json` answer 404 unless it's `on` (plan section 7, #12). `DocsGateMiddleware` reads it per request.

**Changed**
- `/docs` is no longer served by default. For local work, add `API_DOCS=on` to `.env`.
- The max_tokens 200→1000 trial in `description_agent.py` was dropped (Mon, 2026-11-04). It stays at 200.

**Verified**
- `pytest`: everything passes. `tests/test_smoke_test.py` runs the script against the real app with the fake LLM. It covers:
  - all six checks passing
  - a 429 at the right count
  - failing when docs are on, the server key is unset, or the model is down
- Against a local `uvicorn` with real Groq: 6/6 passed (a `general` answer, then 429 after 14 more scans).
- `ruff` is clean.

**Open items (DevOps)**
- Railway: pin the Python version, run `kb_build.py` in the build step, health check on `/health`.
- Run the smoke test after each staging deploy and after promoting to production.
- Don't set `API_DOCS` on Railway.

---

## 2026-11-01 — M4: rate limiting and abuse protection

**Branch:** `backend/m3-m4-supabase-ratelimit` (M3 and M4 ship together,
Mon's decision 2026-11-01).

**Added**
- `app/ratelimit.py`:
  - Per device: 15 a minute and 150 a day. A request without a device ID
    counts against a bucket made from its address.
  - Per address: 30 a minute.
  - Every request counts, refused ones included.
  - Windows are fixed UTC minutes and days.
- Daily model-call budget:
  - The pipeline calls `budget()` before each parse and description call,
    so cache hits cost nothing.
  - 180 calls a UTC day for the whole service, plus optional per-key caps
    (`KEY_DAILY_LLM_CAPS`).
  - A warning is logged at 80%.
- Counters:
  - stored in the store's `rate_limits` table (`migrations/0003`; SQLite
    has it too)
  - one statement per check
  - an in-memory fallback when the store fails
  - hashed buckets
  - finished windows deleted by `store_admin purge`
- API:
  - `api.GUARDS` checks the API key and then the limits, before the
    pipeline.
  - Every limit answers 429 with `reason` (`too_fast`, `daily_limit`,
    `service_limit`), `spoken_text`, `retry_after_s` and `Retry-After`.
  - The new spoken lines are in `speech.py` and covered by its tests.
- Named API keys (`MEDSIGHT_API_KEYS`, `name=key,...`), all compared in
  constant time. `MEDSIGHT_API_KEY` still works as `default`. The key name
  is in the scan log.
- `CLIENT_IP_HEADER` (Railway: `x-real-ip`; with `x-forwarded-for`, the
  entry the proxy itself added). Empty means the TCP peer.
- `MAX_CONCURRENT_SCANS` (8): past it, a quick 503 with `Retry-After: 2`.

**Decision:** the plan's 6 a minute per device conflicted with its own
done-when ("a rescan every 5 seconds is never blocked", which is 12 a
minute). The default is 15 a minute, configurable.

**Verified**
- `pytest`: everything passes. `tests/test_ratelimit.py`:
  - 60 back-to-back scans from one phone give 15 OKs, then only 429s, with
    at most 2 model calls.
  - 12 scans a minute are never blocked.
  - The budget gives a 429 and spoken text, while cached scans still work.
  - Rotating device IDs are caught by the address limit.
  - The memory fallback keeps limiting when the store is down.
  - There's no counter clash at midnight.
  - Concurrency: a 503 when all slots are full.
- The store tests and rate counters also pass on a local Postgres 16.
- End to end on Postgres: migrations 0001 to 0003 apply, and the backend
  role updates counters with no errors.
- `ruff` is clean.

**Open items**
1. Railway variables are DevOps's: `CLIENT_IP_HEADER=x-real-ip`, the key
   list, and the caps.
2. The web-tier caps (3 a device, 20 a day) come with M6.
3. Phone attestation is later (plan section 7, #1).

---

## 2026-11-01 — M3: Supabase cache and scan log

**Branch:** `backend/m3-supabase`, on top of `backend/m2-dataset-matching`
(M2's PR is open; rebase onto `main` once it's merged).

**Added**
- `app/store/`, which replaces `app/cache_store.py`. It's one `Store`
  interface with two versions, chosen by `CACHE_BACKEND`:
  - `SQLiteStore` is the default, with the same file and run log as before.
    Older cache files gain the new columns when opened.
  - `PostgresStore` is for Supabase.
- Both versions:
  - store only `general` and `web` answers
  - treat `rejected` and expired answers as a miss
  - give web answers a 30-day expiry
  - support get, set, delete and list, plus export, import and purge
- Fail open:
  - 2 s to connect and 1 s per query (`SET LOCAL statement_timeout`).
  - After a failure, the circuit breaker skips the database for 60 s and
    logs one `database skipped` warning.
  - `pipeline._cache_call` ignores `StoreSkipped` silently.
  - Prepared statements are off, so Supabase's transaction pooler works.
  - A missing `DATABASE_URL` with `CACHE_BACKEND=postgres` stops the
    service from starting, because that's a configuration mistake, not an
    outage.
- The scan log moves behind the store (`log_scan`):
  - SQLite: `logs/run_log.jsonl`, as before.
  - Postgres: the `scan_log` table, with the OCR text in its own column and
    left out of the `record` JSON.
- `migrations/0001_cache_and_scan_log.sql`: the `parses`, `answers` and
  `scan_log` tables, with RLS on.
- `migrations/0002_backend_role_grants.sql`: grants and RLS policies for
  the `medsight_backend` role only, and everything revoked from `anon`,
  `authenticated` and `PUBLIC`.
- `scripts/migrate.py`: applies the numbered files in order, one transaction
  each, recorded in `schema_version`. `--check` lists what's pending.
- `scripts/store_admin.py`: `export`, `restore` and `purge`. Purge deletes
  expired answers and clears scan-log OCR text after 30 days.
- `migrations/README.md`: role setup, first deploy, outage behavior, the
  nightly jobs and the restore steps.
- `/health`'s startup log line includes `cache_backend`.
- Dependencies: `psycopg[binary]==3.3.6`, `psycopg-pool==3.3.3`
  (`pip-audit`: no known vulnerabilities).

**Decision:** the plan said "RLS on, no policies". With no policies, the
backend role would need `BYPASSRLS`, which Supabase may not grant. Instead,
RLS stays on and the only policies are for `medsight_backend`, so the public
keys still open nothing.

**Verified**
- `pytest`: all tests pass on SQLite. `tests/test_store.py` also passes on
  a local Postgres 16 (`pgserver`) with `MEDSIGHT_TEST_DATABASE_URL` set.
  - A dead database fails in under 2 s and is then skipped in under 0.1 s.
  - Scans keep answering with the database down.
- End to end on a local Postgres:
  - `migrate --check`, `migrate`, then `--check` again shows nothing pending.
  - The backend role reads and writes, and a stand-in `anon` role gets
    `InsufficientPrivilege`.
  - `/v2/identify` answered a miss, then a hit.
  - With Postgres stopped mid-run, the next scans still returned
    `ok`/`general` in about 0.01 s.
- `ruff` is clean (the scraper is excluded).

**Open items**
1. DevOps:
   - Create the Supabase projects (staging and production, Singapore).
   - Set `CACHE_BACKEND`, `DATABASE_URL` and `MIGRATION_DATABASE_URL` in
     Railway.
   - Run `migrate` on deploy.
   - Schedule `store_admin export` and `purge` nightly.
   - Add a Postgres service container to CI with
     `MEDSIGHT_TEST_DATABASE_URL`.
2. The first real Supabase connection (pooler user format
   `medsight_backend.<project-ref>`) hasn't been tried yet.
3. The restore test on the real database is planned for M6.

---

## 2026-10-27 — M2: reviewed dataset and matching (backend part)

**Branch:** `backend/m2-dataset-matching` (from `main` after M1 was merged).

**Decisions (2026-10-26):**
- **TheFilipinoDoctor (TFD) is the dataset's text source** (`source_type: tfd`,
  page URL in `source_ref`). **MIMS is dropped**: its drug directory needs an
  account. This replaces decisions 1 and 3 in
  `docs/project-status-and-decisions.md`, which still say MIMS.
- The team's TFD scraper (`scripts/tfd_scrape.py` + its test) stays local
  and is **not committed**. Its output goes to `kb/staging/`, now git-ignored.
- OTC/Rx, registration number and expiry still come from the FDA portal by
  hand; the website's "Non-Rx" labels are only hints.

**Added**
- `app/retrieval.py`: `KnowledgeBase` (loaded once from `data/kb.sqlite`;
  missing file = empty dataset + a warning) and `match()`:
  - `reviewed`: exact brand or alias, or a fuzzy brand at 0.85 or more,
    **and** the same ingredient set and strengths after synonyms. Spoken
    with the dataset's spelling, its `spoken_description`, and the
    prescription line for `Rx`.
  - `partial`: an exact listed brand whose ingredients or strengths
    disagree. Becomes a rescan (`strength_unread` or
    `ingredients_incomplete`, then `gave_up`). Never for a fuzzy brand, so
    a near miss never names a listed brand.
  - `ingredient`: every ingredient has a reviewed description.
  - Several full matches with different descriptions or Rx status are not
    answered as reviewed.
- Pipeline: the dataset match runs after the scan rules. Reviewed and
  ingredient answers never use the answer cache or an LLM, so they beat a
  stale general answer cached earlier. `/health` adds `kb_version` and
  `kb_products`; the scan log adds `match`, `matched_product_id` and
  `kb_version`.
- `kb_build.py`:
  - new columns: products `spoken_description`, `description_checked_by`;
    ingredients `spoken_description`, `source_url`, `entered_by`, `checked_by`
  - the `tfd` source type, with a TFD-URL check
  - spoken descriptions over 300 characters, or with a dose or schedule,
    are rejected
  - a described ingredient needs all four fields and two different people
  - `--kb-dir` validates a draft folder
  - the "targets 10 products" warning is gone
  - the CSV header rows in `kb/` are updated (still no data rows)
- `scripts/kb_draft_descriptions.py`: an LLM drafts plain spoken
  descriptions from the indications text into
  `kb/staging/descriptions_review.csv`. It drafts ingredients from
  single-ingredient products only, and flags `dose`, `too long`,
  `new words`, `no source` and `failed`. It refuses to write into `kb/`.
- `scripts/eval_kb.py`: runs `kb/eval/scans.jsonl` (`expected`: a product_id,
  `not_listed`, `multiple` or `rescan`) through the real pipeline with a
  temporary cache. It reports each outcome and exits with code 1 on any
  wrong reviewed answer. `kb_make_scans.py` now writes `expected`.
- `kb/README.md` rewritten for the TFD and hand-FDA-lookup workflow.

**Changed:** `speech.PRESCRIPTION_LINE` is now `RX_LINE`, and the
`prescription=` parameter is now `rx=`. CodeQL had flagged the old name as
sensitive data on PR #23, a false positive that DevOps dismissed. The API
field `product.prescription` is unchanged.

**Verified**
- `pytest`: 239 backend tests pass, plus the scraper's own 6. `ruff` is
  clean (the scraper is excluded). The real empty `kb/` passes
  `kb_build --check --allow-empty`.
- Tests cover the risky pairs on a fake dataset:
  - NEOZEP read with Bioflu's exact formula → ingredient tier, never
    Bioflu's answer
  - generic paracetamol 500 mg → ingredient tier, never Biogesic
  - Alaxan FR read as paracetamol only → `ingredients_incomplete`
  - "Biogesik" with ibuprofen → never named Biogesic
  - Tempra forms by strength
- End to end on `/v2/identify` with a fake built dataset and local
  `qwen3.5:4b`:
  - Biogesic, Alaxan FR and Dolfenal → `reviewed`, with the prescription
    line for Dolfenal
  - brand-only foil → `strength_unread`, then `gave_up` at attempt 3
  - NEOZEP → `ingredient`
  - an unknown product → `general`
- `kb_draft_descriptions` on the team's real staging data (23 products, 15
  ingredients) with `qwen3.5:4b`: 38 drafts in 73 s.
  - Most drafts are faithful plain-language rewrites.
  - Flags point at substitutions to confirm ("vaccination", "heartburn",
    "heavy periods").
  - Some drafts keep jargon ("mucoviscidosis", "ileostomies") for the
    reviewer to simplify.
  - 8 ingredients only appear in combinations and need another source.

**Open items**
1. **The dataset is empty.** 15 of the 29 brands aren't on TFD under their
   guessed URL, and Solmux only has "Solmux for Kids". All FDA columns,
   synonyms, PNF columns and both checker names need doing by hand. M2 is
   done only after 100+ labeled scans give 0 wrong reviewed answers.
2. The staged TFD "warnings" are long contraindication lists, not spoken
   warnings. They aren't spoken; the team can write short ones later.
3. 8 combination-only ingredients (phenylephrine, chlorphenamine,
   phenylpropanolamine, dextromethorphan, aluminum hydroxide, magnesium
   hydroxide, simethicone, …) have no single-ingredient source. Until
   they're described, unlisted products with them get the general tier,
   not the ingredient tier.
4. Still open from M1: the general tier on Groq for unknown medicines
   (`qwen3.5:4b` again described the made-up ZORBAXIL confidently), and
   the mobile team's review of contract v2.
5. `docs/` (outside `backend/`) still says MIMS and "verified".

---

## 2026-10-20 — M1: parse v2, scan rules, contract v2

**Branch:** `backend/m1-parse-scan-rules` (on top of M0).

**Decision (2026-10-20):** team-checked answers are called **"reviewed"**,
not "verified" (replaces decision 2 in `docs/project-status-and-decisions.md`,
which still says "verified"). Wording now in `app/speech.py`:
- reviewed tier: "Our team has reviewed this information."
- ingredient tier (open decision 2): "Our team hasn't reviewed this product
  yet. Based on its ingredients, ..." This was chosen over "This still needs
  a review", which could sound like the listener has to do something.
- web tier: "This medicine isn't in our reviewed list. The following
  information comes from the web and hasn't been reviewed by our team."
- v1 keeps `grounded` and the `"not verified"` warning value for older app
  builds; only its spoken text changed.

**Added**
- `llm_parse.parse_label()`: every product in view, each with
  `generics[]` and `strengths[]` paired by position. The OCR text is fenced in
  `<ocr_text>` tags (a copy of the tag inside the text is removed). The
  reply is checked for types and caps (5 products, 8 ingredients, 80/40
  characters, no ingredient that looks like a strength); any failure is a
  rescan. `PARSE_PROMPT_VERSION = "2"`.
- `scan_rules.py`: pure functions for input checks, name/strength-in-text,
  several medicines (with block heights to ignore small print),
  strength pairing, `partial_match_reason()` for M2, hints, retry limit
  (`gave_up` from attempt 3) and the OCR caveat. The model's own confidence
  no longer decides anything.
- `speech.py`: every spoken string, in the plan's fixed order, with
  strengths spoken in words ("500 milligrams") and names in their printed case.
- `POST /v2/identify` (contract in `README.md`): `blocks`, `attempt`,
  `capture_hints`, `X-Device-Id` (hashed), new statuses, `rescan_reason`,
  `hint`, `tier`, `product`, `sources`, `request_id`. **Additions to the plan's
  contract:** tier `general` (the model-knowledge fallback, which the plan
  had no value for) and reason `too_much_text`.
- Raw-text parse cache (approved 2026-10-19) and an answer cache keyed on brand
  + every (ingredient, strength) pair (`utils.answer_key`); `CacheStore` now
  has `parses` and `answers` tables. Cache errors fail open.
- Security items for M1 (plan section 7, items 3 and 10): a 64 KB body limit
  (413, also without Content-Length); a 500-block cap; a Groq timeout of 15 s
  (`LLM_TIMEOUT_S`); input checks before any model call (no letters, or over
  `MAX_PARSE_CHARS` = 2000).
- Code-enforced no-dosing gate on general descriptions (`utils.contains_dosing`)
  and a 300-character cap. Either one makes the result `failed`, not spoken
  as is and not cached.
- Ollama calls send `think: false`; without it `qwen3.5:4b` returned empty
  content (hidden reasoning used up the token budget).

**Changed:** `/identify` (v1) is now an adapter over the v2 pipeline:
`ok_caveat` → `low_confidence`, `multiple`/`gave_up` → `rescan`, combination
ingredients joined with " + ".
**Removed:** `confidence_handler.py`, `warnings_log.jsonl`,
`utils.normalize_key`, the `medicines` cache table (old SQLite files keep
it, unused). `LOW_CONFIDENCE_THRESHOLD` is still read as the fallback for
`OCR_CAVEAT_THRESHOLD`.

**Verified**
- `pytest`: 158 passed (each scan rule on realistic labels, the "every pair
  that shares an ingredient" key tests, speech snapshots, the v1 adapter, 413
  and 422 cases). `ruff` is clean.
- Real local models through `parse_label` + `scan_rules`, on 8 labels:
  Biogesic, NEOZEP Forte (3 ingredients), Alaxan FR, two boxes, an "also
  available" mention, an injection label, garbled text, and foil with no
  strength.
  - `llama3.2:3b` (2 runs) and `qwen3.5:4b` (2 runs): all 3 NEOZEP
    ingredients paired correctly, two boxes → `multiple`, the "also
    available" mention ignored, garbled → rescan.
  - Injection label: `llama3.2` put "500 mg" in the ingredient slot (now
    rejected by the reply checks). `qwen3.5` invented "Paracetamol" for
    "ZORBAXIL", which was caught as `name_not_in_text`. Neither was answered.
- End to end on `/v2/identify` with `qwen3.5:4b`: general answer with the
  warning first, cache hit with `ok_caveat`, NEOZEP with all three
  strengths spoken, and `gave_up` on foil with no strength at attempt 3.
- **Not verified:** Groq (no `GROQ_API_KEY` on this machine).

**Open items**
1. **General tier can still be confidently wrong.** `qwen3.5:4b` described the
   made-up "ZORBAXIL / Fakeamol 250 mg" as "a pain reliever and fever
   reducer" instead of "I don't recognize this product". It is spoken behind
   the warning, but re-check the prompt on Groq's `qwen/qwen3.8-27b` (which
   said "I don't recognize" on 2026-10-08) before release, and don't use a
   small local model for this tier.
2. **Contract v2 needs the mobile team's agreement** before merge, including
   the `general` tier, `too_much_text`, and resetting `attempt` after an answer
   or `gave_up`.
3. `MAX_PARSE_CHARS = 2000` makes a whole package insert a rescan. Check it
   against real phone scans.
4. `SMALL_PRINT_RATIO` (0.4) and `OCR_CAVEAT_THRESHOLD` (0.55) are guesses;
   set them from labeled scans (M2).
5. A brand-only read (foil showing only "BIOGESIC") is answered by the general
   tier. In M2, a brand that matches the dataset without a strength becomes
   `strength_unread`.

---

## 2026-10-20 — M0: foundations (tests, logging, defect fixes)

**Branch:** `backend/m0-foundations`. First milestone of
`docs/medsight-development-plan.md` (section 6, M0).

**Added**
- **Tests:** `tests/` with pytest, 31 tests. `conftest.py` replaces
  `llm_client.chat` with a `FakeLLM` in every test, so no test can call a
  real model, and points data and log paths at a temp folder.
  `tests/fixtures/fake_kb.py` writes fake `kb/` datasets for `kb_build` tests.
- **Tooling:** `pyproject.toml` (pytest and ruff settings),
  `requirements-dev.txt` (pytest, httpx2, ruff, pip-audit). Server packages
  in `requirements.txt` are now pinned. `pip-audit` found no known
  vulnerabilities.
- **Logging** (`app/logging_setup.py`): one JSON object per line on stdout,
  ASCII-escaped. `RequestIdMiddleware` gives each request an ID (the client's
  `X-Request-Id` if it is 1 to 64 safe characters, else a new one), returns
  it as a header and puts it on every log line, including lines written from
  the sync endpoint's worker thread. One line per request and one per scan;
  neither includes OCR text.
- **CI:** `ops/backend-ci.yml` (ruff, pytest, `kb_build --check
  --allow-empty`, pip-audit, gitleaks), with a checklist in `ops/README.md`
  for the DevOps engineer: copy it to `.github/workflows/`, add the required
  checks, the `kb/` CODEOWNERS line and a pip Dependabot entry.
- `kb_build.py --check --allow-empty`: an empty dataset is a warning, so CI
  can run before the team's rows land. It refuses to build with it.

**Fixed** (each has a test that failed on the old code)
- Failed descriptions were cached. `get_description()` now returns `status`
  (`ok`, `not_recognized`, `failed`) and the pipeline caches only `ok`.
- "500 mg", "500mg" and "500 MG" gave different cache keys.
  `utils.normalize_strength()` now joins number and unit and lowercases;
  different strengths or units still differ.
- Importing `app.config` created `cache_vault/`, `data/`, `logs/` and
  `config/`. Now `config.ensure_dirs()` does it at startup. `CacheStore` is
  created in the API's startup, and reads its path at call time.
- `scripts/main.py` kept every webcam capture. It now deletes each one after
  OCR unless `--save-captures` is passed.

**Removed:** `scripts/obsidian_cache.py`, `CACHE_VAULT_*`,
`PH_FDA_REFERENCE_DOMAINS`, `utils.safe_slug`. Remaining `print` calls in
server code became log calls.

**Verified:** `pytest` 31 passed; `ruff check .` clean;
`pip-audit -r requirements.txt` clean; a live `uvicorn` run returned the
client's `X-Request-Id`, 401 without a key, `rescan` on empty text, and wrote
only JSON lines to stdout and nothing into the repo.

**Not done here (outside `backend/`):** the workflow file, branch
protection, CODEOWNERS and Dependabot changes are in `ops/README.md` for the
DevOps engineer. Uvicorn's first two startup lines are still plain text on
stderr (before the app loads); M5 adds a log config for the start command.

---

## 2026-10-08 — Unverified medicines get a warned, general description

**Commit:** `72966aa`

**Decision:** a scanned medicine that isn't one of the 10 verified ones still
gets an LLM-generated general description, clearly marked as unverified. It
isn't refused. This replaces the `unsupported` refusal from the RAG plan, and
`AGENTS.md` safety rule 3 was updated to match.

**Behavior (`pipeline.py`):**
- **Spoken order:** the name, then the warning *"this medicine is not in my
  verified list, so this is general information only and may be
  inaccurate"*, then the description, then any reading caveat, then *"Please
  check the label or ask a pharmacist before taking it."*
- **Flags:** `grounded: false`, and `warnings` includes `"not verified"`. The
  old `"FDA verification"` warning is gone.
- **Status:** the capped (0.6) description confidence no longer counts as a
  low-confidence stage. `status` now reflects only how well the label was
  read, so a clearly read but unverified scan is `ok`. Before this change,
  every scan was `low_confidence`.
- **Safeguards kept:**
  - no dosing advice
  - "I don't recognize this product" instead of a guess
  - the 0.6 confidence cap
  - repeated reading-caveat advice merged into one sentence

**Verified on Groq (`qwen/qwen3.8-27b`):**
- A clear Biogesic scan was `ok` with the warning first.
- NEOZEP with OCR confidence 0.4 was `low_confidence`, with both caveats and
  no repeated advice.
- An invented product ("ZORBAXIL") got "I don't recognize this product".
- Empty text was still `rescan`.

**Contract change for the mobile team:** see the README section on
`grounded`. Plan part 2 still adds verified (`grounded: true`) answers for
the 10.

---

## 2026-10-08 — Verified dataset tooling (RAG plan, part 1)

**Commit:** `7ee8b38`

**Why:** descriptions must come from verified FDA data, not the LLM's memory.
The prototype is limited to **10 medicines**, using a dataset the team builds
by hand.

**Research behind the decision:**
- **FDA verification portal:**
  - It has identity data (registration number, brand, generic, strength,
    form, Rx/OTC, expiry) but **no indications**, and no bulk download.
  - Its search API is protected by Cloudflare Turnstile; one plain request
    got HTTP 429.
  - **We don't scrape or bypass it.** Each lookup is done by a person in a
    browser.
- **PNF Essential Medicines List PDF:** generic names, forms and strengths
  only; no indications, and most OTC combinations are missing.
- **The product box or insert:** FDA-approved labeling. It has the
  indications, and its printed registration number links it to the portal
  record.

**Added:**
- `kb/products.csv`, `kb/ingredients.csv`, `kb/passages.csv` (header-only
  templates) and `kb/eval/scans.jsonl` (empty). **No real data is entered
  yet;** the team fills these in from evidence.
- `kb/README.md`: step-by-step collection guide, column reference and
  two-person check.
- `kb_build.py`: validates the CSVs and builds `data/kb.sqlite` (tables plus
  an FTS5 search index, and a `kb_version` hash of the CSVs). Use `--check`
  to validate only.
  - **Errors** (the build is refused):
    - an empty required field
    - an empty `checked_by`, or the same person as `entered_by`
    - a different number of generics and strengths
    - an unknown generic or product
    - a product with no indications passage
    - an expired registration
    - a bad date, duplicate, wrong value, or an unquoted comma
  - **Warnings:** an unusual registration number format, or a product count
    other than 10.
- `kb_make_scans.py`: runs local OCR over dataset images and writes
  `kb/eval/scans_draft.jsonl`, so the test scans only need labels.
- `config.KB_DIR` and `config.KB_DB_PATH`. `evidence/` is gitignored.

**Verified:**
- Fake fixtures in a temp folder: the valid set builds and FTS search finds
  products by brand and ingredient; the invalid set produced 16 specific
  errors.
- The real empty `kb/` fails with "no products yet".
- `kb_make_scans.py` produced a draft line from a real Biogesic image.

**Next (part 2 of the plan):** `retrieval.py` (product matching), list-valued
parsing, description generation from passages only (with a citation check
and verbatim fallback), new `unsupported` status, API and README contract
update, and `eval_kb.py`. This needs the first real rows in `kb/` to test.

---

## 2026-10-07 — Default Groq model switched to Qwen

**Commit:** `5fb21f2`

**Why:** the first real Groq test failed with HTTP 404 `model_not_found`.
`llama-3.3-70b-versatile` is no longer offered (open item 5 from 2026-10-04).

**Change:** the default `GROQ_MODEL` is now `qwen/qwen3.8-27b`, in
`config.py`, `.env.example` and `README.md`.

**Model comparison** (run through the real `llm_parse` and
`description_agent` code):

| Model | Result |
|---|---|
| `qwen/qwen3.8-27b` | All checks passed; fastest at about 0.6–0.9 s per call. **Chosen.** |
| `openai/gpt-oss-20b` | All checks passed; about 0.8–1.2 s per call |
| `openai/gpt-oss-120b` | One parse failed with HTTP 400 `json_validate_failed` |

**Qwen reliability:** 15 parse calls (5 sample texts × 3 runs) produced 0
failures. Garbled text came back with every field empty and confidence 0.1,
which the pipeline turns into `rescan`.

**New open items found in testing:**
1. **Combination medicines lose ingredients.** For NEOZEP Forte (phenylephrine
   + chlorphenamine + paracetamol), the parse returned only
   `generic_name: "Paracetamol"` at confidence 0.95. For ALAXAN it listed both
   ingredients. This conflicts with safety rule 2. A likely fix is to make
   `generic_name` and `dosage` lists in the parse schema.
2. **OCR errors pass through with high confidence.** `"Cetiri ine
   Dihydrochloride"` was returned as-is at confidence 0.9. The
   anti-hallucination check doesn't catch it, because the text really is in
   the OCR output. Phase B registry matching would correct it.

**Available Groq models** (as listed for this key on 2026-10-07): chat models
`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b` and
`allam-2-7b` (4k context); the rest are speech and guard models. To check
again, call `GET https://api.groq.com/openai/v1/models` with the key.

---

## 2026-10-04 — Phase 2 backend (FastAPI + Groq)

**Commit:** `4251fdf` (pushed to `origin/main`), on top of `b0b1ecd`, the initial
prototype commit.

### Why

MedSight is moving from a laptop-webcam prototype to a React Native app built
with Expo (development build).
- The **phone** runs OCR on the device (Apple Vision on iOS, Google ML Kit on
  Android) and text-to-speech (`expo-speech`). Only text leaves the phone,
  never photos.
- The **backend** in this repo handles everything after OCR.
- This repo's owner builds the backend; teammates build the app in a separate
  repo.
- Blister pack foil is in scope.

Decisions made in this session:

| Topic | Decision |
|---|---|
| LLM | **Groq** (OpenAI-compatible API, JSON mode). Ollama kept for offline work. |
| Deployment | Cloud host (not chosen yet) |
| Mobile | Expo dev build. Expo Go can't load the native OCR modules. |
| "Stubs" | Blister pack foil |
| Cache | SQLite. The Obsidian-vault cache isn't safe with concurrent requests. |
| Auth | Shared secret in the `X-API-Key` header. Fails closed if the server key is unset. |

### Architecture now

```
[Phone] camera → on-device OCR → POST /identify {text, lines[], ocr_confidence?, ...}
[api.py] → pipeline.identify():
    OCR confidence check (skipped if null) → parse (llm_parse via llm_client)
    → cache (cache_store, SQLite) → description on miss (description_agent)
    → confidence checks → spoken_text
[Phone] ← {status, brand/generic/dosage, description, grounded, warnings, spoken_text, cache}
        → speaks spoken_text exactly as sent
```

The full request and response contract is in `README.md` under "API contract".

### Files

**New**
- `api.py`: FastAPI app.
  - `GET /health` and `POST /identify`, with Pydantic request and response
    models.
  - The API-key check uses a constant-time comparison.
  - `LLMUnavailableError` becomes a 503 with a spoken retry message.
  - Request text is capped at 5000 characters; longer text returns 422.
- `pipeline.py`: `identify(raw_text, ocr_confidence, cache, meta)`, which is
  everything after OCR. It was pulled out of `main.process_frame()`, and
  `api.py` and `main.py` both use it.
  - Status is `ok`, `low_confidence` or `rescan`.
  - It returns `rescan` when no text was read or no name was found.
  - It adds a period to the description when the model leaves it off, so the
    spoken caveat is a separate sentence.
- `llm_client.py`: `chat(system, user, schema, max_tokens)` for Groq or
  Ollama, chosen by `LLM_PROVIDER`.
  - Groq uses `response_format: json_object`, with the schema appended to the
    system prompt.
  - Ollama is the old `ollama_client.py` logic.
  - A 429 rate limit, network error or missing key raises
    `LLMUnavailableError`.
- `cache_store.py`: `CacheStore`, a SQLite cache with the same `get`/`set`
  interface as `ObsidianCache`. It uses WAL mode and opens a new connection
  for each call. The database lives at `$MEDSIGHT_DATA_DIR/cache.sqlite`.
- `requirements-local.txt`: the webcam, OCR and TTS packages (OpenCV, PaddleOCR,
  EasyOCR, pyttsx3). It includes `requirements.txt`.
- `.env.example`: lists the current environment variables.
- `CLAUDE.md`: guidance for Claude Code. It imports `.agents/AGENTS.md`.

**Modified**
- `config.py`:
  - added `LLM_PROVIDER`, `GROQ_API_KEY`, `GROQ_MODEL`
    (default `llama-3.3-70b-versatile`), `GROQ_BASE_URL`,
    `MEDSIGHT_API_KEY` and `MAX_OCR_TEXT_CHARS`
  - added `MEDSIGHT_DATA_DIR` (default `data/`) and `MEDSIGHT_LOGS_DIR`
    (default `logs/`)
  - removed the outdated Gemini comment
- `main.py`: still the webcam test tool. It now runs local OCR and then
  `pipeline.identify()`, and uses `CacheStore`. The docstring's fallback engine
  is corrected to `paddleocr`.
- `llm_parse.py` and `description_agent.py`: now use `llm_client` instead of
  `ollama_client`. Docstrings updated.
- `confidence_handler.py`: the ⚠️ emoji is now `[!]`. Printing the emoji on a
  non-UTF-8 stdout raised `UnicodeEncodeError` and failed the whole request.
- `requirements.txt`: server only (`fastapi`, `uvicorn`, `python-dotenv`,
  `requests`). Earlier in the session, `requests` and `easyocr` were added
  because they were missing.
- `.agents/AGENTS.md`:
  - The scope section is now Phase 2 (backend). The "Do not build Phase 2"
    rule was lifted for the backend.
  - The project structure, commands and gitignored folders are updated.
  - The benchmark command now includes `<dataset_root>`.
- `README.md`: rewritten. It now covers the backend, the environment variable
  table, the API contract for the mobile team, the local prototype and known
  limitations. Gemini is no longer mentioned.

**Removed**
- `ollama_client.py`: replaced by `llm_client.py`.

**Unused but kept**
- `obsidian_cache.py`: the old markdown-vault cache. Not used by the
  pipeline, but kept in case an export-to-Obsidian script is wanted later.

**Installed in `.venv`:** `fastapi` 0.142.2 and `uvicorn` 0.54.0.

### Verification done

Ran with `LLM_PROVIDER=ollama` (`llama3.2:3b`) through FastAPI's
`TestClient`. Temporary data and log directories kept the tests out of the
real cache and logs.

| Check | Result |
|---|---|
| `GET /health` | 200 |
| No API key or wrong key | 401 |
| Server API key unset | 503 |
| Empty text | 200, `rescan`, "couldn't read any text" |
| `"BIOGESIC\nParacetamol\n500 mg Tablet"` | 200, Biogesic / Paracetamol / 500 mg, cache `miss` |
| Same request again | cache `hit` |
| `lines` only, `ocr_confidence: null` | 200; OCR check skipped and logged |
| Garbled text | `rescan`, "couldn't identify a medicine name" |
| Text over 5000 characters | 422 |
| Groq selected with no key | 503, `service_unavailable` with a spoken retry message |
| Live `uvicorn api:app` | `/health` OK; `/identify` without a key returns 401 |
| `import main` | OK |

**Not verified:** a real Groq call (no `GROQ_API_KEY` in `.env` yet), and
`main.py` with a webcam.

### Known issues and open items

1. **Descriptions aren't FDA-grounded.** `grounded` is always `false` and
   confidence is capped at 0.6, so every reply includes the "FDA verification"
   caveat. In testing, NEOZEP Forte (a combination cold medicine) was
   described only as "a pain reliever and fever reducer", which is incomplete.
   This is the highest-priority fix: see Phase B below.
2. **Every fresh scan is `low_confidence`.** The local `.env` sets
   `LOW_CONFIDENCE_THRESHOLD=0.85`, which is above the 0.6 description cap.
   Lower the threshold, or accept this until Phase B.
3. **Dosage spacing splits the cache.** `normalize_key` treats "500 mg" and
   "500mg" as different, so some repeat scans miss the cache.
4. **Low confidence doesn't stop a scan.** `AGENTS.md` safety rule 1 says low
   confidence should mean "rescan". The pipeline keeps the older behavior:
   it reports `low_confidence` and speaks a caveat. Decide whether a
   low-confidence name extraction should return `rescan`.
5. **Groq model name not confirmed.** Check the `GROQ_MODEL` default against
   Groq's current model list before deploying.
6. **Cloud storage.** Many hosts wipe local disk on redeploy. Mount a
   persistent disk and set `MEDSIGHT_DATA_DIR` and `MEDSIGHT_LOGS_DIR`.
7. **Old cache not migrated.** The two entries in `cache_vault/` weren't
   copied into SQLite.
8. **Leftover folder.** There's an empty stray directory named
   `{cache_vault,logs,config}` in the repo root, from an unexpanded `mkdir`.
   It's safe to delete.
9. **Old keys in `.env`.** It still contains the unused `GEMINI_*` keys.

### Next steps

- **Phase B, FDA grounding:**
  - Load the PH FDA registered drug products list into SQLite. First check
    whether a bulk download exists, since it may need to be scraped from the
    verification portal.
  - Fuzzy-match the parsed brand and generic names against it. This helps
    with partial names on blister foil.
  - On a confident match, set `grounded=true`, correct the name to the
    registered spelling, and keep the registration number as the source.
- **Deploy:** add `GROQ_API_KEY` and `MEDSIGHT_API_KEY`, choose a cloud host,
  attach a persistent disk, and run
  `uvicorn api:app --host 0.0.0.0 --port $PORT`.
- **Mobile team:** share the API contract in `README.md`, then confirm:
  - whether the chosen OCR library reports per-line confidence on both
    platforms (send `null` if not)
  - multi-frame text merging and "move closer / reduce glare" prompts for
    blister foil
- **Commit:** commit this work once it has been reviewed.
