# MedSight — Medicine Identification Backend

An assistive tool that helps visually impaired users identify Philippine OTC
and branded medicines, including blister pack foil, from the text printed on
the packaging.

- **Mobile app** (React Native + Expo, in the root of this repo): runs OCR on
  the phone (Apple Vision on iOS, Google ML Kit on Android), sends the text to
  this backend, and speaks the reply with `expo-speech`. No photos leave the
  phone.
- **Backend** (this folder, FastAPI): parses the brand, generic name and dosage
  with an LLM, checks confidence, serves repeat scans from a cache, writes a
  short description, and returns the exact text to speak.

```
phone OCR → POST /identify → parse (LLM) → confidence check → cache (SQLite)
          → description (LLM, on a cache miss) → spoken_text → phone TTS
```

## Folder structure

This backend lives in the `backend/` folder of the MedSight repo, next to the
mobile app. Run every command below from inside `backend/`.

```
backend/
├── app/        the server: what the cloud host runs (uvicorn app.api:app)
├── scripts/    laptop-only tools: webcam prototype, OCR benchmark, dataset builders
├── kb/         the verified medicine dataset (CSV files)
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
| `MEDSIGHT_API_KEY` | — | Shared secret the app sends as `X-API-Key`. If unset, `/identify` refuses every request. |
| `LOW_CONFIDENCE_THRESHOLD` | `0.55` | Any stage below this gets a spoken caveat |
| `MEDSIGHT_DATA_DIR` / `MEDSIGHT_LOGS_DIR` | `data/` / `logs/` | Point at a persistent disk on the cloud host |
| `LOG_LEVEL` | `INFO` | Level for the JSON logs on stdout |

## Run the backend

```bash
uvicorn app.api:app --reload                     # local
uvicorn app.api:app --host 0.0.0.0 --port $PORT  # cloud host
```

Interactive API docs are served at `/docs`.

## API contract (for the mobile team)

`POST /identify`, with header `X-API-Key: <MEDSIGHT_API_KEY>`.

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

- `text` is all recognized text, joined with newlines (max 5000 characters).
  If it's empty, the server joins `lines` instead.
- `ocr_confidence` is optional. Send `null` if the OCR library doesn't report
  one; the server then skips the OCR confidence check rather than treating it
  as 0.

Response (200):

```json
{
  "status": "ok | low_confidence | rescan",
  "brand_name": "Biogesic", "generic_name": "Paracetamol", "dosage": "500mg",
  "description": "A pain reliever and fever reducer.",
  "grounded": false, "source_url": null,
  "warnings": ["not verified"],
  "spoken_text": "Biogesic. Warning: this medicine is not in my verified list, ...",
  "cache": "hit | miss | null"
}
```

- **Always speak `spoken_text` exactly as sent.** It already includes the
  safety caveats.
- `rescan`: no text was read, or no medicine name was found. All name fields
  are `null`, and `spoken_text` asks the user to try again.
- `low_confidence`: the label may have been misread, because at least one
  reading stage (OCR, name extraction) was below the threshold. `warnings`
  lists which ones.
- `grounded`: whether the description comes from the verified dataset.
  - `true`: one of the 10 verified medicines.
  - `false`: any other medicine. The description is general AI-generated
    information, `warnings` includes `"not verified"`, and `spoken_text`
    gives the unverified warning **before** the description. It ends by
    advising the user to check the label or ask a pharmacist. Consider a
    distinct sound or vibration for these results.
- `status` and `grounded` are independent. For example, a clearly read
  medicine outside the verified list is `"ok"` with `grounded: false`.

Other responses:
- **401:** missing or wrong API key.
- **422:** the request body is invalid.
- **503:** the LLM is busy or down (for example, the Groq rate limit), or the
  server's API key isn't configured. When the LLM is the cause, the body
  includes a `spoken_text` asking the user to retry.

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
(method, path, status code, latency) and one per scan (status, cache, parse
confidence, description status), each with the request ID. These lines never
include the OCR text, keys or headers.

Every scan, from the app or the webcam, also appends a full record to
`logs/run_log.jsonl`: the OCR text, parsed fields, confidences, cache hit or
miss, and the final spoken text. Low-confidence stages also go to
`logs/warnings_log.jsonl`. These logs are how accuracy is scored by hand.

## Known limitations

- **Verified answers aren't wired in yet.** The verified dataset for 10
  medicines is being collected (see `kb/README.md`). Until the retrieval step
  is built, every medicine gets the unverified, general description, with
  its spoken warning.
- The confidence threshold is a starting guess. Tune it from real run logs.
- Not built: smart-glasses integration, shape/color classification.
