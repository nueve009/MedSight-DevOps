"""Combination safety: products that share an ingredient never share a cache key."""
import itertools

import pytest

from app.llm_parse import PARSE_PROMPT_VERSION
from app.utils import answer_key, raw_text_key

PRODUCTS = {
    "Biogesic": ("Biogesic", ["Paracetamol"], ["500 mg"]),
    "Alaxan FR": ("Alaxan FR", ["Ibuprofen", "Paracetamol"], ["200 mg", "325 mg"]),
    "Neozep Forte": ("Neozep Forte", ["Phenylephrine HCl", "Chlorphenamine Maleate", "Paracetamol"],
                     ["10 mg", "2 mg", "500 mg"]),
    "Advil": ("Advil", ["Ibuprofen"], ["200 mg"]),
    "generic paracetamol": (None, ["Paracetamol"], ["500 mg"]),
    "generic ibuprofen": (None, ["Ibuprofen"], ["200 mg"]),
}


@pytest.mark.parametrize("a, b", list(itertools.combinations(PRODUCTS, 2)))
def test_every_pair_has_its_own_key(a, b):
    assert answer_key(*PRODUCTS[a]) != answer_key(*PRODUCTS[b])


def test_combination_never_matches_one_of_its_ingredients():
    assert answer_key("Alaxan FR", ["Paracetamol"], ["325 mg"]) != answer_key(*PRODUCTS["Alaxan FR"])


def test_ingredient_order_and_spacing_do_not_matter():
    assert answer_key("ALAXAN FR", ["Paracetamol", "Ibuprofen"], ["325mg", "200 MG"]) == \
        answer_key(*PRODUCTS["Alaxan FR"])


def test_answer_key_needs_one_strength_per_ingredient():
    with pytest.raises(ValueError):
        answer_key("Neozep Forte", ["Phenylephrine HCl", "Paracetamol"], ["500 mg"])


def test_raw_text_key_normalizes_case_and_spacing_only():
    key = raw_text_key("BIOGESIC\nParacetamol 500 mg", "m", PARSE_PROMPT_VERSION)
    assert key == raw_text_key("  biogesic  paracetamol 500 MG ", "m", PARSE_PROMPT_VERSION)
    assert key != raw_text_key("BIOGESIC\nParacetamol 650 mg", "m", PARSE_PROMPT_VERSION)


def test_raw_text_key_changes_with_model_and_prompt():
    key = raw_text_key("BIOGESIC", "model-a", "2")
    assert key != raw_text_key("BIOGESIC", "model-b", "2")
    assert key != raw_text_key("BIOGESIC", "model-a", "3")
