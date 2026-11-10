# MedSight — Medicine Identification Backend

An assistive tool that helps visually impaired users identify Philippine OTC
and branded medicines, including blister pack foil, from the text printed on
the packaging.

- **Mobile app** (React Native + Expo, in the root of this repo): runs OCR on
  the phone (Apple Vision on iOS, Google ML Kit on Android), sends the text to
  this backend, and speaks the reply with `expo-speech`. No photos leave the
  phone.
- **Backend** (this folder, FastAPI): reads every product in the text with an
  LLM (brand, every active ingredient and its strength), applies the scan
  rules (rescan, several medicines, give up), matches the label against the
  team-reviewed dataset in `kb/`, falls back to a warned general description,
  and returns the exact text to speak.

```
phone OCR → POST /v2/identify → input checks → parse (LLM, raw-text cache)
          → scan rules → match kb/ (reviewed | partial → rescan | ingredient)
          → otherwise answer cache → general description (LLM, on a miss)
          → spoken_text → phone TTS
```

## Folder structure

This backend lives in the `backend/` folder of the MedSight repo, next to the
mobile app. Run every command below from inside `backend/`.

```
backend/
├── app/        the server: what the cloud host runs (uvicorn app.api:app)
├── scripts/    laptop-only tools: webcam prototype, OCR benchmark, dataset builders
├── kb/         the reviewed medicine dataset (CSV files)
├── migrations/ numbered SQL files for the Supabase cache (see migrations/README.md)
├── tests/      pytest suite; the LLM is always faked
├── ops/        CI workflow and other files the DevOps engineer moves into place
├── requirements.txt         server packages (pinned)
├── requirements-dev.txt     server + pytest, ruff, pip-audit
├── requirements-local.txt   server + laptop-tool packages (OCR, webcam, TTS)
├── pyproject.toml           pytest and ruff settings
└── .env.example             copy to .env and fill in
```

Scripts run as modules, so they can import the server code:
`python -m scripts.kb_build --check`.

## Setup

```bash
python -m venv .venv                   # Python 3.12
.venv\Scripts\activate                 # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt    # server + test tools (requirements.txt for server only)
cp .env.example .env                   # then fill in GROQ_API_KEY and MEDSIGHT_API_KEY
```

### Tests and lint

```bash
pytest           # never calls a real LLM; data and logs go to a temp folder
                 # set MEDSIGHT_TEST_DATABASE_URL to also run the store tests on a throwaway Postgres
ruff check .
pip-audit -r requirements.txt
```

For the local webcam prototype and OCR benchmark, install
`requirements-local.txt` instead. It adds OpenCV, PaddleOCR, EasyOCR and
pyttsx3.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `groq` | `groq`, or `ollama` for offline work |
| `GROQ_API_KEY` | — | Groq API key |
| `GROQ_MODEL` | `qwen/qwen3.8-27b` | Any Groq chat model that supports JSON mode |
| `OLLAMA_HOST` / `OLLAMA_MODEL` | `http://localhost:11434` / `llama3.2:3b` | Used when `LLM_PROVIDER=ollama` |
| `LLM_TIMEOUT_S` | `15` | Groq request timeout; past it the user hears "try again" |
| `MEDSIGHT_API_KEY` | — | Shared secret the app sends as `X-API-Key`. If no key is set at all, both identify routes refuse every request. |
| `MEDSIGHT_API_KEYS` | — | More keys as `name=key,name=key` (one per app release or tester group); remove one to cut it off. `MEDSIGHT_API_KEY` counts as `default`. |
| `RATE_DEVICE_PER_MINUTE` / `RATE_DEVICE_PER_DAY` | `15` / `150` | Scans per phone (`X-Device-Id`); a request without one counts per address |
| `RATE_ADDRESS_PER_MINUTE` | `30` | Scans per client address |
| `LLM_DAILY_BUDGET` | `180` | Model calls (parse + describe) per UTC day for the whole service; a warning is logged at 80% |
| `KEY_DAILY_LLM_CAPS` | — | Optional model-call caps per key name, e.g. `testers-a=60` |
| `CLIENT_IP_HEADER` | — | Header with the real client address. On Railway: `x-real-ip`. Empty = the TCP peer. |
| `MAX_CONCURRENT_SCANS` | `8` | Scans running at once; past it, a quick 503 |
| `MAX_PARSE_CHARS` | `2000` | Longer OCR text is a rescan ("move closer") with no LLM call |
| `OCR_CAVEAT_THRESHOLD` | `0.55` | Below this OCR confidence an answer gets "I'm not fully sure of this reading". Old name `LOW_CONFIDENCE_THRESHOLD` still works. |
| `MAX_SCAN_ATTEMPTS` | `3` | From this attempt on, a rescan becomes `gave_up` |
| `CACHE_BACKEND` | `sqlite` | `postgres` for the shared Supabase cache and scan log (see `migrations/README.md`) |
| `DATABASE_URL` | — | Postgres connection for the backend's own role; required with `CACHE_BACKEND=postgres` |
| `DATABASE_SSLMODE` | `require` | TLS to Supabase; `disable` only for a local database |
| `DB_CONNECT_TIMEOUT_S` / `DB_QUERY_TIMEOUT_MS` / `DB_SKIP_AFTER_FAILURE_S` | `2` / `1000` / `60` | Fail-open limits: past them a scan treats the cache as a miss, then skips the database for a while |
| `MEDSIGHT_DATA_DIR` / `MEDSIGHT_LOGS_DIR` | `data/` / `logs/` | Point at a persistent disk on the cloud host |
| `LOG_LEVEL` | `INFO` | Level for the JSON logs on stdout |
| `WEB_SEARCH_PROVIDER` / `TAVILY_API_KEY` | — | `tavily` plus a key turns the web tier on. Empty = off: unlisted medicines get the general tier. |
| `WEB_SEARCH_TIMEOUT_S` | `8` | Search timeout; past it the scan gets the general tier |
| `WEB_DEVICE_PER_DAY` / `WEB_DAILY_BUDGET` | `3` / `20` | Web lookups per phone and for the whole service per UTC day; past them, the general tier (not a 429) |
| `WEB_MISS_TTL_DAYS` | `7` | A medicine the web tier couldn't answer isn't searched again for this long |
| `ADMIN_ENABLED` | `off` | `on` turns on the `/admin` routes |
| `ADMIN_KEYS` | — | One admin key per person, as `name=key,name=key`; the name goes in the audit log |
| `API_DOCS` | `off` | `on` serves `/docs`, `/redoc` and `/openapi.json`. Leave it off on staging and production. |

