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
# Verified medicine dataset: hand-curated CSVs in kb/, built into kb.sqlite
# by kb_build.py.
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
