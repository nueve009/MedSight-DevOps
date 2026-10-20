"""Scan rules: each one on real-looking OCR text, no LLM involved."""
import pytest

from app import scan_rules as r
from app.llm_parse import ParsedProduct
from app.scan_rules import Block, CaptureHints
from tests.fixtures import labels

NO_HINTS = CaptureHints()


def p(d: dict) -> ParsedProduct:
    return ParsedProduct(d["brand_name"] or None, tuple(d["generic_names"]), tuple(d["strengths"]))


def decide(products, text, blocks=(), attempt=1, hints=NO_HINTS, ocr_confidence=0.9):
    return r.decide([p(x) for x in products], text, list(blocks), attempt, hints, ocr_confidence)


# --- input checks ---

@pytest.mark.parametrize("text, reason", [
    ("", r.NO_TEXT), ("   \n ", r.NO_TEXT), ("12345 // 678", r.NO_TEXT),
    ("x" * 2001, r.TOO_MUCH_TEXT), ("BIOGESIC 500", None),
])
def test_input_reason(text, reason):
    assert r.input_reason(text) == reason


# --- answers ---

def test_single_clear_product_is_ok():
    d = decide([labels.BIOGESIC], labels.BIOGESIC_TEXT)
    assert d.status == "ok" and d.product.brand == "Biogesic"


def test_neozep_with_three_ingredients_is_ok():
    d = decide([labels.NEOZEP], labels.NEOZEP_TEXT)
    assert d.status == "ok"
    assert len(d.product.generics) == 3


def test_low_ocr_confidence_answers_with_a_caveat():
    assert decide([labels.BIOGESIC], labels.BIOGESIC_TEXT, ocr_confidence=0.4).status == "ok_caveat"


def test_missing_ocr_confidence_is_not_treated_as_low():
    assert decide([labels.BIOGESIC], labels.BIOGESIC_TEXT, ocr_confidence=None).status == "ok"


# --- hard rescans ---

def test_no_products_is_no_name():
    assert decide([], "Lot 12345 Exp 2027").reason == r.NO_NAME


def test_name_not_in_text_is_a_rescan():
    """A prompt-injected or invented name never gets answered."""
    d = decide([labels.product("Zorbaxil", ["Fakeamol"], ["500 mg"])], labels.BIOGESIC_TEXT)
    assert (d.status, d.reason) == ("rescan", r.NAME_NOT_IN_TEXT)


def test_invented_ingredient_is_a_rescan():
    d = decide([labels.product("Biogesic", ["Ibuprofen"], ["500 mg"])], labels.BIOGESIC_TEXT)
    assert d.reason == r.NAME_NOT_IN_TEXT


def test_invented_strength_is_a_rescan():
    d = decide([labels.product("Biogesic", ["Paracetamol"], ["650 mg"])], labels.BIOGESIC_TEXT)
    assert (d.reason, d.hint) == (r.STRENGTH_UNREAD, r.TURN_BOX)


def test_neozep_read_with_one_strength_is_a_rescan():
    neozep = labels.product("Neozep Forte", ["Phenylephrine HCl", "Chlorphenamine Maleate", "Paracetamol"],
                            ["", "", "500 mg"])
    d = decide([neozep], labels.NEOZEP_TEXT)
    assert (d.status, d.reason, d.hint) == ("rescan", r.STRENGTH_UNREAD, r.TURN_BOX)


def test_fewer_strengths_than_ingredients_is_a_rescan():
    neozep = labels.product("Neozep Forte", ["Phenylephrine HCl", "Paracetamol"], ["500 mg"])
    assert decide([neozep], labels.NEOZEP_TEXT).reason == r.STRENGTH_UNREAD


def test_brand_only_is_answered():
    """Nothing to pair, so nothing is missing; M2's dataset match decides whether the strength is needed."""
    assert decide([labels.product("Biogesic", [], [])], labels.BIOGESIC_TEXT).status == "ok"


# --- several medicines ---

def test_two_brands_are_multiple():
    assert decide([labels.BIOGESIC, labels.ADVIL], labels.TWO_BOXES_TEXT).status == "multiple"


def test_two_strengths_of_one_brand_are_multiple():
    text = labels.BIOGESIC_TEXT + "\nBIOGESIC Paracetamol 250 mg"
    second = labels.product("Biogesic", ["Paracetamol"], ["250 mg"])
    assert decide([labels.BIOGESIC, second], text).status == "multiple"


def test_same_brand_repeated_is_one_product():
    repeat = labels.product("BIOGESIC", ["Paracetamol"], ["500mg"])
    assert decide([labels.BIOGESIC, repeat], labels.BIOGESIC_TEXT).status == "ok"


