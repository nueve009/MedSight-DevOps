"""Regression tests for the defects listed in the development plan, section 2."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import description_agent, llm_parse
from app.cache_store import CacheStore
from app.pipeline import identify
from app.utils import normalize_key

BACKEND_DIR = Path(__file__).resolve().parent.parent

BIOGESIC_PARSE = {"generic_name": "Paracetamol", "brand_name": "Biogesic",
                  "dosage": "500 mg", "confidence": 0.95}


@pytest.mark.parametrize("dosage", ["500mg", "500 MG", " 500  mg ", "500 Mg"])
def test_normalize_key_ignores_unit_spacing_and_case(dosage):
    assert normalize_key("Paracetamol", "Biogesic", dosage) == normalize_key("Paracetamol", "Biogesic", "500 mg")


def test_normalize_key_keeps_different_strengths_apart():
    assert normalize_key("Paracetamol", "Biogesic", "500 mg") != normalize_key("Paracetamol", "Biogesic", "50 mg")
    assert normalize_key("Paracetamol", "Biogesic", "500 mg") != normalize_key("Paracetamol", "Biogesic", "500 mcg")


def test_failed_description_is_not_cached(fake_llm, tmp_path):
    cache = CacheStore(tmp_path / "cache.sqlite")
    fake_llm.reply(llm_parse.PARSE_SYSTEM_PROMPT, BIOGESIC_PARSE)
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT, "not json at all")

    first = identify("BIOGESIC\nParacetamol 500 mg", 0.9, cache)
    assert first["cache"] == "miss"
    assert cache.get(normalize_key("Paracetamol", "Biogesic", "500 mg")) is None

    # The provider recovers: the next scan must generate a description again.
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT,
                   {"description": "A pain reliever and fever reducer.", "confidence": 0.5})
    second = identify("BIOGESIC\nParacetamol 500 mg", 0.9, cache)
    assert second["cache"] == "miss"
    assert second["description"] == "A pain reliever and fever reducer."
    assert fake_llm.count(description_agent.DESCRIPTION_SYSTEM_PROMPT) == 2


def test_good_description_is_cached(fake_llm, tmp_path):
    cache = CacheStore(tmp_path / "cache.sqlite")
    fake_llm.reply(llm_parse.PARSE_SYSTEM_PROMPT, BIOGESIC_PARSE)
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT,
                   {"description": "A pain reliever and fever reducer.", "confidence": 0.5})

    assert identify("BIOGESIC\nParacetamol 500 mg", 0.9, cache)["cache"] == "miss"
    assert identify("BIOGESIC\nParacetamol 500mg", 0.9, cache)["cache"] == "hit"
    assert fake_llm.count(description_agent.DESCRIPTION_SYSTEM_PROMPT) == 1


@pytest.mark.parametrize("reply, status", [
    ({"description": "A pain reliever and fever reducer.", "confidence": 0.5}, "ok"),
    ({"description": "I don't recognize this product.", "confidence": 0.1}, "not_recognized"),
    ({"description": "", "confidence": 0.5}, "failed"),
    ("{broken", "failed"),
])
def test_description_status(fake_llm, reply, status):
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT, reply)
    assert description_agent.get_description("Paracetamol", "Biogesic", "500 mg")["status"] == status


def test_importing_config_creates_no_folders(tmp_path):
    data_dir, logs_dir = tmp_path / "data", tmp_path / "logs"
    env = {**os.environ, "MEDSIGHT_DATA_DIR": str(data_dir), "MEDSIGHT_LOGS_DIR": str(logs_dir)}
    subprocess.run([sys.executable, "-c", "import app.config"], cwd=BACKEND_DIR, env=env, check=True)
    assert not data_dir.exists()
    assert not logs_dir.exists()
