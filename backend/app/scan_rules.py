"""
Scan rules: decide from checks the code can make (never the model's own
confidence score) whether to answer, ask for a rescan, or give up.
docs/project-status-and-decisions.md, section 3.

Every function here is pure: no LLM, network, database or clock. decide()
combines them in this order:

1. Hard rescan: no letters in the text, too much text, no product found, a
   parsed name that isn't in the OCR text, or strengths that can't be paired
   with the ingredients.
2. Several medicines in view -> "multiple".
3. After MAX_SCAN_ATTEMPTS tries, a rescan becomes "gave_up".
4. Otherwise answer: "ok", or "ok_caveat" when the phone's OCR confidence is low.

partial_match_reason() is the rule for a product that matches the dataset by
brand but not fully by ingredients or strength (used once retrieval exists).
"""
import difflib
import re
from dataclasses import dataclass
from typing import Optional

from app import config
from app.llm_parse import ParsedProduct
from app.utils import clean_text, normalize_strength

# Rescan reasons (API v2 "rescan_reason").
NO_TEXT = "no_text"
TOO_MUCH_TEXT = "too_much_text"
NO_NAME = "no_name"
NAME_NOT_IN_TEXT = "name_not_in_text"
STRENGTH_UNREAD = "strength_unread"
INGREDIENTS_INCOMPLETE = "ingredients_incomplete"

# Hints (API v2 "hint").
MOVE_CLOSER = "move_closer"
MORE_LIGHT = "more_light"
TURN_BOX = "turn_box"

# A second brand whose text block is less than this fraction of the height
# of the largest brand block is treated as small print ("also available:
# ..."), not a second box in view. A starting value: tune it on labeled scans.
SMALL_PRINT_RATIO = 0.4
# Brand spellings at least this similar are the same brand ("BIOGESIC" vs "Biogesic", "Bi0gesic").
SAME_BRAND_RATIO = 0.85


@dataclass(frozen=True)
class Block:
    """One OCR text block from the phone. box = (left, top, right, bottom) as fractions of the image."""
    text: str
    confidence: Optional[float] = None
    box: Optional[tuple[float, float, float, float]] = None


@dataclass(frozen=True)
class CaptureHints:
    blurry: bool = False
    dark: bool = False
    text_small: bool = False


@dataclass(frozen=True)
class Decision:
    status: str                                  # ok | ok_caveat | rescan | multiple | gave_up
    reason: Optional[str] = None                 # rescan reason, also kept for gave_up
    hint: Optional[str] = None
    product: Optional[ParsedProduct] = None      # the product to answer about (or what was read, for gave_up)


# --- Input checks (before any model call) ---

def input_reason(text: str) -> Optional[str]:
    if not re.search(r"[^\W\d_]", text or ""):
        return NO_TEXT          # empty, or only digits and symbols
    if len(text) > config.MAX_PARSE_CHARS:
        return TOO_MUCH_TEXT
    return None


# --- Is what the model returned really in the OCR text? ---

def appears_in_text(value: str, raw_text: str) -> bool:
    """True if the value, or any word of 4+ letters in it, appears (fuzzily) in the OCR text."""
    value_lower = clean_text(value)
    if not value_lower:
        return False
    raw_lower = clean_text(raw_text)
    if value_lower in raw_lower:
        return True
    ocr_words = re.findall(r"[a-z0-9]+", raw_lower)
    return any(len(token) >= 4 and difflib.get_close_matches(token, ocr_words, n=1, cutoff=0.8)
               for token in re.findall(r"[a-z0-9]+", value_lower))


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", text or "")}


def strength_in_text(strength: str, raw_text: str) -> bool:
    """Every number in the strength must be printed in the OCR text, so a made-up dose is caught."""
    numbers = _numbers(strength)
    return bool(numbers) and numbers <= _numbers(raw_text)


def grounding_reason(products: list[ParsedProduct], raw_text: str) -> Optional[str]:
    for p in products:
        names = [n for n in (p.brand, *p.generics) if n]
        if not all(appears_in_text(n, raw_text) for n in names):
            return NAME_NOT_IN_TEXT
        if any(s and not strength_in_text(s, raw_text) for s in p.strengths):
            return STRENGTH_UNREAD
    return None


# --- Several medicines in view ---