## Run the backend

```bash
uvicorn app.api:app --reload                     # local
uvicorn app.api:app --host 0.0.0.0 --port $PORT  # cloud host
```

Interactive API docs are at `/docs` when `API_DOCS=on` (set it in your local
`.env`). They're off by default, so a deployed service doesn't publish its
schema.

## Admin routes (M7)

Off unless `ADMIN_ENABLED=on`: every `/admin` path is a 404 until then. Each
person has their own key in `ADMIN_KEYS` (`name=key,name=key`) and sends it
as `X-Admin-Key`. App keys never work here.

| Route | Does |
|---|---|
| `GET /admin/answers?status=&limit=` | Lists cached answers (any status, expired too) |
| `GET /admin/answers/{cache_key}` | Reads one answer |
| `PATCH /admin/answers/{cache_key}` | `{"status": "active" \| "pending_review" \| "rejected", "description": "..."}`: approve, reject or fix the text. The text is gated like a model's: 300 characters at most, no doses. Anything else in the body, `tier` included, is a 422. |
| `DELETE /admin/answers/{cache_key}` | Removes it; the next scan answers afresh |
| `GET /admin/audit?limit=` | Every admin write, newest first: who, what, before, after |

Every write and its audit row are saved together. A database outage gives a
503 with nothing changed. `docs/runbook.md` has the step-by-step for a bad
answer.

## Smoke test (after each deploy)

```powershell
$env:SMOKE_API_KEY = "<an app key for that environment>"
python -m scripts.smoke_test --url https://<service host>
```

It checks `/health`, a wrong key (401), empty text (a `no_text` rescan), a
known Biogesic scan, that `/docs` and `/openapi.json` are 404, and that
repeated scans end in a 429. It prints PASS or FAIL per check and exits 1 if
any fail. Only the known scan can call the model (at most twice, then it's
cached). The rate-limit check uses up the caller's address limit for the rest
of the minute, so wait a minute between runs.

## API contract v2 (for the mobile team)

> **Status:** built on branch `backend/m1-parse-scan-rules`, **not yet agreed
> with the mobile team.** v1 (below) keeps working unchanged in shape.

`POST /v2/identify`, with headers:
- `X-API-Key`: the app key
- `X-Device-Id`: a random UUID the app creates at install (optional for now;
  M4 rate limits use it). Stored and logged only as a hash.
- `X-Request-Id`: optional; see "Request IDs" below

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

- `text`: all recognized text joined with newlines (max 5000 characters). If
  it's empty, the server joins the `blocks` texts.
- `blocks` (max 500): each OCR text block, with its position `box` as
  fractions of the image (left, top, right, bottom). Optional, but it helps
  the server tell two boxes in view from small print like "also available".
- `ocr_confidence`: send `null` if the platform doesn't report one; the
  caveat check is then skipped, not treated as 0.
- `attempt`: counts rescans of the **same item**, starting at 1. Reset it to 1
  when the user scans something new or after an answer.
