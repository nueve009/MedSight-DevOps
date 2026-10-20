"""
Every sentence the app speaks is built here, so the wording and order are the
same on every phone and covered by snapshot tests (tests/test_speech.py).
A change to any string here needs a reviewer's approval and a listen through
the phone's text-to-speech.

Answer order (development plan, section 5):
    name and strength
    -> "This is a prescription medicine." when it applies
    -> the tier line: reviewed, or the warning for the ingredient, web and
       general tiers (always BEFORE the description: a listener may stop early)
    -> the description
    -> the reading caveat, when OCR confidence was low
    -> the pharmacist advice

Tiers: "reviewed" (team-checked dataset), "ingredient" (team-checked
ingredient descriptions, brand not reviewed), "web" (allow-listed web
search) and "general" (the model's own general knowledge, the fallback).
"""
import re
from typing import Optional

from app.llm_parse import ParsedProduct

REVIEWED, INGREDIENT, WEB, GENERAL = "reviewed", "ingredient", "web", "general"

REVIEWED_LINE = "Our team has reviewed this information."
PRESCRIPTION_LINE = "This is a prescription medicine."
INGREDIENT_INTRO = "Our team hasn't reviewed this product yet. Based on its ingredients,"
WEB_WARNING = ("This medicine isn't in our reviewed list. The following information comes from "
               "the web and hasn't been reviewed by our team.")
GENERAL_WARNING = ("Warning: this medicine isn't in our reviewed list, so this is general "
                   "information only and may be inaccurate.")
READING_CAVEAT = "I'm not fully sure of this reading."
PHARMACIST_ADVICE = "Please check the label or ask a pharmacist before taking it."

MULTIPLE = "I can see more than one medicine. Please set the others aside and scan one at a time."
SERVICE_BUSY = "The identification service is busy or unavailable right now. Please try again in a moment."

_RESCAN_OPENING = {
    "no_text": "I couldn't read any text.",
    "too_much_text": "There's a lot of text in view.",
    "no_name": "I read some text but couldn't find a medicine name.",
    "name_not_in_text": "I'm not sure I read the name correctly.",
    "strength_unread": "I found {label} but couldn't read the strength.",
    "ingredients_incomplete": "I found {label} but couldn't read all of its ingredients.",
}
_HINT_SENTENCE = {
    "move_closer": "Move the phone a little closer to the label and try again.",
    "more_light": "Find more light and try again.",
    "turn_box": "Turn the box and scan the side with the ingredients.",
    None: "Please try again.",
}
_GAVE_UP_MISSING = {
    "strength_unread": "the strength",
    "ingredients_incomplete": "all of its ingredients",
}
GAVE_UP_UNREAD = "I still can't read this label clearly. Please ask someone to check it for you."

_UNITS = {"mcg": "micrograms", "µg": "micrograms", "mg": "milligrams", "g": "grams", "ml": "milliliters",
          "l": "liters", "iu": "international units", "%": "percent"}
_UNIT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(mcg|µg|mg|ml|iu|g|l|%)(?![a-z])", re.IGNORECASE)


def say_strength(strength: str) -> str:
    """'500mg' -> '500 milligrams'; '5 mg/5 mL' -> '5 milligrams per 5 milliliters'."""
    def unit(m: re.Match) -> str:
        number, word = m.group(1), _UNITS[m.group(2).lower()]
        if number == "1" and word.endswith("s"):
            word = word[:-1]
        return f"{number} {word}"
    spoken = _UNIT_RE.sub(unit, strength)
    return " ".join(re.sub(r"\s*/\s*", " per ", spoken).split())


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _lower_first(text: str) -> str:
    """'Used to relieve...' -> 'used to relieve...', so it can follow 'Based on its
    ingredients,'. An acronym such as 'MIMS' (second letter uppercase) is left alone."""
    return text[0].lower() + text[1:] if len(text) > 1 and text[1].islower() else text


def name_line(product: ParsedProduct) -> str:
    """'Biogesic, Paracetamol 500 milligrams.' Names keep their printed case,
    so text-to-speech reads salts like 'HCl' as letters."""
    ingredients = [f"{g} {say_strength(s)}".strip()
                   for g, s in zip(product.generics, product.strengths, strict=False)]
    ingredients += list(product.generics[len(product.strengths):])
    if product.brand and ingredients:
        return f"{product.brand}, {_join(ingredients)}."
    return _sentence(product.brand or _join(ingredients))


def answer(product: ParsedProduct, tier: str, description: str, prescription: bool = False,
           caveat: bool = False, source_site: Optional[str] = None) -> str:
    parts = [name_line(product)]
    if prescription:
        parts.append(PRESCRIPTION_LINE)
    description = _sentence(description)
    if tier == REVIEWED:
        parts += [REVIEWED_LINE, description]
    elif tier == INGREDIENT:
        parts.append(f"{INGREDIENT_INTRO} {_lower_first(description)}")
    elif tier == WEB:
        parts += [WEB_WARNING, f"According to {source_site}, {_lower_first(description)}" if source_site
                  else description]
    else:
        parts += [GENERAL_WARNING, description]
    if caveat:
        parts.append(READING_CAVEAT)
    parts.append(PHARMACIST_ADVICE)
    return " ".join(parts)


def rescan(reason: str, hint: Optional[str], product: Optional[ParsedProduct] = None) -> str:
    label = product.label if product and product.label else "the medicine"
    return f"{_RESCAN_OPENING[reason].format(label=label)} {_HINT_SENTENCE[hint]}"


def gave_up(reason: Optional[str], product: Optional[ParsedProduct] = None) -> str:
    """Say only what was read clearly."""
    missing = _GAVE_UP_MISSING.get(reason)
    if missing and product and product.label:
        return f"I can read {product.label} but not {missing}. Please ask someone to check the label."
    return GAVE_UP_UNREAD
