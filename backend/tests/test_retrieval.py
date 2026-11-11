"""
Dataset matching. The one failure that matters most: a confident wrong
"reviewed" answer. Every test here uses the fake dataset in fake_kb.review_rows().
"""
import pytest

from app import retrieval
from app.llm_parse import ParsedProduct
from app.retrieval import INGREDIENT, NONE, PARTIAL, REVIEWED, KnowledgeBase
from app.scan_rules import INGREDIENTS_INCOMPLETE, STRENGTH_UNREAD
from tests.fixtures.fake_kb import build_kb, ingredient, product, review_rows, tfd_passage

NEOZEP_LIKE = ("Phenylephrine HCl", "Chlorphenamine Maleate", "Paracetamol")


@pytest.fixture
def kb(tmp_path) -> KnowledgeBase:
    return build_kb(tmp_path)


def match(kb, brand, generics=(), strengths=()):
    return kb.match(ParsedProduct(brand, tuple(generics), tuple(strengths)))


# --- reviewed ---

def test_exact_match_is_reviewed(kb):
    m = match(kb, "Biogesic", ["Paracetamol"], ["500 mg"])
    assert (m.kind, m.product.product_id) == (REVIEWED, "biogesic-500-tab")
    assert m.description == "TEST: relieves mild pain and fever."
    assert m.sources == ("https://thefilipinodoctor.com/drug/test-biogesic-500-tab",)


@pytest.mark.parametrize("brand, generics, strengths", [
    ("BIOGESIC", ["paracetamol"], ["500mg"]),                       # case and spacing
    ("Biogesic", ["Acetaminophen"], ["500 MG"]),                     # synonym
    ("ALAXAN", ["Paracetamol", "Ibuprofen"], ["325 mg", "200 mg"]),  # alias, any order
    ("Buscopan", ["Hyoscine N-butylbromide"], ["10 mg"]),            # salt punctuation
    ("Bioflu", ["Phenylephrine hydrochloride", "Chlorpheniramine maleate", "Paracetamol"],
     ["10 mg", "2 mg", "500 mg"]),                                   # synonyms in a combination
])
def test_spelling_variants_still_match(kb, brand, generics, strengths):
    assert match(kb, brand, generics, strengths).kind == REVIEWED


def test_fuzzy_brand_with_identical_contents_is_reviewed(kb):
    """An OCR slip in the brand ("Bi0gesic") is fine when everything else agrees."""
    m = match(kb, "Bi0gesic", ["Paracetamol"], ["500 mg"])
    assert (m.kind, m.product.brand) == (REVIEWED, "Biogesic")


def test_rx_flag_comes_from_the_dataset(kb):
    assert match(kb, "Dolfenal", ["Mefenamic acid"], ["500 mg"]).product.rx is True
    assert match(kb, "Biogesic", ["Paracetamol"], ["500 mg"]).product.rx is False


def test_each_form_matches_on_its_own_strength(kb):
    assert match(kb, "Tempra", ["Paracetamol"], ["120 mg/5 mL"]).product.product_id == "tempra-120-syr"
    assert match(kb, "Tempra", ["Paracetamol"], ["325 mg"]).product.product_id == "tempra-325-tab"


# --- partial: a listed brand that doesn't fully agree is a rescan ---

def test_brand_only_is_a_strength_rescan(kb):
    m = match(kb, "Biogesic")
    assert (m.kind, m.reason, m.product.brand) == (PARTIAL, STRENGTH_UNREAD, "Biogesic")


def test_wrong_strength_is_a_strength_rescan(kb):
    assert match(kb, "Biogesic", ["Paracetamol"], ["650 mg"]).reason == STRENGTH_UNREAD


def test_a_clearly_read_strength_we_dont_list_is_an_unlisted_product(kb):
    p = ParsedProduct("Dolfenal", ("Mefenamic acid",), ("250 mg",))
    assert (kb.match(p, clear_reading=True).kind, kb.match(p).kind) == (NONE, PARTIAL)


def test_a_clearly_read_other_strength_can_use_the_ingredient_tier(kb):
    p = ParsedProduct("Biogesic", ("Paracetamol",), ("650 mg",))
    assert kb.match(p, clear_reading=True).kind == INGREDIENT


