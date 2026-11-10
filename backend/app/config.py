"""
Central configuration for the MedSight backend.
Reads secrets from environment variables (.env) and defines fixed paths/constants.

Importing this module has no side effects on disk. Call ensure_dirs() at
startup (api.py and the scripts do) to create the data and log folders.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; env vars can also be set directly

# --- Paths ---
# The backend/ folder (this file is in backend/app/). Data, logs and kb/ all
# live directly under it unless the environment points elsewhere.
BASE_DIR = Path(__file__).resolve().parent.parent
# On a cloud host, point these at a persistent disk so the cache and logs
# survive redeploys.
DATA_DIR = Path(os.environ.get("MEDSIGHT_DATA_DIR", BASE_DIR / "data"))
CACHE_DB_PATH = DATA_DIR / "cache.sqlite"
# Reviewed medicine dataset: CSVs in kb/, built into kb.sqlite by kb_build.py
# (local runs, tests, eval). On Railway it comes from Supabase (KB_BACKEND).
KB_DIR = BASE_DIR / "kb"
KB_DB_PATH = DATA_DIR / "kb.sqlite"
LOGS_DIR = Path(os.environ.get("MEDSIGHT_LOGS_DIR", BASE_DIR / "logs"))
RUN_LOG_PATH = LOGS_DIR / "run_log.jsonl"
OCR_ENGINE_CHOICE_PATH = BASE_DIR / "config" / "ocr_engine_choice.json"
BENCHMARK_RESULTS_PATH = BASE_DIR / "config" / "ocr_benchmark_results.json"


def ensure_dirs() -> None:
    """Create the data and log folders. Called at startup, never on import."""
    for d in (DATA_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)


# --- Cache and scan log (app/store/) ---
# "sqlite" (local file, the default) or "postgres" (Supabase on Railway).
CACHE_BACKEND = os.environ.get("CACHE_BACKEND", "sqlite").lower()
# Postgres connection string for the backend's own role (never the owner's).
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATABASE_SSLMODE = os.environ.get("DATABASE_SSLMODE", "require")   # TLS to Supabase; "disable" only locally
DB_CONNECT_TIMEOUT_S = float(os.environ.get("DB_CONNECT_TIMEOUT_S", "2"))
DB_QUERY_TIMEOUT_MS = int(os.environ.get("DB_QUERY_TIMEOUT_MS", "1000"))
# After a database failure, scans skip it for this long instead of waiting on it each time.
DB_SKIP_AFTER_FAILURE_S = float(os.environ.get("DB_SKIP_AFTER_FAILURE_S", "60"))
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "5"))
WEB_ANSWER_TTL_DAYS = 30            # web answers stop being served after this (decided 2026-10-19)
SCAN_LOG_OCR_RETENTION_DAYS = 30    # OCR text is cleared from scan_log after this (privacy, plan section 7 #9)

# --- Reviewed dataset source (app/retrieval.py, app/kb_source.py) ---
# "sqlite": data/kb.sqlite, built from kb/ by scripts/kb_build.py.
# "postgres": the approved rows of the kb_* tables in Supabase
# (migrations/0005_kb_dataset.sql), re-read every KB_REFRESH_S seconds.
# Defaults to CACHE_BACKEND, so a Supabase deploy reads the dataset from Supabase too.
KB_BACKEND = os.environ.get("KB_BACKEND", CACHE_BACKEND).lower()
# Connection for reading the dataset; empty = DATABASE_URL (the backend role, which can only read approved rows).
KB_DATABASE_URL = os.environ.get("KB_DATABASE_URL", "")
KB_REFRESH_S = float(os.environ.get("KB_REFRESH_S", "60"))

# --- Rate limits and the daily LLM budget (app/ratelimit.py) ---
# Per device (X-Device-Id, hashed) and per client address, in fixed UTC
# windows. The plan's 6 a minute would block a rescan every 5 seconds, so the
# default allows 15.
RATE_DEVICE_PER_MINUTE = int(os.environ.get("RATE_DEVICE_PER_MINUTE", "15"))
RATE_DEVICE_PER_DAY = int(os.environ.get("RATE_DEVICE_PER_DAY", "150"))
RATE_ADDRESS_PER_MINUTE = int(os.environ.get("RATE_ADDRESS_PER_MINUTE", "30"))
# Model calls (parse + describe) a UTC day for the whole service, under Groq's free tier.
LLM_DAILY_BUDGET = int(os.environ.get("LLM_DAILY_BUDGET", "180"))
# Optional caps per app key name, e.g. "testers-a=60,testers-b=60", so tester
# groups can't use up the pitch devices' share.
KEY_DAILY_LLM_CAPS = os.environ.get("KEY_DAILY_LLM_CAPS", "")
# Web lookups (app/web_search.py, M6): a search plus a model call, so they get
# their own, lower caps, counted only when a search actually runs (plan section 3, #5).
WEB_DEVICE_PER_DAY = int(os.environ.get("WEB_DEVICE_PER_DAY", "3"))
WEB_DAILY_BUDGET = int(os.environ.get("WEB_DAILY_BUDGET", "20"))
# Where the real client address comes from. Empty = the TCP peer (local runs).
# On Railway set "x-real-ip": its edge proxy overwrites that header.
CLIENT_IP_HEADER = os.environ.get("CLIENT_IP_HEADER", "").lower()
# Scans running at once; past it, a quick "busy, try again" (503).
MAX_CONCURRENT_SCANS = int(os.environ.get("MAX_CONCURRENT_SCANS", "8"))

# --- Web tier (app/web_search.py, M6) ---
# "tavily" turns the web tier on (with TAVILY_API_KEY set); empty = off, and
# unlisted medicines get the general tier as before.
WEB_SEARCH_PROVIDER = os.environ.get("WEB_SEARCH_PROVIDER", "").strip().lower()
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
WEB_SEARCH_TIMEOUT_S = float(os.environ.get("WEB_SEARCH_TIMEOUT_S", "8"))
# A medicine the web tier couldn't answer isn't searched again for this long.
WEB_MISS_TTL_DAYS = int(os.environ.get("WEB_MISS_TTL_DAYS", "7"))

# --- Logging ---
# One JSON object per line on stdout (see logging_setup.py).
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# --- LLM provider (parse + description steps) ---
# "groq" for the cloud backend, "ollama" for offline local work.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "groq").lower()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
# A scan shouldn't hang on a slow provider; past this the user hears "try again".
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "15"))

# --- Backend API ---
# Shared secret the mobile app sends in the X-API-Key header. If unset, the
# API refuses every /identify request rather than running open.
MEDSIGHT_API_KEY = os.environ.get("MEDSIGHT_API_KEY", "")
# More keys, as "name=key,name=key" (one per app release or tester group), so a
# key can be retired or cut off on its own. Names show up in logs, keys never.
MEDSIGHT_API_KEYS = os.environ.get("MEDSIGHT_API_KEYS", "")
# /docs, /redoc and /openapi.json: off unless API_DOCS=on, so a deployed
# service doesn't publish its schema (plan section 7, #12). Turn on locally.
API_DOCS = os.environ.get("API_DOCS", "off").strip().lower() in ("1", "on", "true", "yes")
# Admin routes (app/admin.py): off unless ADMIN_ENABLED=on; then one key per
# person, as "name=key,name=key" (the name goes in the audit log, the key never).
ADMIN_ENABLED = os.environ.get("ADMIN_ENABLED", "off").strip().lower() in ("1", "on", "true", "yes")
ADMIN_KEYS = os.environ.get("ADMIN_KEYS", "")
# Review console (app/review.py): connects as the medsight_review login, signs
# reviewers in through Supabase Auth, and is gated by ADMIN_ENABLED like /admin.
REVIEW_DATABASE_URL = os.environ.get("REVIEW_DATABASE_URL", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")   # public: it goes to the page
# Set for a project with a shared HS256 secret; empty = verify with the project's published keys (JWKS).
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
# Comma-separated origins allowed to call the API from a browser (the hosted page). Empty = none.
REVIEW_ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("REVIEW_ALLOWED_ORIGINS", "").split(",") if o.strip()]
EVIDENCE_BUCKET = "kb-evidence"
MAX_EVIDENCE_BYTES = 5 * 1024 * 1024
MAX_EVIDENCE_BODY_BYTES = MAX_EVIDENCE_BYTES + 64 * 1024   # the file plus the multipart wrapping
MAX_BODY_BYTES = 64 * 1024     # whole request body; larger gets 413
MAX_OCR_TEXT_CHARS = 5000      # request validation; longer gets 422
MAX_BLOCKS = 500               # text blocks per request; more gets 422
# Input check before any model call: longer text is a rescan ("move closer"),
# so a whole page of small print can't run up parse cost.
MAX_PARSE_CHARS = int(os.environ.get("MAX_PARSE_CHARS", "2000"))

# --- Scan rules (scan_rules.py) ---
# Below this OCR confidence (0-1, from the phone) an answer gets the spoken
# "I'm not fully sure of this reading" caveat. A starting guess: set it from
# the team's labeled scans (open decision 3). LOW_CONFIDENCE_THRESHOLD is the
# old name and still read.
OCR_CAVEAT_THRESHOLD = float(os.environ.get("OCR_CAVEAT_THRESHOLD",
                                            os.environ.get("LOW_CONFIDENCE_THRESHOLD", "0.55")))
# From this attempt on, a scan that would be a rescan "gives up" and says
# only what was read clearly, so the user isn't stuck rescanning.
MAX_SCAN_ATTEMPTS = int(os.environ.get("MAX_SCAN_ATTEMPTS", "3"))