- `capture_hints`: what the phone could tell about the picture. It picks the
  rescan hint ("find more light", "move closer").

Response (200):

```json
{
  "status": "ok | ok_caveat | rescan | multiple | gave_up",
  "rescan_reason": "no_text | too_much_text | no_name | name_not_in_text | strength_unread | ingredients_incomplete | null",
  "hint": "move_closer | more_light | turn_box | null",
  "tier": "reviewed | ingredient | web | general | null",
  "product": {"brand": "Biogesic", "generics": ["Paracetamol"], "strengths": ["500 mg"], "prescription": false},
  "description": "A pain reliever and fever reducer.",
  "sources": [],
  "spoken_text": "Biogesic, Paracetamol 500 milligrams. Warning: ...",
  "request_id": "7f3c...",
  "cache": "hit | miss | null"
}
```

- **Always speak `spoken_text` exactly as sent.** It already holds every
  warning, in the right order.
- `status`:
  - `ok`: answered.
  - `ok_caveat`: answered, but the phone's OCR confidence was low; the spoken
    text adds "I'm not fully sure of this reading."
  - `rescan`: ask the user to scan again. `rescan_reason` says why and `hint`
    what to do (`turn_box`: scan the side with the ingredients list). A
    brand in the reviewed list whose strength or ingredients don't fully
    match is always a rescan, e.g. "I found Biogesic but couldn't read the
    strength. Turn the box and scan the side with the ingredients."
  - `multiple`: two different brands, or two strengths of one brand, are in
    view. The user hears "I can see more than one medicine. Please set the
    others aside and scan one at a time."
  - `gave_up`: sent instead of `rescan` from attempt 3 on, saying only what
    was read clearly, e.g. "I can read Biogesic but not the strength. Please
    ask someone to check the label." Reset `attempt` after it.
- `product` and `tier` are `null` unless `status` is `ok` or `ok_caveat`.
  `generics` and `strengths` are paired by position (a combination product
  lists every ingredient).
- `tier`: where the description comes from. The spoken text says it, before
  the description:

  | Tier | Source | Spoken before the description |
  |---|---|---|
  | `reviewed` | the team-reviewed dataset in `kb/`: brand, every ingredient and strength matched | "Our team has reviewed this information." |
  | `ingredient` | not a listed product, but every ingredient has a team-reviewed description | "Our team hasn't reviewed this product yet. Based on its ingredients, ..." |
  | `web` | allow-listed web search (M6; on when `WEB_SEARCH_PROVIDER=tavily`). The description is then spoken as "According to <site>, ...". `sources` holds the page URL. | "This medicine isn't in our reviewed list. The following information comes from the web and hasn't been reviewed by our team." |
  | `general` | the model's general knowledge (fallback) | "Warning: this medicine isn't in our reviewed list, so this is general information only and may be inaccurate." |

  Consider a distinct sound or vibration for anything other than `reviewed`.
  For `reviewed` answers, `product` holds the dataset's spelling (not the
  OCR's), `sources` the source page URLs, and `product.prescription` is true
  for prescription products, which are also spoken with "This is a
  prescription medicine." For a `web` answer, `product.prescription` is true
  only when the source page says so; it is spoken after the web warning as
  "According to <site>, this is a prescription medicine." It is always false
  for `ingredient` and `general`, which means "not stated", not "doesn't need
  a prescription". `cache` is `null` for `reviewed` and `ingredient` answers
  (they come from the dataset, not the cache).

Other responses (both versions):
- **401:** missing or wrong API key.
- **429:** too many requests. The body is `{"status": "rate_limited",
  "reason": ..., "retry_after_s": ..., "spoken_text": ..., "request_id": ...}`,
  with a `Retry-After` header (seconds). Speak `spoken_text`, and don't
  retry before `retry_after_s`. `reason`:
  - `too_fast`: this phone (`X-Device-Id`) or network sent too many scans this minute
  - `daily_limit`: this phone's daily scan limit
  - `service_limit`: the service's model budget for the day (UTC) is used up.
    Scans that need no model call (cached labels) still work.
- **413:** request body over 64 KB.
- **422:** invalid body: text over 5000 characters, more than 500 blocks, a
  `box` without 4 numbers, `attempt` below 1, a confidence outside 0–1.
- **503:** too many scans running at once (with `Retry-After: 2`), or the LLM is busy or down (for example, the Groq rate limit or a
  15-second timeout), or the server's API key isn't configured. When the LLM
  is the cause, the body is `{"status": "service_unavailable", "spoken_text":
  ..., "request_id": ...}`.

## API contract v1

`POST /identify`, with header `X-API-Key`. Kept until every active app build
uses v2. Since M1 it runs the same pipeline as v2, so its answers follow the
same scan rules; only the shape is v1.

