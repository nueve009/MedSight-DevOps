# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@.agents/AGENTS.md

The imported `AGENTS.md` holds the project overview, scope limits, medical
safety rules and general rules. This file adds what that one doesn't cover.
`README.md` has the API contract the mobile app depends on: keep it in sync
with `app/api.py`. `CHANGES.md` records past changes, decisions, verification
results and open items. Read it at the start of a session, and add a new
dated entry at the top after significant work.

## Commands

Run everything from the `backend/` folder. `scripts/main.py --save-captures`
writes `captures/` relative to the working directory.

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt                       # server + pytest, ruff, pip-audit
pytest                                                    # all tests; the LLM is always faked
ruff check .                                              # lint
uvicorn app.api:app --reload                          # backend; docs at /docs with API_DOCS=on
python -m scripts.main [--save-captures]                  # webcam loop: SPACE = capture + run pipeline, q = quit
python -m scripts.benchmark_ocr <dataset_root>            # dataset path is required
python -m scripts.sort_packaging_images <dataset_root> <output_dir> [--min-text-regions 3]
python -m scripts.kb_build --check [--kb-dir <folder>]    # validate the reviewed dataset in kb/
python -m scripts.kb_build                                # validate + build data/kb.sqlite
python -m scripts.kb_build --push --kb-dir <folder>       # import CSV drafts into Supabase, unapproved (KB_ADMIN_DATABASE_URL)
python -m scripts.kb_build --pull [--csv-out kb]          # approved Supabase rows -> data/kb.sqlite (+ CSVs)
python -m scripts.kb_draft_descriptions                   # LLM drafts of spoken descriptions -> kb/staging/
python -m scripts.eval_kb [--out report.json]             # labeled scans; exit 1 on any wrong reviewed answer
python -m scripts.kb_make_scans sorted_output/packaging   # draft eval scans from images
python -m scripts.migrate [--check]                       # apply migrations/ to Supabase (MIGRATION_DATABASE_URL)
python -m scripts.store_admin export --out <dir> | restore <file> | purge   # nightly backup and cleanup
python -m scripts.smoke_test --url <base url>              # after a deploy; key in SMOKE_API_KEY
```

`kb/staging/` (git-ignored) holds the team's raw scraper output and the
description review sheet. The TheFilipinoDoctor scraper
(`scripts/tfd_scrape.py`, `tests/test_tfd_scrape.py`) is the team's local
tool and **is never committed**: stage files by explicit path, and exclude
it from lint (`ruff check . --exclude scripts/tfd_scrape.py --exclude
tests/test_tfd_scrape.py`).

The hand-curated, two-person-checked ("reviewed") dataset of the supported
medicines lives in Supabase (`kb_ingredients`, `kb_products`, `kb_passages`,
migration 0005); a second teammate approves each row in the Table Editor.
`kb/` holds the same columns as CSVs (draft imports and a git copy), and
`kb/README.md` explains how it's collected. **Never add, edit or approve
rows in `kb/*.csv` or the `kb_*` tables yourself.** Every value must come from the team's box
photos, the TheFilipinoDoctor pages and FDA portal values. Use fake data in a
temp folder for tests.

**FDA portal lookups (rule changed by Mon, 2026-11-10).** A script may look up
the FDA values the dataset needs (`fda_reg_no`, `reg_expiry`,
`classification`, `manufacturer`, `portal_checked_on`) on
https://verification.fda.gov.ph, within these limits:
- only the products in the team's medicine list (about 30 brands), never a
  crawl of the whole portal
- at least 5 seconds between requests, a clear user agent, and results cached
  in `kb/staging/` so each product is looked up once
- run locally by the team, never from the deployed backend or at scan time
- output goes to `kb/staging/` drafts, then into Supabase as unapproved rows;
  a reviewer still confirms each value on the portal before approving
- stop and report if the portal adds terms forbidding it, a login or a CAPTCHA

Checked 2026-11-10: the portal's robots.txt allows every path, and no terms of
use were found; the fda.gov.ph footer says "All Rights Reserved".

**Tests** live in `tests/` (pytest). `tests/conftest.py` replaces
`llm_client.chat` with a `FakeLLM` in every test (a test that doesn't set a
reply fails rather than calling a real model) and points the data and log
paths at a temp folder. Fake `kb/` datasets come from
`tests/fixtures/fake_kb.py`, never from the real `kb/`. Every bug fix starts
with a test that fails on the old code. To try the real model by hand, use
`TestClient` with `LLM_PROVIDER=ollama`, or call one stage directly, e.g.
`llm_parse.parse_label(text)`. Realistic OCR texts and parse replies for tests
are in `tests/fixtures/labels.py`.

## Architecture

The phone does OCR and TTS. `app/api.py` receives the text: `/v2/identify`
calls `pipeline.identify_scan(ScanRequest, cache)`, and the v1 `/identify`
calls `pipeline.identify()`, an adapter that runs the same pipeline and maps
the result to the v1 shape. `scripts/main.py` (webcam) uses `identify()` too.
`pipeline.py` only orchestrates; each step is its own module:

1. **Input checks** (`scan_rules.input_reason`). No letters → `no_text`; more
   than `MAX_PARSE_CHARS` → `too_much_text`. Both are rescans with no LLM call.
2. **Raw-text parse cache** (`Store.get_parse`), keyed by
   `utils.raw_text_key` (normalized OCR text + model + `PARSE_PROMPT_VERSION`).
   Bump `PARSE_PROMPT_VERSION` whenever the parse prompt or schema changes.
   Only valid, non-empty parses are cached.
3. **Parse** (`llm_parse.parse_label`). Returns every product in view as
   `ParsedProduct(brand, generics, strengths)`; strengths are paired with
   generics by position, `""` = unreadable. The OCR text goes in
   `<ocr_text>` fences (`utils.fence_untrusted`). The reply is checked for
   types and caps (`MAX_PRODUCTS`, `MAX_INGREDIENTS`, name lengths, an
   ingredient that looks like a strength); any failure is `valid=False` →
   rescan `no_name`. The model's own confidence is logged, never used to decide.
4. **Scan rules** (`scan_rules.decide`, pure functions). Hard rescans: no
   product, a name or strength number not in the OCR text
   (`name_not_in_text` / `strength_unread`), strengths that don't pair with
   ingredients. Then `multiple` (two brands, two strengths of one brand, or an
   unbranded product with other ingredients; `blocks` heights drop small-print
   mentions). From attempt `MAX_SCAN_ATTEMPTS` a rescan becomes `gave_up`.
   An answer is `ok_caveat` when the phone's OCR confidence is below
   `OCR_CAVEAT_THRESHOLD` (`None` = skipped, not 0).
4b. **Dataset match** (`retrieval.KnowledgeBase.match`, on `app.state.kb`).
   `KB_BACKEND` (default: `CACHE_BACKEND`) picks the source: `sqlite` loads
   `data/kb.sqlite` once (missing file = empty dataset); `postgres`
   (`app/kb_source.py`) reads the approved `kb_*` rows at startup and every
   `KB_REFRESH_S` in a background thread (`KBRefresher`), re-checks them with
   `app/kb_validate.py` (a row that breaks a rule, and rows that need it, are
   dropped and logged), and swaps the new dataset in only when its version
   changes. A failed load keeps the current dataset. The database trigger
   `kb_review_guard` enforces the approval rules (two people, rules pass,
   edits un-approve); the backend role sees approved rows only (RLS).
   `reviewed`: exact brand or alias (or fuzzy brand ≥0.85) **and** the same
   ingredient set and strengths, after synonyms (`name_key`, `brand_key`,
   `normalize_strength`) → spoken with the dataset's spelling, its
   `spoken_description`, and `speech.RX_LINE` for `Rx`. `partial`: an
   **exact** listed brand whose contents disagree → rescan via
   `scan_rules.partial_match_reason` (never for a fuzzy brand, so a near
   miss never names a listed brand). `ingredient`: every ingredient has a
   reviewed `spoken_description`. Several full matches with different
   descriptions or Rx status are not answered as reviewed. Reviewed and
   ingredient answers never use the answer cache or an LLM.
5. **Answer cache** (`Store.get_answer`, general and web tiers), keyed by `utils.answer_key`:
   brand + every (ingredient, strength) pair, sorted, with strengths
   normalized (`utils.normalize_strength`: "500 mg" = "500mg" = "500 MG").
   A combination never shares a key with one of its ingredients.
5b. **Web tier** (`web_search.lookup`, M6; off unless `WEB_SEARCH_PROVIDER=tavily`
   and `TAVILY_API_KEY`). It runs for a branded, unlisted product with no cached web
   answer and no live "not found" marker (`web-miss:` keys in the parse cache,
   `WEB_MISS_TTL_DAYS`). `Limiter.spend_web_lookup` caps lookups per device and per
   day. Past a cap it raises `WebLimitReached`, and the scan gets the general tier.
   - One Tavily search, restricted to `web_search.ALLOWED_SITES`. `site_name()`
     drops anything else in code: https only, no port, exact hosts.
   - The page must name the brand and every ingredient.
   - At most 2 pages go to Groq in `<source_N>` fences, 4,000 characters each.
   - Gates on the summary: no dosing, 300 characters at most, not "recognize",
     and every ingredient of a combination named.
   - Found: cached as `web` / `pending_review` (30-day expiry) and spoken as
     "According to <site>, ...". Not found or a failed gate: a "not found" marker,
     then the general tier. A search outage caches nothing.
   - Prescription (2026-11-10): `WebAnswer.rx` needs the model's `prescription: true`
     **and** the chosen page's own wording (`page_states_rx`). It is stored as
     `rx` in the answer's `product` JSON and spoken after the web warning,
     attributed to the site. The ingredient and general tiers never say it.
   - The server never fetches pages itself: the text comes from the provider.
   - `tests/conftest.py` replaces `web_search.search` with `FakeSearch`.
6. **Describe** (`description_agent.get_description`, the `general` tier),
   when there's no web answer and no cached answer. `status` is `ok`, `not_recognized` or `failed`; a
   reply with a dose or schedule (`utils.contains_dosing`) or over 300
   characters is `failed`. Only `ok` is cached. The team decided
   (2026-10-08) that medicines outside the reviewed dataset still get this
   warned description; they are not refused.
7. **Speech** (`speech.py`). Every spoken string lives here, with snapshot
   tests in `tests/test_speech.py`. Order: name and strength, prescription
   line, tier line (the warning comes **before** the description), the
   description, the reading caveat, the pharmacist advice. Team-checked
   answers are called **"reviewed"**, never "verified" (decision 2026-10-20);
   v1 keeps its old `"not verified"` warning value for older app builds.
   Internal names use `rx`, not "prescription", because CodeQL flagged the
   old constant name as sensitive data (false positive, PR #23).

Cache errors never fail a scan (`pipeline._cache_call` logs and carries on).

**Store** (`app/store/`): one interface, two versions, picked by
`CACHE_BACKEND`. `SQLiteStore` (default; tests, laptops, `eval_kb.py`; scan
log = `logs/run_log.jsonl`) and `PostgresStore` (Supabase on Railway; scan
log = the `scan_log` table, OCR text in its own column, cleared after 30 days
by `scripts/store_admin.py purge`). Postgres fails open fast: 2 s connect,
1 s per query, then the database is skipped for 60 s (`CircuitBreaker`,
`StoreSkipped`, logged once). Only `general` and `web` answers are stored;
`rejected` and expired ones are a miss; web answers expire after 30 days.
Tables come only from `migrations/` (`python -m scripts.migrate`, owner
connection in `MIGRATION_DATABASE_URL`); see `migrations/README.md` for the
backend role and the restore steps. `tests/test_store.py` runs the same
suite on SQLite and, when `MEDSIGHT_TEST_DATABASE_URL` is set, on Postgres.

**Rate limits** (`app/ratelimit.py`, M4): `api.GUARDS` runs the API-key
check (several named keys: `MEDSIGHT_API_KEYS`) and then
`Limiter.check_request` (per device and per address, in fixed UTC minute/day
windows; every request counts) before the pipeline. The pipeline calls its
`budget` callable before each model call (`Limiter.spend_llm_call`: global
`LLM_DAILY_BUDGET`, optional per-key caps), which raises
`LLMBudgetExceeded`, a subclass of `LLMUnavailableError`. All of these give
a 429 with `reason`, `spoken_text` and `Retry-After`. Counters live in the
store's `rate_limits` table, with `MemoryCounters` as the fallback when the
store fails, so an outage never turns the limits off. Buckets hold hashes,
and the window length is part of the bucket name. A `BoundedSemaphore`
(`MAX_CONCURRENT_SCANS`) gives a quick 503 when too many scans run at once.
The client address comes from `CLIENT_IP_HEADER` (`x-real-ip` on Railway).

**LLM provider** (`app/llm_client.py`):
- **Groq:** calls the OpenAI-compatible `/chat/completions` endpoint with
  `response_format: json_object`. JSON mode doesn't enforce field names, so
  the schema is also appended to the system prompt.
- **Ollama:** passes the schema in `format`, which enforces it, and
  `think: false`, without which thinking models (qwen3.5) return empty content.
- **Errors:** both raise `LLMUnavailableError` for unreachable services, rate
  limits, timeouts (`LLM_TIMEOUT_S`, Groq) and bad keys. `app/api.py` maps it
  to a 503 with a spoken retry message and the request ID.

**Admin** (`app/admin.py`, M7): the `/admin` routes list, read, update (status
and description only) and delete cached answers, and read `audit_log`.
- Off unless `ADMIN_ENABLED`. `api.RouteGateMiddleware` 404s every `/admin`
  path, and `/docs` without `API_DOCS`.
- Per-person `ADMIN_KEYS` (constant time). An admin key equal to an app key is
  ignored.
- `Store.admin_update_answer` / `admin_delete_answer` write the change and its
  audit row in one transaction. Admin writes don't fail open: they give a 503.
- The tier can't be changed, so admins can never set "reviewed".
- `tests/test_drills.py` holds the failure drills; `docs/runbook.md` the manual
  steps.

**Auth** (`api.require_api_key`): checks the `X-API-Key` header against
`MEDSIGHT_API_KEY` using a constant-time comparison. If the server key isn't
set, it fails closed with a 503. `BodySizeLimitMiddleware` refuses bodies
over 64 KB with 413, with or without a Content-Length.

**Logs** (`app/logging_setup.py`): server code uses `logging`, never
`print`. Each line is one JSON object on stdout, with the request's ID
(`RequestIdMiddleware`; also returned as the `X-Request-Id` header). Extra
fields go in `extra={"fields": {...}}`. Output is ASCII-escaped, because a
non-UTF-8 stdout would otherwise raise `UnicodeEncodeError` and fail the
request. Never log keys, headers or request bodies.

**Local OCR** (prototype only): `scripts/ocr_engines.py` uses the **PaddleOCR 3.x
API** (`.predict()`, `results[0].json["res"]["rec_texts"/"rec_scores"]`).
Don't revert it to the 2.x `.ocr()` call or tuple format. When
`config/ocr_engine_choice.json` is missing, it falls back to `paddleocr`.

## Config gotchas

- Importing `app/config.py` creates nothing on disk. `config.ensure_dirs()`
  creates `data/` and `logs/`; the API calls it at startup and the scripts
  call it themselves. Settings read at call time (`config.X`), not bound as
  default arguments, so tests can monkeypatch them.
- `ops/` holds files for outside `backend/` (CI workflow); see `ops/README.md`.
- `.env` may still contain the old `GEMINI_*` keys. Nothing reads them;
  `.env.example` lists the current variables.
- The README and code mention "handoff Sections 5–7" in
  `medicine-scanner-handoff.md`, which isn't in this repo.
