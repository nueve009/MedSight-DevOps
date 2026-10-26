"""The pipeline with the reviewed dataset: tiers, partial-match rescans, the Rx line."""
import pytest

from app import description_agent, llm_parse, speech
from app.pipeline import ScanRequest, identify, identify_scan
from tests.fixtures import labels
from tests.fixtures.fake_kb import build_kb

PARSE, DESCRIBE = llm_parse.PARSE_SYSTEM_PROMPT, description_agent.DESCRIPTION_SYSTEM_PROMPT
NEOZEP_ALIKE = labels.product("Neozep Forte", ["Phenylephrine HCl", "Chlorphenamine Maleate", "Paracetamol"],
                              ["10 mg", "2 mg", "500 mg"])


@pytest.fixture
def kb(tmp_path):
    return build_kb(tmp_path)


def scan(cache, kb, text=labels.BIOGESIC_TEXT, **kwargs):
    return identify_scan(ScanRequest(text, **kwargs), cache, kb)


def test_reviewed_answer_needs_no_description_call(fake_llm, cache, kb):
    fake_llm.reply(PARSE, labels.reply(labels.BIOGESIC))
    r = scan(cache, kb, ocr_confidence=0.9)
    assert (r["status"], r["tier"], r["cache"], r["matched_product_id"]) == ("ok", "reviewed", None,
                                                                            "biogesic-500-tab")
    assert r["description"] == "TEST: relieves mild pain and fever."
    assert r["sources"] == ["https://thefilipinodoctor.com/drug/test-biogesic-500-tab"]
    assert r["spoken_text"] == ("Biogesic, Paracetamol 500 milligrams. Our team has reviewed this information. "
                                "TEST: relieves mild pain and fever. "
                                "Please check the label or ask a pharmacist before taking it.")
    assert fake_llm.count(DESCRIBE) == 0


def test_reviewed_answer_speaks_the_datasets_spelling(fake_llm, cache, kb):
    text = "BI0GESIC\nParacetamol 500mg Tablet"
    fake_llm.reply(PARSE, labels.reply(labels.product("BI0GESIC", ["Paracetamol"], ["500mg"])))
    r = scan(cache, kb, text=text)
    assert r["product"]["brand"] == "Biogesic"
    assert r["spoken_text"].startswith("Biogesic, Paracetamol 500 milligrams.")


def test_rx_product_says_so_before_the_description(fake_llm, cache, kb):
    text = "DOLFENAL\nMefenamic acid 500 mg"
    fake_llm.reply(PARSE, labels.reply(labels.product("Dolfenal", ["Mefenamic acid"], ["500 mg"])))
    r = scan(cache, kb, text=text)
    assert r["product"]["prescription"] is True
    assert r["spoken_text"].index(speech.RX_LINE) < r["spoken_text"].index(speech.REVIEWED_LINE)


def test_low_ocr_confidence_keeps_the_tier_and_adds_the_caveat(fake_llm, cache, kb):
    fake_llm.reply(PARSE, labels.reply(labels.BIOGESIC))
    r = scan(cache, kb, ocr_confidence=0.3)
    assert (r["status"], r["tier"]) == ("ok_caveat", "reviewed")
    assert speech.READING_CAVEAT in r["spoken_text"]


def test_brand_without_strength_rescans_then_gives_up(fake_llm, cache, kb):
    text = "BIOGESIC BIOGESIC\nUNILAB"
    fake_llm.reply(PARSE, labels.reply(labels.product("BIOGESIC", [], [])))
    first = scan(cache, kb, text=text, attempt=1)
    assert (first["status"], first["rescan_reason"], first["hint"]) == ("rescan", "strength_unread", "turn_box")
    assert first["spoken_text"].startswith("I found Biogesic but couldn't read the strength.")
    third = scan(cache, kb, text=text, attempt=3)
    assert third["status"] == "gave_up"
    assert third["spoken_text"] == "I can read Biogesic but not the strength. Please ask someone to check the label."
    assert fake_llm.count(DESCRIBE) == 0


def test_combination_read_short_is_never_answered(fake_llm, cache, kb):
    fake_llm.reply(PARSE, labels.reply(labels.product("Alaxan FR", ["Paracetamol"], ["325 mg"])))
    r = scan(cache, kb, text=labels.ALAXAN_TEXT)
    assert (r["status"], r["rescan_reason"], r["tier"]) == ("rescan", "ingredients_incomplete", None)
    assert r["spoken_text"].startswith("I found Alaxan FR but couldn't read all of its ingredients.")


def test_same_ingredients_other_brand_gets_the_ingredient_tier(fake_llm, cache, kb):
    fake_llm.reply(PARSE, labels.reply(NEOZEP_ALIKE))
    r = scan(cache, kb, text=labels.NEOZEP_TEXT)
    assert (r["tier"], r["matched_product_id"], r["product"]["brand"]) == ("ingredient", None, "Neozep Forte")
    assert "Bioflu" not in r["spoken_text"]
    assert r["spoken_text"].startswith(
        "Neozep Forte, Phenylephrine HCl 10 milligrams, Chlorphenamine Maleate 2 milligrams and Paracetamol "
        "500 milligrams. Our team hasn't reviewed this product yet. Based on its ingredients, TEST: ")
    assert fake_llm.count(DESCRIBE) == 0


def test_unlisted_ingredients_fall_to_the_general_tier(fake_llm, cache, kb):
    text = "ZORBAXIL\nFakeamol 250 mg"
    fake_llm.reply(PARSE, labels.reply(labels.product("Zorbaxil", ["Fakeamol"], ["250 mg"])))
    fake_llm.reply(DESCRIBE, {"description": "I don't recognize this product.", "confidence": 0.1})
    r = scan(cache, kb, text=text)
    assert r["tier"] == "general"
    assert r["spoken_text"].count(speech.GENERAL_WARNING) == 1


def test_reviewed_wins_over_an_old_general_answer_in_the_cache(fake_llm, cache, kb):
    """Before the dataset existed, Biogesic got a general answer that was cached."""
    fake_llm.reply(PARSE, labels.reply(labels.BIOGESIC))
    fake_llm.reply(DESCRIBE, labels.GOOD_DESCRIPTION)
    assert scan(cache, None)["tier"] == "general"
    assert scan(cache, kb)["tier"] == "reviewed"


def test_v1_reports_reviewed_as_grounded(fake_llm, cache, kb):
    fake_llm.reply(PARSE, labels.reply(labels.BIOGESIC))
    r = identify(labels.BIOGESIC_TEXT, 0.9, cache, kb=kb)
    assert (r["grounded"], r["warnings"], r["source_url"]) == (
        True, [], "https://thefilipinodoctor.com/drug/test-biogesic-500-tab")


def test_v1_partial_is_a_rescan(fake_llm, cache, kb):
    fake_llm.reply(PARSE, labels.reply(labels.product("Biogesic", ["Paracetamol"], ["650 mg"])))
    r = identify(labels.BIOGESIC_TEXT + "\n650 mg", None, cache, kb=kb)
    assert (r["status"], r["brand_name"]) == ("rescan", None)
