# MedSight Backend Development Plan

Oct 19, 2026 · Reymond Ferrero

Exported from the [Claude Doc](https://claude.ai/code/artifact/da749243-5380-446e-b419-d6b6af190270) on 2026-10-20 and **updated in `backend/docs/` on 2026-10-27** with the decisions and progress since then (ticked boxes and **Status** lines in section 6). If the Claude Doc stays the version to edit, copy these changes back to it.

## 1. Purpose and ground rules

This plan takes the MedSight backend from today's prototype on `main` (commit `0a8144d`) to a pilot-ready service on Railway with Supabase. It covers eight milestones, M0 to M7, plus one optional milestone, M8 (voice chat), that starts only after the pilot gate is met. Each one ends with acceptance criteria that a reviewer can check. It reflects the team's decisions of 2026-10-19 and replaces the 10-phase plan of 2026-10-12.

**Progress (2026-10-27):** M0 and M1 are merged into `main` (PR #23). The backend part of M2 is on branch `backend/m2-dataset-matching`; M2 finishes when the team's checked dataset and labeled scans are in.

**Decisions since 2026-10-19:** team-checked answers are called **"reviewed"**, not "verified" (2026-10-20). **TheFilipinoDoctor** replaces MIMS as the dataset source and is on the web allowlist; MIMS is dropped because its drug directory needs an account (2026-10-26). The team's scraper stays out of git (2026-10-26). The backend developer does backend work only; CI, branch protection, CODEOWNERS, Dependabot and Railway setup belong to DevOps (2026-10-20).

**What the service does:** it receives OCR text from the phone, works out which medicine is in view, and returns one short spoken answer. That answer is either checked by the team ("reviewed"), built from checked ingredient descriptions, or taken from an allow-listed web search (until then, a general description) behind a spoken "not reviewed" warning.

**Rules every change follows:**

1. **A wrong confident answer is the worst failure.** When in doubt, the service asks for a rescan or says what it couldn't read. It never fills a gap with a guess (`AGENTS.md`, safety rules 1 to 5).
2. **Only team-checked text is spoken as "reviewed".** Anything generated or fetched at scan time is labelled "not reviewed", and that warning is spoken before the description.
3. **No dosing advice** in any tier, enforced in code and not only in the prompts.
4. **The cache fails open.** A database problem must never fail a scan; it only costs an extra LLM call.
5. **No secrets in git.** Every key lives in Railway's environment variables or a local `.env`.
6. **Every behaviour change ships with tests,** and every response-shape change updates `README.md` and is agreed with the mobile team before merge.
7. **One pull request per task,** small enough to review in one sitting, merged only when CI is green.

**Out of scope:** user accounts and login (data stays on the phone), storing images, dosing or interaction advice, smart glasses, and on-device LLMs. Voice chat (M8) is not part of the pilot.

## 2. Baseline: what `main` has today

`main` is a working single-product prototype of about 1,580 lines of Python. It has no tests, no CI, no deployment, and an empty dataset.

| Area | Today (file) | Gap this plan closes |
| --- | --- | --- |
| API | `POST /identify`, `GET /health`, `X-API-Key` checked in constant time, fails closed if unset (`api.py`) | No rate limit, no device ID, no request ID, no versioned contract |
| Parse | One product: brand, one generic, one dosage; a check that the name appears in the OCR text (`llm_parse.py`) | Combinations and several products in view are lost; OCR text isn't fenced against prompt injection |
| Rescan rules | Only "no text" and "no name"; one threshold of 0.55 on the model's own score (`confidence_handler.py`) | Rules from code-checkable signals, hints, retry limit |
| Dataset | Validator and SQLite builder, 260 lines (`kb_build.py`); CSVs are header-only | 29 products and their ingredients entered and checked; matching code |
| Description | LLM from memory, always unverified, capped at 0.6 (`description_agent.py`) | Reviewed and ingredient tiers from `kb/`; allow-listed web search |
| Cache | SQLite, key = hash of parsed fields (`cache_store.py`, `utils.py`) | Supabase; key normalization; failures are cached today |
| Logs | JSONL files on local disk (`utils.append_jsonl`) | Structured logs to stdout; retention; no raw logs on Railway's disk |
| Dead code | `obsidian_cache.py`, `CACHE_VAULT_*` in `config.py`, `PH_FDA_REFERENCE_DOMAINS` (unused) | Removed |

**Known defects to fix first (M1):**

- A failed description ("I could not generate a description") is cached like a good one, so a temporary LLM error sticks for every later scan (`pipeline.py`, cache `set` on every miss).
- "500 mg" and "500mg" produce different cache keys (`utils.normalize_key`).
- `config.py` creates folders when imported, which makes tests write into the repo.
- `main.py` saves every webcam capture to `captures/` with no clean-up.

## 3. Target architecture

One request runs five steps on Railway. The checks in steps 1 to 3 can end it early with a spoken rescan, before any matching or web search is paid for.

```
Phone app: OCR text, block positions, device ID
   |
   v
Railway: FastAPI backend
   1. Auth and rate limits ........ app key, device and address limits   <-> Supabase (rate counters, cache, scan log)
   2. Parse the label ............. every product, ingredients, strengths <-> Groq LLM (reads the OCR text)
   3. Scan rules .................. rescan, several medicines, retry limit
        | (rescan: skip to step 5)
   4. Match and describe .......... reviewed, then ingredient, then web   <-> kb/ dataset in git (29 products and ingredients)
                                                                          <-> Allow-listed web search (TheFilipinoDoctor and approved sites)
   5. Build spoken text ........... prescription line and warnings first
   |
   v
Phone speaks the answer (text-to-speech; "My medicines" stays local)
```

In step 4, a scan tries the most trusted source first: a reviewed product match from `kb/`, then checked ingredient descriptions, then the allow-listed web search behind a "not reviewed" warning. The `kb/` dataset is built into the image at deploy time, so reviewed answers never depend on the database or the web. Since M2, reviewed and ingredient answers are served from `kb/` in memory and never cached, so a dataset change takes effect at once.

**Code layout** (new modules in bold): `api.py` (routes, auth), **`ratelimit.py`**, `pipeline.py` (orchestration only), `llm_parse.py`, **`scan_rules.py`**, **`retrieval.py`**, `description_agent.py` (web tier), **`web_search.py`**, **`speech.py`**, **`store/`** (SQLite and Postgres), `llm_client.py`, `kb_build.py`, **`migrations/`**, **`tests/`**, **`scripts/smoke_test.py`**. Each module has one job, so each can be tested on its own.

### Models and call budget

Decided 2026-10-19: no model is hosted on Railway. Every model call made during a scan goes to an API, so nothing costs money while the app is idle.

| Job | Model | When it runs | Plan and cost |
| --- | --- | --- | --- |
| Read the label (parse) | Groq `qwen/qwen3.8-27b` | Every scan that passes the input checks | Free tier now: 30 requests a minute, 1,000 requests and 200,000 tokens a day, shared by the whole Groq account, so about 200 scans a day. Developer plan once pitching starts, at about $0.001 a scan |
| Descriptions of the 29 products and their ingredients | A local model run once on a laptop with `scripts/kb_draft_descriptions.py` (for example Ollama; tried with `qwen3.5:4b`) | Once, offline, before each dataset release | The team checks every sentence against the TheFilipinoDoctor source text; stored in `kb/` and spoken as reviewed |
| Unlisted medicines (web tier) | Claude Sonnet 5.5 with the web search tool, allowlisted sites only | Cache miss for an unlisted medicine, after the scan rules pass | Paid per call, under the caps below |
| Fallback when the web tier can't run | Groq, the same model as parsing | Web cap reached, or the search failed its checks | The `general` tier: today's general description, spoken behind its warning |

A self-hosted 3B to 4B model on Railway was compared and rejected: about $45 to $60 a month for a CPU-only service, an estimated 5 to 15 seconds per scan, and weaker reading of combination labels. The Groq limits come from a third-party summary read 2026-10-16 ([source](https://benchlm.ai/md/free-tier/groq.md)), so check the limits page in the Groq account before relying on them.

**Guardrails against scripted testing.** Testers will send requests with scripts, so every model call has a budget:

1. Input checks before any model call: refuse empty text, text with no letters, and text over about 2,000 characters.
2. A raw-text cache in front of the parse, keyed on a hash of the normalized OCR text, so the same label sent again costs no Groq call. This adds a cache lookup before parsing (approved by Mon, 2026-10-19). Combination safety: the answer cache key holds the brand plus every ingredient and strength, sorted, never one ingredient alone, so Alaxan FR (ibuprofen + paracetamol) can never be served Biogesic's (paracetamol) answer. A combination brand read with fewer ingredients than the dataset lists is a rescan, and an unlisted product gets the ingredient tier only when every ingredient it lists has a checked description. Tests cover each pair of medicines in the list that share an ingredient.
3. A global daily budget of about 180 Groq calls, under the free limit. Past it, the user hears "The service is busy. Please try again later."
4. Separate API keys for the pitch devices, with a reserved share of the daily budget, and one per tester group, so testers can't use up the demo's quota and a group can be cut off.
5. The web tier runs only for an unlisted name that also appears in the OCR text, with about 3 lookups per device a day, about 20 a day overall, and at most 2 searches and a short reply per call.
6. Web answers are shared with every user as `pending_review` with a 30-day expiry (Mon, 2026-10-19). "Not found" results are cached too, so made-up names can't trigger repeat searches.
7. A monthly spend limit in the Anthropic console is the final backstop. The team is alerted at 80% of either daily budget.
8. Day-to-day development uses local Ollama, because the free Groq quota is shared by the whole account.

## 4. Data model

Curated medicine facts live in git; everything that changes at runtime lives in Supabase. That split keeps every reviewed fact checkable in a pull request, while the cache, counters and logs can grow and be wiped without touching what users hear as reviewed.

**In git (`kb/`, built into `data/kb.sqlite` at deploy time by `kb_build.py`):**

| File | One row per | Key columns | Added by this plan |
| --- | --- | --- | --- |
| `products.csv` | product (brand, strength, form) | `product_id`, `brand_name`, `aliases`, `generic_names`, `strengths`, `classification`, `fda_reg_no`, `reg_expiry`, `entered_by`, `checked_by` | `spoken_description` (the LLM-written, team-checked sentence), `description_checked_by` |
| `ingredients.csv` | active ingredient | `generic_name`, `synonyms`, `pnf_listed` | `spoken_description`, `source_url`, `entered_by`, `checked_by` |
| `passages.csv` | TheFilipinoDoctor, box or insert text | `passage_id`, `product_id`, `section`, `text`, `source_type` (`tfd` added), `source_ref` (the page URL) | `tfd` (M2) |
| `eval/scans.jsonl` | labeled test scan | `ocr_text`, `blocks` (positions), `expected` (product, `not_listed`, `multiple` or `rescan`) | `blocks`, the extra expected values |

**In Supabase (Postgres, Singapore region):**

| Table | Holds | Notes |
| --- | --- | --- |
| `answers` | cached answers: `cache_key`, `tier` (`web`, `general`), parsed names and strengths, `description`, `source_urls`, `status` (`active`, `pending_review`, `rejected`), `created_at`, `expires_at` | Reviewed and ingredient answers aren't stored here: since M2 they're served from `kb/` in memory. Web rows expire after 30 days. |
| `rate_limits` | counters per device ID, per address and global spend, per time window | Old windows deleted by a scheduled job |
| `scan_log` | one row per scan: request ID, hashed device ID, parsed fields, tier, status, latency, cost; **no OCR text after 30 days** | Replaces the JSONL run log on the server |
| `audit_log` | every admin write: who, what, before, after, when | Append-only; no update or delete grants |
| `schema_version` | applied migration numbers | Written by the migration tool |

**Rules for the schema:**

- Migrations are numbered SQL files in `migrations/`, applied in CI against a throwaway database and on deploy against production. Nobody edits tables in the Supabase dashboard.
- Row-level security is turned on for every table with **no** policies. The backend connects with its own Postgres role, so the public `anon` key opens nothing, even if it leaks.
- The free plan has no backups. Until the paid plan, a nightly export of `answers` and `audit_log` goes to private storage, and the restore is tested once in M6.

## 5. API contract v2

Version 2 adds fields and never removes v1 fields until the app has switched, so older app builds keep working. The server keeps building `spoken_text`, so every phone speaks the same warnings.

**Request** `POST /v2/identify`, headers `X-API-Key`, `X-Device-Id` (random UUID made at install), `X-Request-Id` (optional; the server makes one if absent):

```json
{
  "text": "BIOGESIC\nParacetamol 500 mg ...",
  "blocks": [{"text": "BIOGESIC", "confidence": 0.93, "box": [0.12, 0.30, 0.55, 0.38]}],
  "ocr_confidence": 0.88,
  "attempt": 1,
  "capture_hints": {"blurry": false, "dark": false, "text_small": false},
  "platform": "android",
  "app_version": "1.2.0"
}
```

`box` is the block's position as fractions of the image (left, top, right, bottom). `attempt` counts rescans of the same item, starting at 1.

**Response 200:**

```json
{
  "status": "ok | ok_caveat | rescan | multiple | gave_up",
  "rescan_reason": "no_text | too_much_text | no_name | name_not_in_text | strength_unread | ingredients_incomplete | null",
  "hint": "move_closer | more_light | turn_box | null",
  "tier": "reviewed | ingredient | web | general | null",
  "product": {"brand": "Biogesic", "generics": ["Paracetamol"], "strengths": ["500 mg"], "prescription": false},
  "description": "Used to relieve mild pain and reduce fever.",
  "sources": ["https://thefilipinodoctor.com/drug/biogesic"],
  "spoken_text": "Biogesic, paracetamol 500 milligrams. ...",
  "request_id": "7f3c...",
  "cache": "hit | miss | null"
}
```

**Other responses:**

| Code | When | Body |
| --- | --- | --- |
| 401 | missing or wrong `X-API-Key` | `{"detail": ...}` |
| 413 | body over 64 KB (added in M1) | `{"detail": ...}` |
| 422 | invalid body, text over 5,000 characters, more than 500 blocks | FastAPI's validation error |
| 429 | rate limit hit | `spoken_text` "You're scanning very quickly..." and a `Retry-After` header |
| 503 | LLM or search provider down, or server keys unset | `spoken_text` asking to retry |

**Spoken text order** (fixed in one module, `speech.py`, and covered by snapshot tests): name and strength, then "This is a prescription medicine" when it applies, then the tier line ("Our team has reviewed this information." for `reviewed`, the warning for `ingredient`, `web` and `general`), then the description, then any reading caveat, then the pharmacist advice. Each template is listened to once through the phone's text-to-speech before release.

**Tier wording** (web warning decided 2026-10-19 and changed to "reviewed" on 2026-10-20; ingredient wording decided 2026-10-20), spoken before the description:

- Web: "This medicine isn't in our reviewed list. The following information comes from the web and hasn't been reviewed by our team."
- Ingredient: "Our team hasn't reviewed this product yet. Based on its ingredients, …"
- General (fallback until the web tier exists): "Warning: this medicine isn't in our reviewed list, so this is general information only and may be inaccurate."

Added in M1 beyond this section: tier `general`, rescan reason `too_much_text`, and 413 for bodies over 64 KB. The full contract is in `backend/README.md`; the mobile team still has to review it.

The v1 route stays until the app reports v2 in every active build, then it's removed in one PR with a `CHANGES.md` entry.

## 6. Milestones

Eight milestones run mostly in order; the team's dataset work runs alongside from M0 and gates M2. Safety-critical work (tests, matching, scan rules, evaluation) comes before scale work (Supabase, web search), because the second adds coverage, not correctness. Sizes assume one part-time backend developer: S = 1 to 2 days, M = 3 to 5, L = 1 to 2 weeks.

### M0. Foundations (M)

Goal: a codebase that can be changed safely.

- [x] Add `pytest`, `ruff` and a `requirements-dev.txt`; mock `llm_client.chat` in every test.
- [x] Move folder creation out of `config.py` import into an explicit `ensure_dirs()` called at startup.
- [x] Fix the two known defects: don't cache failed descriptions (`description_agent` returns `ok`, `failed` or `not_recognized`; only `ok` is cached), and normalize units in `normalize_key` ("500 mg", "500mg", "500 MG" give one key).
- [x] Delete `obsidian_cache.py`, the `CACHE_VAULT_*` settings and `PH_FDA_REFERENCE_DOMAINS`; stop `main.py` saving captures unless `--save-captures` is passed.
- [x] Structured JSON logs to stdout with a request ID on every line.
- [ ] GitHub Actions: lint, tests and `kb_build.py --check` on every PR; branch protection on `main` (PR + green CI required). **DevOps task:** the workflow is ready in `backend/ops/backend-ci.yml` (ruff, pytest, `kb_build --check`, pip-audit, gitleaks).

Done when: CI runs on every PR, the defect tests fail on the old code and pass on the new, and `main` is protected.

**Status:** backend work merged with PR #23 (2026-10-20); the defect tests were confirmed to fail on the old code. CI and branch protection are with DevOps.

### M1. Parse v2 and scan rules (L)

Goal: read every product in view, with all its ingredients, and decide rescan or answer from checks the code can make.

- [x] Parse returns a list of products, each with `generics[]` and `strengths[]` paired by position. Validate the reply's types and cap field lengths; any invalid reply = `rescan`.
- [x] Put OCR text inside clear delimiters and tell the model to ignore instructions in it (prompt-injection guard, see section 7).
- [x] Scan rules in `scan_rules.py`, one pure function per rule: hard rescan, partial match, caveat, normal, give up after attempt 3, hints from `capture_hints`, `multiple` when two brands (or two strengths of one brand) appear, using `blocks` positions when present.
- [x] `speech.py` builds every spoken template, including the prescription line.
- [x] Contract v2 route next to v1; `README.md` updated. **Still open:** agreement from the mobile team (it was merged before their review).
- [x] Raw-text cache in front of the parse (approved 2026-10-19): key = hash of the normalized OCR text plus the model name; stores only the parse result, never failed parses; scan rules and dataset matching still run on every scan.
- [x] Combination safety: the answer cache key is the brand plus every ingredient and strength, sorted (replacing today's single-generic key); a combination brand read with fewer ingredients than the dataset lists is a rescan; the ingredient tier is used only when every listed ingredient has a checked description; tests cover every pair in the list that shares an ingredient (for example Biogesic and Alaxan FR, Biogesic and Neozep Forte, Advil and Alaxan FR).

Done when: each rule has unit tests with real-looking OCR text (NEOZEP returns three ingredients or a rescan; two brands return `multiple`), and the mobile team has confirmed the contract.

**Status:** merged (PR #23, 2026-10-20). Tested by hand on local Ollama (`llama3.2:3b`, `qwen3.5:4b`), not yet on Groq. Waiting for the mobile team's review of the contract.

### M2. Dataset and matching (L; data entry by the team in parallel)

Goal: the 29 products and their ingredients answer from team-checked text only.

- [ ] Team: finish the sheet (real names in `entered_by`/`checked_by`, TheFilipinoDoctor page URLs, portal reg numbers), load into `kb/` through a PR reviewed by a second person.
- [x] `kb_build.py`: accept `source_type: tfd` (TheFilipinoDoctor; replaces PR #1's `mims`), the new `spoken_description` columns, and reject empty checkers or a checker equal to the entrant.
- [x] One-off script `kb_draft_descriptions.py`: an LLM drafts each `spoken_description` from the TheFilipinoDoctor text into a review sheet (`kb/staging/`); nothing is written to `kb/` automatically.
- [x] `retrieval.py`: exact match on brand plus the full ingredient set and strengths; fuzzy matching only for brand spelling, and only when ingredients and strengths also agree. A near miss is never a match.
- [x] Ingredient tier: when every parsed ingredient is in `ingredients.csv`, compose the description from those rows.
- [x] `eval_kb.py`: runs `kb/eval/scans.jsonl` and reports correct matches, wrong matches, correct `not_listed`, `multiple` and `rescan`.

Done when: all 29 validate; the evaluation on at least 100 labeled scans (including real phone scans) shows **zero wrong reviewed answers**; results are recorded in `CHANGES.md`.

**Status:** the backend items are on branch `backend/m2-dataset-matching`. A first draft of the dataset came from the team's TheFilipinoDoctor scraper: 23 products from 14 of the 29 brands, in `kb/staging/` (not committed). The FDA lookups, the checks and the labeled scans are still to do.

### M3. Supabase (M)

M3 and M4 ship together on one branch and one PR, `backend/m3-m4-supabase-ratelimit` (decided 2026-11-01).

Goal: durable cache and logs that survive redeploys, without making scans depend on the database.

- [x] A `Store` interface (get, set, delete, list) with a SQLite version for tests and a Postgres version for Railway, chosen by `CACHE_BACKEND` (`app/store/`).
- [x] Numbered SQL migrations for the tables in section 4; RLS on; a dedicated backend role. M3 adds `parses`, `answers` and `scan_log`; `rate_limits` comes with M4 and `audit_log` with M7. RLS policies exist only for the backend role, so the public keys still open nothing (no BYPASSRLS needed).
- [x] Fail open: connect timeout 2 s, query timeout 1 s; after a failure, skip the database for 60 s (circuit breaker) and log it.
- [x] Web answers expire after 30 days. (Reviewed and ingredient answers aren't cached: since M2 they're served from `kb/` in memory.)
- [x] Nightly export job and a written restore procedure (`scripts/store_admin.py`, `migrations/README.md`; scheduling it on Railway is DevOps's).

Done when: the same test suite passes on SQLite and on a throwaway Postgres in CI, and killing the database mid-test still returns answers.

### M4. Rate limiting and abuse protection (M; ships with M3)

Goal: one device or script can't run up costs or crowd out other users.

- [x] Limits per device ID (150 a day; **15 a minute, not 6**, because 6 would block the "rescan every 5 seconds" in the done-when below), per address (30 a minute), a global daily budget of about 180 Groq calls, named API keys with optional per-key caps; counters in Supabase with an in-memory fallback when it's down. The web caps come with the web tier (M6).
- [x] Read the client address only from Railway's proxy header, set by the proxy itself (`CLIENT_IP_HEADER=x-real-ip`).
- [x] 429 with spoken text and `Retry-After`.
- [x] The rest of section 7's items marked M4: `MEDSIGHT_API_KEYS`, an 80% budget warning in the logs, a cap on concurrent scans.

Done when: a load test of 100 requests a second from one device gets 429s after the limit with no LLM calls made, and normal use (a rescan every 5 seconds) is never blocked.

### M5. Railway deployment (M)

Goal: staging and production services the app can use.

- [ ] (DevOps) Two Railway environments, `staging` and `production`, each with its own keys and its own Supabase project.
- [ ] Pinned Python version, `kb_build.py` in the build step (a bad dataset fails the deploy), health check on `/health`.
- [ ] (DevOps) Deploy to staging on merge to `main`; promote to production by hand after the smoke test passes.
- [ ] `scripts/smoke_test.py`: health, a known scan, wrong key, empty text, rate limit.

Done when: the app's development build completes scans against staging, and a production deploy plus rollback has been rehearsed once.

### M6. Web search tier (L)

Goal: unlisted medicines get a sourced, warned description from allow-listed sites only.

- [ ] Search restricted to an allowlist kept in the repo (TheFilipinoDoctor; since 2026-11-01 also `unilab.com.ph` and its subdomains, `labeling.pfizer.com` and `hellodoctor.com.ph`; plus any sites the team adds); any result from outside it is dropped in code, not only by the provider's filter.
- [ ] Fetched page text is treated as untrusted input: delimited, length-capped, and the model is asked only to summarize uses.
- [ ] Code gates before anything is spoken: the source mentions the ingredient or brand, no dose numbers or units in the output, length limit, every combination ingredient covered. A failed gate falls back to today's general description.
- [ ] Spoken after the web warning, naming the source site. Cached and shared with every user as `pending_review` with a 30-day expiry (decided 2026-10-19); the team approves or rejects each one from the admin queue, and a rejected answer is searched again on the next scan.
- [ ] Claude Sonnet 5.5 with at most 2 searches and a short reply per call, a timeout of 8 s, about 3 lookups per device a day and about 20 overall, a cached "not found" result, an alert at 80%, and a monthly spend limit in the Anthropic console.

Done when: on 20 unlisted medicines reviewed by two people, zero answers contain a claim the source doesn't support, and cost per scan is measured.

### M7. Admin tools and pilot readiness (M)

Goal: the team can fix a bad answer in minutes, and the system has passed its pilot gate.

- [ ] Admin routes (list, read, update, delete answers; read audit log) behind per-person admin keys, never the app key; every write goes to `audit_log`. Admins can never set `tier: reviewed`.
- [ ] Failure drills: Groq down, search down, Supabase down, a key rotated.
- [ ] Accessibility pass with TalkBack and VoiceOver on every spoken template.
- [ ] Runbook written (section 10).

Done when: the pilot gate in section 11 is met.

### M8. Voice chat over scan history and "my medicines" (L; optional, after the pilot gate)

Goal: the user asks a spoken question about medicines they have scanned or saved ("What did I scan this morning?", "Which of my medicines is for fever?") and hears a short answer grounded only in their own list and the reviewed dataset. The coach suggested this on 2026-11-01. It starts only after M7's pilot gate is met, and nothing in M0 to M7 depends on it.

- [ ] `POST /v2/chat`: speech-to-text runs on the phone, which sends the question text plus the relevant part of the user's scan history and "my medicines" (product IDs and names, never raw OCR text). The server stores neither the question nor the history; it logs only the request ID, latency and cost.
- [ ] Retrieval: map the user's items to `kb/` products by `product_id` first. Vector search (Supabase `pgvector`, built from reviewed `passages.csv` by `kb_build.py`) is used only to pick which reviewed passage answers the question, never to identify a medicine. No Pinecone: the vectors live in the M3 Supabase project, so there's one service fewer to run and secure.
- [ ] Answers come only from reviewed passages and the user's own list. When the answer isn't there, the reply says so ("I don't have reviewed information on that"). Web-tier answers are never used in chat.
- [ ] The same code gates as M6 (no dose numbers or units, length limit), plus a fixed refusal for dosing, interaction and "should I take it" questions that points the user to a pharmacist or doctor.
- [ ] The question and history are delimited as untrusted input (section 7, #15).
- [ ] Chat has its own per-device daily limit and its own share of the global LLM budget, so it can't use up the budget scanning needs.
- [ ] `speech.py` builds every chat template; the contract is added to `README.md` and agreed with the mobile team.

Done when: on 50 labeled chat questions (history lookups, uses, dosing and interaction questions, medicines not in the dataset), 0 answers make a claim that isn't in a reviewed passage or the user's list, every dosing or interaction question gets the refusal, and the chat templates pass the TalkBack and VoiceOver review.

## 7. Security

The two threats that matter most are a crafted input making the app speak false medicine information, and someone using the app's built-in key to run up LLM and search costs. Everything below is resolved in the milestone shown, and each fix has a test or a CI check.

| # | Threat | What could happen | How it's resolved | When |
| --- | --- | --- | --- | --- |
| 1 | The app's API key is extracted from the installed app | Anyone can call `/identify` and spend the LLM budget | Treat the key as an app identifier, not a password. Rate limits and spend caps (#2) are the real control. Accept a list of keys (`MEDSIGHT_API_KEYS`) so each app release can ship a new key and old ones are retired. Phone attestation (Play Integrity, App Attest) later. | M4 |
| 2 | Scripted scanning, rotating device IDs | Cost overrun; real users blocked | Limits per device, per address and a global daily spend cap; 429 with `Retry-After`; alert at 80% of the daily cap | M4 |
| 3 | Prompt injection in OCR text (a printed "ignore your instructions" label) | The parse returns a made-up medicine | OCR text inside delimiters; strict JSON schema and length caps; the parsed name must appear in the OCR text; reviewed answers come only from `kb/`, never from model output | M1 (done) |
| 4 | Prompt injection or false claims on fetched web pages | Wrong information cached and spoken to everyone | Domain allowlist enforced in code; fetched text delimited and capped; code gates (no dose numbers, source must mention the ingredient); "not reviewed" warning; 30-day expiry; admin delete | M6 |
| 5 | Server-side request forgery through web fetches | The server is tricked into calling internal addresses | Fetch only `https` URLs on allow-listed domains; no redirects to other domains; 2 MB and 8 s limits | M6 |
| 6 | Supabase credentials leak | Cache or logs read or altered | Connection string only in Railway variables; TLS required; a backend role with access to its own tables only; RLS on with no policies so the public `anon` key opens nothing | M3 |
| 7 | Admin routes abused | Answers changed or deleted | Per-person admin keys, compared in constant time; the app key never works there; every write in `audit_log`; admin routes off in production unless `ADMIN_ENABLED=true`; `reviewed` can't be set by admins | M7 |
| 8 | Secrets committed or logged | Keys exposed in git history or logs | `gitleaks` in CI and a one-time scan of history; never log headers or request bodies with keys; provider error text truncated (already done in `llm_client.py`) | M0 (gitleaks workflow ready for DevOps) |
| 9 | Personal data in OCR text (a pharmacy label with a patient's name on a prescription box) | Privacy breach under the Data Privacy Act of 2012 | No images ever leave the phone; OCR text deleted from `scan_log` after 30 days; device IDs stored hashed; privacy notice in the app; confirm the requirements with the instructor | M3, M7 |
| 10 | Oversized or slow requests | The service hangs or runs out of memory | Body size limit of 64 KB; 5,000-character and 500-block caps; LLM timeout cut from 60 s to 15 s; a cap on concurrent scans | M1 (done: body limit, caps, timeout), M4 (concurrency) |
| 11 | Vulnerable dependencies | Known exploits in libraries | Pinned versions; `pip-audit` in CI; Dependabot updates reviewed weekly | M0 (pinned and pip-audit done; Dependabot: DevOps) |
| 12 | Information leaks from the API | Internal errors, schema or prompts exposed | Generic error bodies with a request ID (details only in logs); `/docs` and `/openapi.json` off in production | M5 |
| 13 | Unreviewed edits to `kb/` | A wrong "reviewed" fact | `CODEOWNERS` requires a second reviewer for `kb/`; `kb_build.py --check` in CI; checker must differ from entrant | M0, M2 (checker rule done; CODEOWNERS: DevOps) |
| 14 | LLM or search provider keys leak | Charges on the team's accounts | Separate keys per environment; spend limits set at each provider; rotation steps in the runbook | M5 |
| 15 | Prompt injection or personal data in voice chat (a question or history item that says "ignore your instructions"; a patient name in a saved item) | A made-up or dosing answer is spoken; personal data leaks into logs | Question and history delimited and length-capped; answers only from reviewed passages and the user's list; dose and refusal gates in code; the server stores neither questions nor history and never logs them | M8 |

**Transport:** HTTPS only (Railway terminates TLS), with HSTS. The app pins nothing for now; certificate pinning is reconsidered only if the app moves beyond the pilot.

## 8. Testing and evaluation

Tests prove the code does what we wrote; evaluation proves the system gives the right answer on real labels. The release gate is evaluation: zero wrong reviewed answers on the labeled scan set.

| Layer | What it covers | Runs | Required |
| --- | --- | --- | --- |
| Unit | `normalize_key`, each scan rule, `retrieval.py`, `speech.py` templates, `kb_build.py` checks, rate-limit windows | every PR | 90% line coverage on these modules |
| API | `TestClient`: 401, 422, 429, 503, the v1 and v2 shapes, request ID on every response | every PR | all pass |
| Store | the same suite on SQLite and on a throwaway Postgres (GitHub Actions service container); a killed database still answers | every PR | all pass |
| Spoken text | snapshot tests of every template; a change to a snapshot needs a reviewer's approval | every PR | no unreviewed changes |
| Parse evaluation | `eval_parse.py` runs fixed OCR texts through the real model 3 times each; per-field accuracy | before any prompt or model change | no drop from the recorded baseline |
| Dataset evaluation | `eval_kb.py` on at least 100 labeled scans (phone scans, blister foil, poor light, unlisted medicines, two products in view) | before every release | 0 wrong reviewed answers; at least 95% of unlisted medicines correctly not matched |
| Web tier review | 20 unlisted medicines, two reviewers compare each answer with its source | before M6 is turned on, then monthly | 0 unsupported claims |
| Chat evaluation (M8) | `eval_chat.py` on 50 labeled questions: history lookups, uses, dosing and interaction questions, medicines not in the dataset | before M8 is turned on, then before every release that changes chat | 0 unsupported claims; every dosing or interaction question refused |
| Load | 20 concurrent scans for 5 minutes against staging | before the pilot | p95 latency under 4 s on a cache miss, under 500 ms on a hit; no errors |

**Rules:**

- No test calls a real LLM, search provider or database except the evaluation scripts and the Postgres container.
- Tests use temp directories for data and logs (`MEDSIGHT_DATA_DIR`, `MEDSIGHT_LOGS_DIR`), never the repo.
- Test data in `kb/` tests is fake and lives in `tests/fixtures/`, never in `kb/`.
- Every bug fix starts with a test that fails on the old code.
- Evaluation results are written to `CHANGES.md` with the date, commit, model and dataset version.

## 9. CI/CD, environments and releases

Every change reaches production the same way: a reviewed PR, green CI, automatic deploy to staging, a smoke test, then a manual promotion to production.

**Environments:**

| Environment | Where | Data | Keys |
| --- | --- | --- | --- |
| Local | developer laptop, `uvicorn --reload` | SQLite in a temp folder; Ollama locally, so the shared free Groq quota is kept for staging and testers | `.env`, never committed |
| CI | GitHub Actions | throwaway Postgres container, fake `kb/` fixtures | none; no real provider calls |
| Staging | Railway `staging` | its own Supabase project | its own keys, lower spend caps |
| Production | Railway `production` | its own Supabase project | its own keys; only two people can view them |

**CI on every PR** (all must pass to merge): `ruff`, `pytest`, `kb_build.py --check`, `pip-audit`, `gitleaks`, and migrations applied to the throwaway database.

**Branch and review rules:**

- `main` is protected: no direct pushes, one approving review, green CI.
- `kb/` changes need a second reviewer from the team (`CODEOWNERS`), and the PR description lists the TheFilipinoDoctor pages and FDA portal records checked.
- Contract changes need a comment from the mobile team on the PR.

**Release steps:**

1. Merge to `main`: Railway builds, runs `kb_build.py`, applies migrations and deploys to staging.
2. `scripts/smoke_test.py --env staging` passes.
3. Before a release that changes parsing, matching or speech: `eval_kb.py` passes the gate in section 8.
4. Promote the same build to production in Railway; run the smoke test against production.
5. Add a dated `CHANGES.md` entry.

**Rollback:** redeploy the previous build in Railway (one click). Migrations only add columns and tables; a column is removed one release after the code stops using it, so a rollback never breaks on the schema.

## 10. Operations

The team should learn about a problem from an alert, not from a user. Railway keeps what the process prints, so every log line is one JSON object on stdout.

**Every scan logs one line with:** request ID, hashed device ID, app version, status, rescan reason, tier, cache hit or miss, matched `product_id`, latency per stage (parse, match, search, store), LLM and search cost, and error class if any. OCR text goes only to `scan_log` in Supabase, which deletes it after 30 days. Keys, headers and full request bodies are never logged.

**Alerts** (Railway notifications or a free uptime monitor to the team's chat):

| Alert | Trigger | First action |
| --- | --- | --- |
| Service down | `/health` fails 3 times in a row | Check the Railway deploy log; roll back if the last deploy caused it |
| LLM failing | more than 10% of scans return 503 in 5 minutes | Check the provider's status page; switch `GROQ_MODEL` if a model was retired |
| Database skipped | the circuit breaker opens more than 3 times in an hour | Check Supabase status; scans still work, so it isn't urgent |
| Spend | 80% of the daily LLM or search cap | Look for one device or address with most of the calls |
| Rate limiting spike | over 5% of requests get 429 in an hour | Check whether it's an attack or limits that are too low |
| Wrong answer reported | a user or tester report | Delete the cached answer through admin; add the scan to `kb/eval/`; fix the root cause |

**Runbook** (`docs/runbook.md`, written in M7) covers: deploying and rolling back; rotating each key (app, admin, Groq, search, Supabase); restoring Supabase from the nightly export; removing a bad cached answer; adding a medicine to `kb/`; and what to do during a provider outage.

**Weekly review during the pilot:** rescan rate, `gave_up` rate, share of answers by tier, the most-scanned unlisted medicines (candidates for `kb/`), cache hit rate, p95 latency and cost per scan.

## 11. Risks, open decisions and the pilot gate

**Pilot gate** (all must hold before real users rely on the service):

- [ ] 0 wrong reviewed answers on at least 100 labeled scans, including real phone scans and blister foil.
- [ ] Every security item in section 7 for M0 to M5 is done and tested.
- [ ] Every failure drill (LLM, search, database down; key rotated) ends in a clear spoken message.
- [ ] Every spoken template has been heard through TalkBack and VoiceOver and reviewed with the coach.
- [ ] The runbook exists and one teammate other than the author has followed it.
- [ ] Privacy notice approved; logging and retention match section 10.

**Risks:**

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Dataset entry and checking slips | M2 can't finish; nothing can be evaluated | Start now; split the 29 across teammates; the pilot can start with fewer products if each one is fully checked |
| OCR turns one product into another | A confident wrong reviewed answer | Match on the full ingredient set and strengths, never on a near-miss name; evaluation gate |
| An LLM-drafted description adds a claim the source doesn't make | A false "reviewed" statement | The draft script flags new words; the checker compares each drafted sentence with the source text before it enters `kb/` |
| TheFilipinoDoctor (a third-party site) is wrong or out of date | A wrong fact spoken as reviewed | Two-person check against the page; OTC/Rx, registration and expiry from the FDA portal by hand; spot-check against the box |
| Groq retires the model | Parsing fails | Model name in configuration; the parse evaluation re-run on any switch |
| Web search returns weak or foreign-market pages | Misleading "not reviewed" answers | Allowlist, code gates, review set, expiry |
| Costs grow faster than expected | Budget runs out | Caps at each provider and in code; weekly cost review |

**Open decisions** (each needs an owner and a date before its milestone starts):

| # | Decision | Needed by | My recommendation |
| --- | --- | --- | --- |
| 1 | Allowlist sites besides TheFilipinoDoctor (on the list since 2026-10-26; MIMS dropped) | M6 | Added 2026-11-01: `unilab.com.ph` (and subdomains), `labeling.pfizer.com`, `hellodoctor.com.ph`. Still open: MedlinePlus, DailyMed and the PH FDA public site (never the verification portal) |
| 2 | ~~Wording for the ingredient tier~~ **Decided 2026-10-20** | M1 | "Our team hasn't reviewed this product yet. Based on its ingredients, …" |
| 3 | OCR-confidence cut-off for the caveat | M2 | Set from the labeled scans so no wrong reviewed answer sits above it (0.55 for now) |
| 4 | Supabase plan (free or paid) | M3 | Paid before the pilot for backups and no pausing |
| 5 | Whether to build voice chat at all, and whether the app's Supabase history (PR #30) or only on-phone history feeds it | M8 | Decide after the pilot from user feedback; send only the items the question needs, as product IDs and names |
