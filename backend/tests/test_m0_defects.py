"""Regression tests for the defects listed in the development plan, section 2."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import description_agent, llm_parse
from app.llm_parse import ParsedProduct
from app.pipeline import ScanRequest, identify_scan
from app.utils import answer_key
from tests.fixtures import labels

BACKEND_DIR = Path(__file__).resolve().parent.parent
BIOGESIC = ParsedProduct("Biogesic", ("Paracetamol",), ("500 mg",))


@pytest.mark.parametrize("strength", ["500mg", "500 MG", " 500  mg ", "500 Mg"])
def test_answer_key_ignores_unit_spacing_and_case(strength):
    assert answer_key("Biogesic", ["Paracetamol"], [strength]) == answer_key("Biogesic", ["Paracetamol"], ["500 mg"])


def test_answer_key_keeps_different_strengths_apart():
    key = answer_key("Biogesic", ["Paracetamol"], ["500 mg"])
    assert key != answer_key("Biogesic", ["Paracetamol"], ["50 mg"])
    assert key != answer_key("Biogesic", ["Paracetamol"], ["500 mcg"])


def test_failed_description_is_not_cached(fake_llm, cache):
    fake_llm.reply(llm_parse.PARSE_SYSTEM_PROMPT, labels.reply(labels.BIOGESIC))
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT, "not json at all")

    first = identify_scan(ScanRequest(labels.BIOGESIC_TEXT, ocr_confidence=0.9), cache)
    assert first["cache"] == "miss"
    assert cache.get_answer(answer_key("Biogesic", ["Paracetamol"], ["500 mg"])) is None

    # The provider recovers: the next scan must generate a description again.
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT, labels.GOOD_DESCRIPTION)
    second = identify_scan(ScanRequest(labels.BIOGESIC_TEXT, ocr_confidence=0.9), cache)
    assert second["cache"] == "miss"
    assert second["description"] == "A pain reliever and fever reducer."
    assert fake_llm.count(description_agent.DESCRIPTION_SYSTEM_PROMPT) == 2


def test_good_description_is_cached(fake_llm, cache):
    fake_llm.reply(llm_parse.PARSE_SYSTEM_PROMPT, labels.reply(labels.BIOGESIC))
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT, labels.GOOD_DESCRIPTION)

    assert identify_scan(ScanRequest(labels.BIOGESIC_TEXT), cache)["cache"] == "miss"
    # A different photo of the same box: the strength now reads "500mg".
    fake_llm.reply(llm_parse.PARSE_SYSTEM_PROMPT,
                   labels.reply(labels.product("Biogesic", ["Paracetamol"], ["500mg"])))
    assert identify_scan(ScanRequest(labels.BIOGESIC_TEXT + "\nLot 123"), cache)["cache"] == "hit"
    assert fake_llm.count(description_agent.DESCRIPTION_SYSTEM_PROMPT) == 1


@pytest.mark.parametrize("reply, status", [
    ({"description": "A pain reliever and fever reducer.", "confidence": 0.5}, "ok"),
    ({"description": "I don't recognize this product.", "confidence": 0.1}, "not_recognized"),
    ({"description": "", "confidence": 0.5}, "failed"),
    ("{broken", "failed"),
])
def test_description_status(fake_llm, reply, status):
    fake_llm.reply(description_agent.DESCRIPTION_SYSTEM_PROMPT, reply)
    assert description_agent.get_description(BIOGESIC)["status"] == status


def test_importing_config_creates_no_folders(tmp_path):
    data_dir, logs_dir = tmp_path / "data", tmp_path / "logs"
    env = {**os.environ, "MEDSIGHT_DATA_DIR": str(data_dir), "MEDSIGHT_LOGS_DIR": str(logs_dir)}
    subprocess.run([sys.executable, "-c", "import app.config"], cwd=BACKEND_DIR, env=env, check=True)
    assert not data_dir.exists()
    assert not logs_dir.exists()