def test_unbranded_repeat_of_the_ingredients_is_one_product():
    repeat = labels.product("", ["Paracetamol"], ["500 mg"])
    d = decide([repeat, labels.BIOGESIC], labels.BIOGESIC_TEXT)
    assert d.status == "ok" and d.product.brand == "Biogesic"


def test_unbranded_different_ingredient_is_multiple():
    text = labels.BIOGESIC_TEXT + "\nIbuprofen 200 mg"
    other = labels.product("", ["Ibuprofen"], ["200 mg"])
    assert decide([labels.BIOGESIC, other], text).status == "multiple"


def test_also_available_in_small_print_is_not_multiple():
    blocks = [Block("BIOGESIC", 0.95, (0.1, 0.10, 0.8, 0.25)),           # big brand on the front
              Block("Also available: Biogesic for Kids 250 mg/5 mL", 0.9, (0.1, 0.90, 0.9, 0.93))]
    d = decide([labels.BIOGESIC, labels.BIOGESIC_KIDS], labels.ALSO_AVAILABLE_TEXT, blocks)
    assert d.status == "ok" and d.product.brand == "Biogesic"


def test_two_boxes_side_by_side_are_multiple_with_blocks():
    blocks = [Block("BIOGESIC", 0.95, (0.05, 0.2, 0.45, 0.32)),
              Block("Advil", 0.95, (0.55, 0.22, 0.95, 0.33))]
    assert decide([labels.BIOGESIC, labels.ADVIL], labels.TWO_BOXES_TEXT, blocks).status == "multiple"


def test_without_blocks_a_mentioned_product_is_multiple():
    """Without positions the code can't tell small print apart; asking is the safe side."""
    assert decide([labels.BIOGESIC, labels.BIOGESIC_KIDS], labels.ALSO_AVAILABLE_TEXT).status == "multiple"


# --- retry limit and hints ---

@pytest.mark.parametrize("attempt, status", [(1, "rescan"), (2, "rescan"), (3, "gave_up"), (7, "gave_up")])
def test_gives_up_from_the_third_attempt(attempt, status):
    neozep = labels.product("Neozep Forte", ["Paracetamol"], [""])
    d = decide([neozep], labels.NEOZEP_TEXT, attempt=attempt)
    assert d.status == status
    assert d.reason == r.STRENGTH_UNREAD
    assert (d.hint is None) == (status == "gave_up")


def test_multiple_never_gives_up():
    assert decide([labels.BIOGESIC, labels.ADVIL], labels.TWO_BOXES_TEXT, attempt=5).status == "multiple"


@pytest.mark.parametrize("reason, hints, hint", [
    (r.NO_TEXT, CaptureHints(dark=True), r.MORE_LIGHT),
    (r.NO_TEXT, CaptureHints(blurry=True), r.MOVE_CLOSER),
    (r.NO_NAME, CaptureHints(text_small=True), r.MOVE_CLOSER),
    (r.NO_NAME, CaptureHints(dark=True, text_small=True), r.MORE_LIGHT),
    (r.NO_NAME, NO_HINTS, None),
    (r.TOO_MUCH_TEXT, NO_HINTS, r.MOVE_CLOSER),
    (r.STRENGTH_UNREAD, CaptureHints(dark=True), r.TURN_BOX),
    (r.INGREDIENTS_INCOMPLETE, NO_HINTS, r.TURN_BOX),
])
def test_hints(reason, hints, hint):
    assert r.hint_for(reason, hints) == hint


# --- partial match against the dataset (wired in by M2) ---

def same(a, b):
    return a.lower() == b.lower()


def test_partial_match_full_agreement():
    assert r.partial_match_reason(p(labels.ALAXAN), ["Ibuprofen", "Paracetamol"], ["200 mg", "325 mg"], same) is None


def test_partial_match_combination_read_with_fewer_ingredients():
    alaxan_one = ParsedProduct("Alaxan FR", ("Paracetamol",), ("325 mg",))
    reason = r.partial_match_reason(alaxan_one, ["Ibuprofen", "Paracetamol"], ["200 mg", "325 mg"], same)
    assert reason == r.INGREDIENTS_INCOMPLETE


@pytest.mark.parametrize("strength", ["", "650 mg"])
def test_partial_match_strength_missing_or_different(strength):
    biogesic = ParsedProduct("Biogesic", ("Paracetamol",), (strength,))
    assert r.partial_match_reason(biogesic, ["Paracetamol"], ["500 mg"], same) == r.STRENGTH_UNREAD
