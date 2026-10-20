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
uvicorn app.api:app --reload                          # backend; docs at /docs
python -m scripts.main [--save-captures]                  # webcam loop: SPACE = capture + run pipeline, q = quit
python -m scripts.benchmark_ocr <dataset_root>            # dataset path is required
python -m scripts.sort_packaging_images <dataset_root> <output_dir> [--min-text-regions 3]
python -m scripts.kb_build --check                        # validate the verified dataset in kb/
python -m scripts.kb_build                                # validate + build data/kb.sqlite
python -m scripts.kb_make_scans sorted_output/packaging   # draft eval scans from images
```

`kb/` holds the hand-curated, two-person-checked dataset of the 10 supported
medicines. `kb/README.md` explains how it's collected. **Never add or edit
rows in `kb/*.csv` yourself.** Every value must come from the team's box
photos and FDA portal lookups. Use fake data in a temp folder for tests. The
FDA portal must not be scraped or automated.

**Tests** live in `tests/` (pytest). `tests/conftest.py` replaces
`llm_client.chat` with a `FakeLLM` in every test (a test that doesn't set a
reply fails rather than calling a real model) and points the data and log
paths at a temp folder. Fake `kb/` datasets come from
`tests/fixtures/fake_kb.py`, never from the real `kb/`. Every bug fix starts
with a test that fails on the old code. To try the real model by hand, use
`TestClient` with `LLM_PROVIDER=ollama`, or call one stage directly, e.g.
`llm_parse.parse_medicine_text(text)`.

## Architecture

The phone does OCR and TTS. `app/api.py` receives the text and calls
`pipeline.identify(raw_text, ocr_confidence, cache, meta)`, which runs
everything after OCR and returns the response dict, including `spoken_text`.
`scripts/main.py` is the local test tool: it runs OCR on a webcam capture
(`scripts/ocr_engines.py`) and calls the same `identify()`.

1. **OCR confidence.** The client computes it. When it's `None`, the check is
   skipped and logged as `ocr_confidence_skipped`; it is not treated as 0.
2. **Parse** (`llm_parse`). The LLM call goes through `llm_client.chat()`.
   After parsing, an anti-hallucination check caps confidence at 0.3 if
   neither extracted name fuzzily appears in the OCR text. Empty fields come
   back as `None`, not `""`. If the reply is invalid JSON, every field is
   empty, and `identify()` returns `rescan`.
3. **Cache** (`cache_store.CacheStore`, SQLite at `MEDSIGHT_DATA_DIR`). The
   key is `utils.normalize_key(generic, brand, dosage)`: a 16-character
   sha256 of the normalized *parsed* fields, not the raw OCR text.
   `utils.normalize_strength` makes "500 mg", "500mg" and "500 MG" one key.
   Each call opens and closes its own connection because FastAPI runs
   requests on several threads. The cache is created at app startup
   (`app.state.cache`), not on import.
4. **Describe** (`description_agent`). This only runs on a cache miss. It
   returns a `status`: `ok`, `not_recognized` or `failed`. Only `ok` results
   are cached, so a temporary LLM error isn't repeated on later scans. Until
   retrieval from `kb/` is built, it generates a general description from the
   model's memory, returns `grounded=False`, and caps confidence at
   `MAX_CONFIDENCE = 0.6`. The team decided (2026-10-08) that medicines
   outside the 10 verified ones keep getting this unverified description;
   they are not refused.
   - For ungrounded results, `pipeline.identify()` speaks `UNVERIFIED_WARNING`
     **before** the description and ends with `UNVERIFIED_ADVICE`.
   - It adds `"not verified"` to `warnings`.
   - It does *not* count the capped description confidence as a
     low-confidence stage, so `status` only reflects how well the label was
     read (OCR and parse).
5. **Status and speech.** `confidence_handler.check_confidence()` is "log,
   don't block". It logs a warning line, appends to `warnings_log.jsonl`,
   and returns True when the stage is low. The only hard stops (`status: "rescan"`) are no text and no parsed
   name. The server builds `spoken_text` so the caveats are worded the same
   on every platform.

**LLM provider** (`app/llm_client.py`):
- **Groq:** calls the OpenAI-compatible `/chat/completions` endpoint with
  `response_format: json_object`. JSON mode doesn't enforce field names, so
  the schema is also appended to the system prompt.
- **Ollama:** passes the schema in `format`, which enforces it.
- **Errors:** both raise `LLMUnavailableError` for unreachable services, rate
  limits and bad keys. `app/api.py` maps it to a 503 with a spoken retry message.

**Auth** (`api.require_api_key`): checks the `X-API-Key` header against
`MEDSIGHT_API_KEY` using a constant-time comparison. If the server key isn't
set, it fails closed with a 503.

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