def same_brand(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    a, b = clean_text(a), clean_text(b)
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= SAME_BRAND_RATIO


def _strength_by_ingredient(p: ParsedProduct) -> dict[str, str]:
    return {clean_text(g): normalize_strength(s) for g, s in zip(p.generics, p.strengths, strict=False)}


def _same_product(a: ParsedProduct, b: ParsedProduct) -> bool:
    if a.brand and b.brand:
        if not same_brand(a.brand, b.brand):
            return False
        # Same brand: only a different strength makes it a second product.
        sa = {normalize_strength(s) for s in a.strengths if s}
        sb = {normalize_strength(s) for s in b.strengths if s}
        return not sa or not sb or sa == sb
    # At least one has no brand. It's the same product only if its ingredients
    # repeat the other's, with no strength that disagrees. When either side
    # has no ingredients there's nothing to compare, so treat them as two.
    sa, sb = _strength_by_ingredient(a), _strength_by_ingredient(b)
    if not sa or not sb or not (sa.keys() <= sb.keys() or sb.keys() <= sa.keys()):
        return False
    return all(not sa[g] or not sb[g] or sa[g] == sb[g] for g in sa.keys() & sb.keys())


def _block_has_brand(brand: str, block_text: str) -> bool:
    """The whole brand, not just one word of it: 'Biogesic for Kids' isn't in a 'BIOGESIC' block."""
    brand, text = clean_text(brand), clean_text(block_text)
    return brand in text or difflib.SequenceMatcher(None, brand, text).ratio() >= SAME_BRAND_RATIO


def _brand_block_height(brand: str, blocks: list[Block]) -> Optional[float]:
    heights = [b.box[3] - b.box[1] for b in blocks if b.box and _block_has_brand(brand, b.text)]
    return max(heights) if heights else None


def drop_small_print(products: list[ParsedProduct], blocks: list[Block]) -> list[ParsedProduct]:
    """Without positions this changes nothing. With them, a brand printed far
    smaller than the main brand is a mention, not a second box in view."""
    heights = {p.brand: _brand_block_height(p.brand, blocks) for p in products if p.brand}
    known = [h for h in heights.values() if h]
    if len(known) < 2:
        return products
    tallest = max(known)
    return [p for p in products
            if not p.brand or heights[p.brand] is None or heights[p.brand] >= SMALL_PRINT_RATIO * tallest]


def distinct_products(products: list[ParsedProduct], blocks: list[Block]) -> list[ParsedProduct]:
    """Merge repeats of the same product; branded entries are kept over unbranded ones."""
    kept: list[ParsedProduct] = []
    for p in sorted(drop_small_print(products, blocks), key=lambda p: (p.brand is None, -len(p.generics))):
        if not any(_same_product(p, k) for k in kept):
            kept.append(p)
    return kept


# --- Strength and partial-match rules ---

def strength_reason(p: ParsedProduct) -> Optional[str]:
    """Each ingredient needs exactly one readable strength."""
    if p.generics and (len(p.strengths) != len(p.generics) or not all(p.strengths)):
        return STRENGTH_UNREAD
    return None


def partial_match_reason(p: ParsedProduct, dataset_generics: list[str], dataset_strengths: list[str],
                         same_ingredient) -> Optional[str]:
    """For a product whose brand matched the dataset. same_ingredient(a, b)
    says whether two ingredient names are the same (it knows the synonyms).

    - fewer ingredients read than the dataset lists -> ingredients_incomplete
    - an ingredient's strength missing or different -> strength_unread
    """
    read = list(zip(p.generics, p.strengths, strict=False))
    for generic, strength in zip(dataset_generics, dataset_strengths, strict=True):
        found = [s for g, s in read if same_ingredient(g, generic)]
        if not found:
            return INGREDIENTS_INCOMPLETE
        if normalize_strength(found[0]) != normalize_strength(strength):
            return STRENGTH_UNREAD
    return None


# --- Hints, retry limit, caveat ---

def hint_for(reason: Optional[str], hints: CaptureHints) -> Optional[str]:
    if reason in (STRENGTH_UNREAD, INGREDIENTS_INCOMPLETE):
        return TURN_BOX
    if hints.dark:
        return MORE_LIGHT
    if hints.text_small or hints.blurry or reason == TOO_MUCH_TEXT:
        return MOVE_CLOSER
    return None


def should_give_up(attempt: int) -> bool:
    return attempt >= config.MAX_SCAN_ATTEMPTS


def needs_caveat(ocr_confidence: Optional[float]) -> bool:
    """None means the phone doesn't report a confidence: skipped, not treated as 0."""
    return ocr_confidence is not None and ocr_confidence < config.OCR_CAVEAT_THRESHOLD


# --- All together ---

def rescan(reason: str, hints: CaptureHints, attempt: int, product: Optional[ParsedProduct] = None) -> Decision:
    status = "gave_up" if should_give_up(attempt) else "rescan"
    return Decision(status, reason, None if status == "gave_up" else hint_for(reason, hints), product)


def decide(products: list[ParsedProduct], raw_text: str, blocks: list[Block], attempt: int,
           hints: CaptureHints, ocr_confidence: Optional[float]) -> Decision:
    """The decision for a parsed label (input checks already passed)."""
    if not products:
        return rescan(NO_NAME, hints, attempt)
    reason = grounding_reason(products, raw_text)
    if reason:
        return rescan(reason, hints, attempt)

    in_view = distinct_products(products, blocks)
    if len(in_view) > 1:
        return Decision("multiple")

    product = in_view[0]
    reason = strength_reason(product)
    if reason:
        return rescan(reason, hints, attempt, product)
    return answer(product, ocr_confidence)


def answer(product: ParsedProduct, ocr_confidence: Optional[float]) -> Decision:
    return Decision("ok_caveat" if needs_caveat(ocr_confidence) else "ok", product=product)
