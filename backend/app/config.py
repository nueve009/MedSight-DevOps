"""
Central configuration for the Medicine Identification Assistant prototype.
Reads secrets from environment variables (.env) and defines fixed paths/constants.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; env vars can also be set directly

# --- Paths ---
# The backend/ folder (this file is in backend/app/). Data, logs, the cache
# vault and kb/ all live directly under it.
BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_VAULT_DIR = BASE_DIR / "cache_vault"          # Obsidian-compatible vault
CACHE_INDEX_PATH = CACHE_VAULT_DIR / "index.json"    # key -> note filename
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
WARNINGS_LOG_PATH = LOGS_DIR / "warnings_log.jsonl"
OCR_ENGINE_CHOICE_PATH = BASE_DIR / "config" / "ocr_engine_choice.json"
BENCHMARK_RESULTS_PATH = BASE_DIR / "config" / "ocr_benchmark_results.json"

for d in (CACHE_VAULT_DIR, DATA_DIR, LOGS_DIR, OCR_ENGINE_CHOICE_PATH.parent):
    d.mkdir(parents=True, exist_ok=True)

# --- LLM provider (parse + description steps) ---
# "groq" for the cloud backend, "ollama" for offline local work.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "groq").lower()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")

# --- Backend API ---
# Shared secret the mobile app sends in the X-API-Key header. If unset, the
# API refuses every /identify request rather than running open.
MEDSIGHT_API_KEY = os.environ.get("MEDSIGHT_API_KEY", "")
MAX_OCR_TEXT_CHARS = 5000  # bounds LLM cost per request

# --- Confidence handling (Section 6 / open item #3) ---
# OCR/parse confidence is normalized to 0.0-1.0 before this check.
LOW_CONFIDENCE_THRESHOLD = float(os.environ.get("LOW_CONFIDENCE_THRESHOLD", "0.55"))

# --- Description agent grounding (Section 6 / open item #1) ---
# Locked prompt instructs the model to ground descriptions in the PH FDA
# Drug Product Registration / verification portal rather than free-generating.
PH_FDA_REFERENCE_DOMAINS = [
    "fda.gov.ph",
    "verification.fda.gov.ph",
]

# --- Cache ---
# Cache key = normalized hash of generic_name + brand_name + dosage (see utils.py)