```json
{
  "text": "BIOGESIC\nParacetamol 500mg ...",
  "lines": [{"text": "BIOGESIC", "confidence": 0.93}],
  "ocr_confidence": 0.88,
  "frames_merged": 4,
  "platform": "android",
  "app_version": "0.1.0"
}
```

Response (200):

```json
{
  "status": "ok | low_confidence | rescan",
  "brand_name": "Biogesic", "generic_name": "Paracetamol", "dosage": "500 mg",
  "description": "A pain reliever and fever reducer.",
  "grounded": false, "source_url": null,
  "warnings": ["not verified"],
  "spoken_text": "Biogesic, Paracetamol 500 milligrams. Warning: ...",
  "cache": "hit | miss | null"
}
```

- v2 `ok` → `ok`; `ok_caveat` → `low_confidence` with `"OCR"` in `warnings`;
  `rescan`, `multiple` and `gave_up` → `rescan` with every name field `null`
  and the v2 spoken text.
- Combination products join their ingredients and strengths with `" + "`,
  e.g. `"generic_name": "Ibuprofen + Paracetamol"`, `"dosage": "200 mg + 325 mg"`.
- `grounded` is `true` only for the `reviewed` tier; otherwise `warnings`
  includes `"not verified"` (the value is unchanged for older app builds,
  although the spoken text now says "reviewed").

`GET /health` returns `{"status": "ok", "llm_provider": "...", "kb_version": "...", "kb_products": 29}`.
`kb_version` is `null` and `kb_products` 0 when no reviewed dataset is loaded
(no approved rows in Supabase, Supabase unreachable since startup, or no
`data/kb.sqlite` locally): the service still runs, but every answer falls to
the lower tiers.

## Reviewed dataset

The team-reviewed medicines live in Supabase (`kb_*` tables, migration
0005). Each row counts only after a second teammate approves it in the
Table Editor; the deployed backend reads approved rows and re-reads them
every minute. `kb/README.md` has the workflow and columns. Locally,
`python -m scripts.kb_build` builds `data/kb.sqlite` from the CSVs in `kb/`,
or `--pull` builds it from Supabase; either is refused if a row breaks a
rule. Tools for the team:

```bash
python -m scripts.kb_build --check [--kb-dir <folder>]   # validate
python -m scripts.kb_build --push --kb-dir <folder>      # import drafts into Supabase, unapproved
python -m scripts.kb_build --pull [--csv-out kb]         # approved rows -> data/kb.sqlite (and CSVs)
python -m scripts.kb_draft_descriptions                  # LLM drafts of spoken descriptions → kb/staging/
python -m scripts.eval_kb --out report.json              # labeled scans: must give 0 wrong reviewed answers
```

**Request IDs:** every response has an `X-Request-Id` header. The app may
send its own `X-Request-Id` (up to 64 letters, digits, `.`, `_` or `-`) and
gets the same value back; otherwise the server makes one. Quote it when
reporting a problem, because every server log line for that scan carries it.

## Local webcam prototype

`scripts/main.py` runs OCR on the laptop and then the same pipeline as the backend.
It's useful for testing without the phone.

```bash
python -m scripts.benchmark_ocr /path/to/kaggle_dataset_root   # once: picks PaddleOCR vs EasyOCR
python -m scripts.main                                         # SPACE = capture, q = quit
python -m scripts.main --save-captures                         # also keep each frame in captures/
```

The benchmark uses the Kaggle
[Pharmaceutical Drugs and Vitamins Dataset V2](https://www.kaggle.com/datasets/vencerlanz09/pharmaceutical-drugs-and-vitamins-dataset-v2).
It has one subfolder per product, and the folder names are used as labels.
The benchmark writes `config/ocr_engine_choice.json`, which `scripts/main.py` reads.
There's no OCR ground truth in this dataset, so spot-check the results.

## Logs

The server writes one JSON object per line to stdout: one line per request
(method, path, status code, latency) and one per scan (status, rescan
reason, hint, tier, both caches, attempt, product count, stage latencies,
hashed device ID), each with the request ID. These lines never include the
OCR text, keys or headers.

Every scan also writes a full record, including the OCR text and the parse,
to the scan log. With `CACHE_BACKEND=sqlite` (local) that's
`logs/run_log.jsonl`. With `postgres` it's the `scan_log` table in Supabase:
the OCR text is in its own column and `python -m scripts.store_admin purge`
clears it after 30 days. Device IDs are only ever stored hashed.

## Known limitations

- **The reviewed dataset is still empty.** Matching is built, but `kb/` has
  no checked rows yet (see `kb/README.md`), so until the team adds them every
  medicine gets the `general` tier, with its spoken warning.
- The OCR-confidence cut-off and the small-print ratio for `multiple` are
  starting guesses. Set them from the team's labeled scans.
- Not built: smart-glasses integration, shape/color classification.
