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
  rules (rescan, several medicines, give up), serves repeat scans from a
  cache, writes a short description, and returns the exact text to speak.

```
phone OCR → POST /v2/identify → input checks → parse (LLM, raw-text cache)
          → scan rules → answer cache → description (LLM, on a miss)
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
| `MEDSIGHT_API_KEY` | — | Shared secret the app sends as `X-API-Key`. If unset, both identify routes refuse every request. |
| `MAX_PARSE_CHARS` | `2000` | Longer OCR text is a rescan ("move closer") with no LLM call |
| `OCR_CAVEAT_THRESHOLD` | `0.55` | Below this OCR confidence an answer gets "I'm not fully sure of this reading". Old name `LOW_CONFIDENCE_THRESHOLD` still works. |
| `MAX_SCAN_ATTEMPTS` | `3` | From this attempt on, a rescan becomes `gave_up` |
| `MEDSIGHT_DATA_DIR` / `MEDSIGHT_LOGS_DIR` | `data/` / `logs/` | Point at a persistent disk on the cloud host |
| `LOG_LEVEL` | `INFO` | Level for the JSON logs on stdout |

## Run the backend

```bash
uvicorn app.api:app --reload                     # local
uvicorn app.api:app --host 0.0.0.0 --port $PORT  # cloud host
```

Interactive API docs are served at `/docs`.

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
    what to do (`turn_box`: scan the side with the ingredients list).
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
  | `reviewed` | the team-reviewed dataset in `kb/` (from M2) | "Our team has reviewed this information." |
  | `ingredient` | team-reviewed ingredient descriptions (from M2) | "Our team hasn't reviewed this product yet. Based on its ingredients, ..." |
  | `web` | allow-listed web search (from M6) | "This medicine isn't in our reviewed list. The following information comes from the web and hasn't been reviewed by our team." |
  | `general` | the model's general knowledge (today's only tier) | "Warning: this medicine isn't in our reviewed list, so this is general information only and may be inaccurate." |

  Consider a distinct sound or vibration for anything other than `reviewed`.
  `product.prescription` and "This is a prescription medicine" come with
  `reviewed` answers from M2.

Other responses (both versions):
- **401:** missing or wrong API key.
- **413:** request body over 64 KB.
- **422:** invalid body: text over 5000 characters, more than 500 blocks, a
  `box` without 4 numbers, `attempt` below 1, a confidence outside 0–1.
- **503:** the LLM is busy or down (for example, the Groq rate limit or a
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

`GET /health` returns `{"status": "ok", "llm_provider": "..."}`.

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

Every scan, from the app or the webcam, also appends a full record to
`logs/run_log.jsonl`, including the OCR text and the parse. It's for local
accuracy checks; M3 replaces it on the server with a `scan_log` table that
drops OCR text after 30 days.

## Known limitations

- **Reviewed answers aren't wired in yet.** The reviewed dataset is being
  collected (see `kb/README.md`). Until retrieval is built (M2), every
  medicine gets the `general` tier, with its spoken warning.
- The OCR-confidence cut-off and the small-print ratio for `multiple` are
  starting guesses. Set them from the team's labeled scans.
- Not built: smart-glasses integration, shape/color classification.
