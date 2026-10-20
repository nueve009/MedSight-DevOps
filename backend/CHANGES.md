# MedSight — Change Log

A running record of project changes, decisions and open items, so later work
can pick up where the last session left off. Newest entry first.

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
