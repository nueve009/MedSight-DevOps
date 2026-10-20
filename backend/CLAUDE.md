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

`kb/` holds the hand-curated, two-person-checked ("reviewed") dataset of the
supported medicines. `kb/README.md` explains how it's collected. **Never add or edit
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
2. **Raw-text parse cache** (`CacheStore.get_parse`), keyed by
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
   `OCR_CAVEAT_THRESHOLD` (`None` = skipped, not 0). `partial_match_reason()`
   is ready for M2's dataset match.
5. **Answer cache** (`CacheStore.get_answer`), keyed by `utils.answer_key`:
   brand + every (ingredient, strength) pair, sorted, with strengths
   normalized (`utils.normalize_strength`: "500 mg" = "500mg" = "500 MG").
   A combination never shares a key with one of its ingredients.
6. **Describe** (`description_agent.get_description`, the `general` tier),
   only on a cache miss. `status` is `ok`, `not_recognized` or `failed`; a
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

Cache errors never fail a scan (`pipeline._cache_call` logs and carries on).

**LLM provider** (`app/llm_client.py`):
- **Groq:** calls the OpenAI-compatible `/chat/completions` endpoint with
  `response_format: json_object`. JSON mode doesn't enforce field names, so
  the schema is also appended to the system prompt.
- **Ollama:** passes the schema in `format`, which enforces it, and
  `think: false`, without which thinking models (qwen3.5) return empty content.
- **Errors:** both raise `LLMUnavailableError` for unreachable services, rate
  limits, timeouts (`LLM_TIMEOUT_S`, Groq) and bad keys. `app/api.py` maps it
  to a 503 with a spoken retry message and the request ID.

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