@pytest.mark.parametrize("brand, generics, strengths", [
    ("Biogesic", (), ()),                                  # nothing read but the brand
    ("Biogesic", ("Paracetamol",), ("",)),                 # strength faded
    ("Biogesic", ("Ibuprofen",), ("200 mg",)),             # other ingredients
    ("Alaxan FR", ("Paracetamol",), ("325 mg",)),          # combination read short
])
def test_a_clear_reading_still_rescans_anything_but_another_strength(kb, brand, generics, strengths):
    assert kb.match(ParsedProduct(brand, generics, strengths), clear_reading=True).kind == PARTIAL


def test_a_clear_reading_still_matches_the_listed_strength(kb):
    p = ParsedProduct("Biogesic", ("Paracetamol",), ("500 mg",))
    assert kb.match(p, clear_reading=True).kind == REVIEWED


def test_combination_read_with_fewer_ingredients_is_incomplete(kb):
    """Alaxan FR read as paracetamol only must never become Biogesic or an answer."""
    m = match(kb, "Alaxan FR", ["Paracetamol"], ["325 mg"])
    assert (m.kind, m.reason) == (PARTIAL, INGREDIENTS_INCOMPLETE)


def test_listed_brand_with_other_ingredients_is_a_rescan(kb):
    assert match(kb, "Biogesic", ["Ibuprofen"], ["200 mg"]).kind == PARTIAL


def test_brand_with_several_forms_prefers_the_strength_reason(kb):
    m = match(kb, "Tempra", ["Paracetamol"], ["250 mg"])
    assert (m.kind, m.reason) == (PARTIAL, STRENGTH_UNREAD)


# --- brand level: a give-up where the strength can't be read ---

def level(kb, brand, generics=(), strengths=()):
    return kb.brand_level(ParsedProduct(brand, tuple(generics), tuple(strengths)))


def test_brand_level_for_a_brand_read_alone(kb):
    assert level(kb, "Biogesic").brand == "Biogesic"


def test_brand_level_with_the_ingredient_read_and_strength_faded(kb):
    assert level(kb, "Biogesic", ["Acetaminophen"], [""]).brand == "Biogesic"   # a synonym still agrees


def test_brand_level_when_every_form_says_the_same(kb):
    assert level(kb, "Tempra").brand == "Tempra"   # two strengths, same ingredients, description, Rx


def test_brand_level_is_never_for_a_fuzzy_brand(kb):
    assert level(kb, "Bi0gesic") is None


def test_brand_level_needs_a_brand_on_the_list(kb):
    assert level(kb, "Unlistedol") is None
    assert level(kb, None, ["Paracetamol"], [""]) is None


def test_brand_level_refuses_a_fully_read_strength(kb):
    assert level(kb, "Biogesic", ["Paracetamol"], ["650 mg"]) is None
    assert level(kb, "Biogesic", ["Paracetamol"], ["500 mg"]) is None


def test_brand_level_refuses_a_read_ingredient_the_brand_doesnt_have(kb):
    assert level(kb, "Biogesic", ["Ibuprofen"], [""]) is None


def test_brand_level_refuses_a_partly_read_strength_that_disagrees(kb):
    assert level(kb, "Alaxan FR", ["Ibuprofen", "Paracetamol"], ["400 mg", ""]) is None
    assert level(kb, "Alaxan FR", ["Ibuprofen", "Paracetamol"], ["200 mg", ""]).brand == "Alaxan FR"


@pytest.mark.parametrize("change", [
    {"spoken_description": "TEST: something else."},
    {"classification": "Rx"},
    {"generic_names": "Ibuprofen"},
])
def test_brand_level_refuses_a_brand_whose_products_differ(tmp_path, change):
    rows = review_rows()
    rows["products"].append(product("tempra-250-tab", "Tempra", "Paracetamol", "250 mg", **change))
    rows["passages"].append(tfd_passage("tempra-250-tab"))
    assert level(build_kb(tmp_path, rows), "Tempra") is None


# --- never the wrong product ---

def test_same_ingredients_under_another_brand_is_never_that_brand(kb):
    """NEOZEP has exactly Bioflu's ingredients and strengths: it gets the ingredient
    tier, never Bioflu's reviewed answer."""
    m = match(kb, "Neozep Forte", NEOZEP_LIKE, ["10 mg", "2 mg", "500 mg"])
    assert m.kind == INGREDIENT
    assert m.product is None


