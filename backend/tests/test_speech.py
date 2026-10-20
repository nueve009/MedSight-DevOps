"""
Snapshot tests for every spoken template. These strings are what a blind user
hears: a change here needs a reviewer's approval and a listen through the
phone's text-to-speech (development plan, sections 5 and 8).
"""
import pytest

from app import speech
from app.llm_parse import ParsedProduct

BIOGESIC = ParsedProduct("Biogesic", ("Paracetamol",), ("500 mg",))
NEOZEP = ParsedProduct("Neozep Forte", ("Phenylephrine HCl", "Chlorphenamine Maleate", "Paracetamol"),
                       ("10 mg", "2 mg", "500mg"))
DESCRIPTION = "Used to relieve mild pain and reduce fever"


@pytest.mark.parametrize("strength, spoken", [
    ("500 mg", "500 milligrams"),
    ("500mg", "500 milligrams"),
    ("1 g", "1 gram"),
    ("250 mg/5 mL", "250 milligrams per 5 milliliters"),
    ("100 mcg", "100 micrograms"),
    ("1%", "1 percent"),
    ("400 IU", "400 international units"),
])
def test_say_strength(strength, spoken):
    assert speech.say_strength(strength) == spoken


def test_name_lines():
    assert speech.name_line(BIOGESIC) == "Biogesic, Paracetamol 500 milligrams."
    assert speech.name_line(NEOZEP) == ("Neozep Forte, Phenylephrine HCl 10 milligrams, Chlorphenamine Maleate "
                                        "2 milligrams and Paracetamol 500 milligrams.")
    assert speech.name_line(ParsedProduct("Biogesic")) == "Biogesic."
    assert speech.name_line(ParsedProduct(None, ("Paracetamol",), ("500 mg",))) == "Paracetamol 500 milligrams."


def test_reviewed_answer():
    assert speech.answer(BIOGESIC, speech.REVIEWED, DESCRIPTION) == (
        "Biogesic, Paracetamol 500 milligrams. Our team has reviewed this information. "
        "Used to relieve mild pain and reduce fever. "
        "Please check the label or ask a pharmacist before taking it.")


def test_reviewed_prescription_answer_with_caveat():
    assert speech.answer(BIOGESIC, speech.REVIEWED, DESCRIPTION, prescription=True, caveat=True) == (
        "Biogesic, Paracetamol 500 milligrams. This is a prescription medicine. "
        "Our team has reviewed this information. Used to relieve mild pain and reduce fever. "
        "I'm not fully sure of this reading. Please check the label or ask a pharmacist before taking it.")


def test_ingredient_answer():
    assert speech.answer(BIOGESIC, speech.INGREDIENT, "Paracetamol is used to relieve mild pain.") == (
        "Biogesic, Paracetamol 500 milligrams. Our team hasn't reviewed this product yet. "
        "Based on its ingredients, paracetamol is used to relieve mild pain. "
        "Please check the label or ask a pharmacist before taking it.")


def test_web_answer():
    assert speech.answer(BIOGESIC, speech.WEB, DESCRIPTION, source_site="MIMS") == (
        "Biogesic, Paracetamol 500 milligrams. This medicine isn't in our reviewed list. "
        "The following information comes from the web and hasn't been reviewed by our team. "
        "According to MIMS, used to relieve mild pain and reduce fever. "
        "Please check the label or ask a pharmacist before taking it.")


def test_general_answer():
    assert speech.answer(BIOGESIC, speech.GENERAL, DESCRIPTION) == (
        "Biogesic, Paracetamol 500 milligrams. Warning: this medicine isn't in our reviewed list, "
        "so this is general information only and may be inaccurate. "
        "Used to relieve mild pain and reduce fever. "
        "Please check the label or ask a pharmacist before taking it.")


def test_warning_always_comes_before_the_description():
    for tier in (speech.INGREDIENT, speech.WEB, speech.GENERAL):
        spoken = speech.answer(BIOGESIC, tier, "UNIQUE-DESCRIPTION")
        assert spoken.index("reviewed") < spoken.lower().index("unique-description")


@pytest.mark.parametrize("reason, hint, product, spoken", [
    ("no_text", "more_light", None, "I couldn't read any text. Find more light and try again."),
    ("no_text", None, None, "I couldn't read any text. Please try again."),
    ("too_much_text", "move_closer", None,
     "There's a lot of text in view. Move the phone a little closer to the label and try again."),
    ("no_name", "move_closer", None, "I read some text but couldn't find a medicine name. "
                                     "Move the phone a little closer to the label and try again."),
    ("name_not_in_text", None, None, "I'm not sure I read the name correctly. Please try again."),
    ("strength_unread", "turn_box", BIOGESIC, "I found Biogesic but couldn't read the strength. "
                                              "Turn the box and scan the side with the ingredients."),
    ("strength_unread", "turn_box", None, "I found the medicine but couldn't read the strength. "
                                          "Turn the box and scan the side with the ingredients."),
    ("ingredients_incomplete", "turn_box", ParsedProduct("Alaxan FR"),
     "I found Alaxan FR but couldn't read all of its ingredients. "
     "Turn the box and scan the side with the ingredients."),
])
def test_rescan_messages(reason, hint, product, spoken):
    assert speech.rescan(reason, hint, product) == spoken


def test_gave_up_messages():
    assert speech.gave_up("strength_unread", BIOGESIC) == (
        "I can read Biogesic but not the strength. Please ask someone to check the label.")
    assert speech.gave_up("ingredients_incomplete", ParsedProduct("Alaxan FR")) == (
        "I can read Alaxan FR but not all of its ingredients. Please ask someone to check the label.")
    assert speech.gave_up("no_name") == (
        "I still can't read this label clearly. Please ask someone to check it for you.")


def test_fixed_messages():
    assert speech.MULTIPLE == "I can see more than one medicine. Please set the others aside and scan one at a time."
    assert speech.SERVICE_BUSY == ("The identification service is busy or unavailable right now. "
                                   "Please try again in a moment.")