def test_generic_with_a_listed_products_contents_is_the_ingredient_tier(kb):
    """Unbranded paracetamol 500 mg is not Biogesic."""
    m = match(kb, None, ["Paracetamol"], ["500 mg"])
    assert (m.kind, m.product) == (INGREDIENT, None)


def test_near_miss_brand_with_other_contents_never_names_the_listed_brand(kb):
    """'Biogesik' with ibuprofen is a different, unlisted product: no reviewed answer,
    and no 'I found Biogesic' rescan either."""
    m = match(kb, "Biogesik", ["Ibuprofen"], ["200 mg"])
    assert m.kind == INGREDIENT
    assert m.product is None


def test_fuzzy_brand_never_gives_a_partial(kb):
    assert match(kb, "Biogesik").kind == NONE


def test_unknown_ingredient_blocks_a_match(kb):
    assert match(kb, "Biogesic", ["Paracetamol", "Caffeine"], ["500 mg", "65 mg"]).kind == PARTIAL
    assert match(kb, "Zorbaxil", ["Fakeamol"], ["500 mg"]).kind == NONE


# --- ingredient tier ---

def test_ingredient_tier_joins_each_description_once(kb):
    m = match(kb, "Neozep Forte", NEOZEP_LIKE, ["10 mg", "2 mg", "500 mg"])
    assert m.description == ("TEST: Phenylephrine relieves a blocked nose. TEST: Chlorphenamine relieves "
                             "sneezing and a runny nose. TEST: Paracetamol relieves pain and fever.")
    assert len(m.sources) == 3


def test_ingredient_tier_needs_every_ingredient_described(kb):
    """Mefenamic acid is listed by name only, so an unlisted mefenamic product gets no ingredient tier."""
    assert match(kb, "Ponstan", ["Mefenamic acid"], ["500 mg"]).kind == NONE
    assert match(kb, "Medicol", ["Ibuprofen", "Mefenamic acid"], ["200 mg", "250 mg"]).kind == NONE


# --- dataset edge cases ---

def test_ambiguous_full_match_is_not_answered_as_reviewed(tmp_path):
    rows = review_rows()
    rows["products"].append(product("biogesic-500-cap", "Biogesic", "Paracetamol", "500 mg",
                                    dosage_form="Capsule", spoken_description="TEST: something else."))
    rows["passages"].append(tfd_passage("biogesic-500-cap"))
    kb = build_kb(tmp_path, rows)
    assert match(kb, "Biogesic", ["Paracetamol"], ["500 mg"]).kind == INGREDIENT


def test_same_description_across_forms_is_still_reviewed(tmp_path):
    rows = review_rows()
    rows["products"].append(product("biogesic-500-cap", "Biogesic", "Paracetamol", "500 mg",
                                    dosage_form="Capsule", spoken_description="TEST: relieves mild pain and fever."))
    rows["passages"].append(tfd_passage("biogesic-500-cap"))
    assert match(build_kb(tmp_path, rows), "Biogesic", ["Paracetamol"], ["500 mg"]).kind == REVIEWED


def test_missing_kb_file_gives_an_empty_dataset(tmp_path):
    kb = KnowledgeBase.load(tmp_path / "nope.sqlite")
    assert kb.products == [] and kb.version is None
    assert match(kb, "Biogesic", ["Paracetamol"], ["500 mg"]).kind == NONE


def test_version_is_loaded(kb):
    assert kb.version and len(kb.version) == 12


def test_from_rows_without_sqlite():
    kb = KnowledgeBase.from_rows(
        [product("x-tab", "Xyz", "Fakeamol", "1 mg")],
        [ingredient("Fakeamol", description="TEST: does fake things.")],
        [tfd_passage("x-tab")])
    assert match(kb, "Xyz", ["Fakeamol"], ["1mg"]).kind == REVIEWED


@pytest.mark.parametrize("a, b", [("Kremil-S", "KREMIL S"), ("Kremil-S", "kremils")])
def test_brand_key(a, b):
    assert retrieval.brand_key(a) == retrieval.brand_key(b)
