"""
Match a parsed label against the reviewed dataset (data/kb.sqlite, built
from kb/ by scripts/kb_build.py). The dataset is small, so it's read into
memory once at startup.

KnowledgeBase.match(product) returns one of:

    reviewed    the brand (or an alias) AND every ingredient AND every
                strength agree with one product. Brand spelling may be fuzzy
                (an OCR slip such as "Bi0gesic") only when ingredients and
                strengths all agree. Spoken with the team-checked description.
    partial     the brand is exactly a listed brand (or alias), but an
                ingredient or strength is missing or different. Never
                answered: the pipeline asks for a rescan of the side of the box.
    ingredient  not a listed product, but every ingredient read has a
                team-checked description. Spoken behind "Our team hasn't
                reviewed this product yet."
    none        none of the above: the general tier.

A near miss is never a match. A fuzzy brand with different contents is
treated as an unlisted product, so the app never names a listed brand that
isn't the one in the user's hand.
"""
import difflib
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.llm_parse import ParsedProduct
from app.scan_rules import INGREDIENTS_INCOMPLETE, STRENGTH_UNREAD, partial_match_reason
from app.utils import normalize_strength

log = logging.getLogger(__name__)

# Brand spellings at least this similar may still give a reviewed answer,
# but only when every ingredient and strength agrees too.
FUZZY_BRAND_RATIO = 0.85

REVIEWED, PARTIAL, INGREDIENT, NONE = "reviewed", "partial", "ingredient", "none"


def name_key(name: str) -> str:
    """'Hyoscine-N-butylbromide' and 'hyoscine n butylbromide' -> 'hyoscine n butylbromide'."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split())


def brand_key(brand: str) -> str:
    """'Kremil-S', 'KREMIL S' and 'kremils' -> 'kremils'."""
    return re.sub(r"[^a-z0-9]+", "", (brand or "").lower())


def _split(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split("|") if part.strip())


@dataclass(frozen=True)
class KBProduct:
    product_id: str
    brand: str
    aliases: tuple[str, ...]
    generics: tuple[str, ...]        # canonical ingredient names, in label order
    strengths: tuple[str, ...]       # same order as generics
    dosage_form: str
    rx: bool                         # classification == "Rx"
    spoken_description: str
    sources: tuple[str, ...] = ()    # https links to the source pages

    @property
    def brand_keys(self) -> set[str]:
        return {brand_key(b) for b in (self.brand, *self.aliases) if b}

    def as_parsed(self) -> ParsedProduct:
        """The dataset's own spelling, for speaking the name and strength."""
        return ParsedProduct(self.brand, self.generics, self.strengths)


@dataclass(frozen=True)
class KBIngredient:
    name: str
    spoken_description: str          # "" when not described (names only)
    source_url: str


@dataclass(frozen=True)
class Match:
    kind: str                                    # reviewed | partial | ingredient | none
    product: Optional[KBProduct] = None          # reviewed, and the brand that matched for partial
    reason: Optional[str] = None                 # partial: scan_rules rescan reason
    description: Optional[str] = None            # reviewed and ingredient
    sources: tuple[str, ...] = ()


@dataclass
class KnowledgeBase:
    products: list[KBProduct] = field(default_factory=list)
    ingredients: dict[str, KBIngredient] = field(default_factory=dict)   # name_key(canonical) -> row
    synonyms: dict[str, str] = field(default_factory=dict)               # name_key(any name) -> canonical
    version: Optional[str] = None

    # --- loading ---

    @classmethod
    def load(cls, db_path: Path) -> "KnowledgeBase":
        """Reads kb.sqlite. A missing file gives an empty dataset (every scan
        falls to the general tier) and a logged warning, never a crash."""
        if not db_path.exists():
            log.warning("kb.sqlite not found; reviewed answers are off", extra={"fields": {"path": str(db_path)}})
            return cls()
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            ingredients = [dict(r) for r in conn.execute("SELECT * FROM ingredients")]
            products = [dict(r) for r in conn.execute("SELECT * FROM products")]
            passages = [dict(r) for r in conn.execute(
                "SELECT product_id, source_ref FROM passages WHERE section = 'indications'")]
            version = conn.execute("SELECT value FROM meta WHERE key = 'kb_version'").fetchone()
        finally:
            conn.close()
        return cls.from_rows(products, ingredients, passages, version[0] if version else None)

    @classmethod
    def from_rows(cls, products: list[dict], ingredients: list[dict], passages: list[dict],
                  version: Optional[str] = None) -> "KnowledgeBase":
        kb = cls(version=version)
        for row in ingredients:
            canonical = row["generic_name"]
            for name in (canonical, *_split(row.get("synonyms", ""))):
                kb.synonyms[name_key(name)] = canonical
            kb.ingredients[name_key(canonical)] = KBIngredient(
                canonical, row.get("spoken_description", ""), row.get("source_url", ""))

        sources: dict[str, list[str]] = {}
        for row in passages:
            if row["source_ref"].startswith("https://") and row["source_ref"] not in sources.get(row["product_id"], []):
                sources.setdefault(row["product_id"], []).append(row["source_ref"])

        for row in products:
            kb.products.append(KBProduct(
                product_id=row["product_id"],
                brand=row["brand_name"],
                aliases=_split(row.get("aliases", "")),
                generics=tuple(kb.canonical(g) for g in _split(row["generic_names"])),
                strengths=_split(row["strengths"]),
                dosage_form=row.get("dosage_form", ""),
                rx=row.get("classification") == "Rx",
                spoken_description=row["spoken_description"],
                sources=tuple(sources.get(row["product_id"], [])),
            ))
        return kb

    # --- matching ---

    def canonical(self, name: str) -> str:
        """The dataset's name for an ingredient, or the name itself if it isn't listed."""
        return self.synonyms.get(name_key(name), name)

    def _contents(self, generics, strengths) -> Optional[dict[str, str]]:
        """{ingredient key: strength}, or None if an ingredient repeats or a strength is missing."""
        contents = {name_key(self.canonical(g)): normalize_strength(s)
                    for g, s in zip(generics, strengths, strict=False)}
        if len(contents) != len(generics) or len(strengths) != len(generics) or not all(contents.values()):
            return None
        return contents

    def _same_contents(self, p: ParsedProduct, k: KBProduct) -> bool:
        read = self._contents(p.generics, p.strengths)
        return read is not None and read == self._contents(k.generics, k.strengths)

    def _partial_reason(self, p: ParsedProduct, candidates: list[KBProduct]) -> str:
        """Why a listed brand didn't fully match. 'Strength' wins when any
        candidate's ingredients were all found, so the user is told to look
        for the strength, the more specific fix."""
        if not p.generics:
            return STRENGTH_UNREAD

        def same(a: str, b: str) -> bool:
            return name_key(self.canonical(a)) == name_key(self.canonical(b))

        reasons = {partial_match_reason(p, list(k.generics), list(k.strengths), same) for k in candidates}
        return STRENGTH_UNREAD if STRENGTH_UNREAD in reasons else INGREDIENTS_INCOMPLETE

    def match(self, p: ParsedProduct) -> Match:
        if p.brand and self.products:
            key = brand_key(p.brand)
            exact = [k for k in self.products if key in k.brand_keys]
            fuzzy = [k for k in self.products if k not in exact and any(
                difflib.SequenceMatcher(None, key, bk).ratio() >= FUZZY_BRAND_RATIO for bk in k.brand_keys)]

            full = [k for k in exact + fuzzy if self._same_contents(p, k)]
            if full:
                if len({(k.spoken_description, k.rx) for k in full}) == 1:
                    k = full[0]
                    return Match(REVIEWED, k, description=k.spoken_description, sources=k.sources)
                # Same brand, ingredients and strength but different descriptions
                # or Rx status (e.g. two forms): don't pick one.
                log.warning("ambiguous dataset match", extra={"fields": {
                    "product_ids": [k.product_id for k in full]}})
            elif exact:
                return Match(PARTIAL, exact[0], reason=self._partial_reason(p, exact))

        return self._ingredient_match(p)

    def brand_level(self, p: ParsedProduct) -> Optional[KBProduct]:
        """A product to describe at brand level when the strength can't be read, or None.

        Only for an exact brand (never fuzzy: a misread brand must not name a
        listed one) whose every approved product has the same ingredients,
        description and Rx status, so it doesn't matter which one this is.
        Whatever was read must agree with them, and a fully read strength
        doesn't qualify: that's a different product, not a missing strength.
        """
        if not p.brand or (p.generics and len(p.strengths) == len(p.generics) and all(p.strengths)):
            return None
        key = brand_key(p.brand)
        exact = [k for k in self.products if key in k.brand_keys]
        if not exact or not exact[0].spoken_description:
            return None

        def names(k: KBProduct) -> frozenset[str]:
            return frozenset(name_key(self.canonical(g)) for g in k.generics)

        if len({(names(k), k.spoken_description, k.rx) for k in exact}) != 1:
            return None
        listed: dict[str, set[str]] = {}
        for k in exact:
            for g, s in zip(k.generics, k.strengths, strict=False):
                listed.setdefault(name_key(self.canonical(g)), set()).add(normalize_strength(s))
        for i, generic in enumerate(p.generics):
            name = name_key(self.canonical(generic))
            strength = p.strengths[i] if i < len(p.strengths) else ""
            if name not in listed or (strength and normalize_strength(strength) not in listed[name]):
                return None
        return exact[0]

    def _ingredient_match(self, p: ParsedProduct) -> Match:
        rows = [self.ingredients.get(name_key(self.canonical(g))) for g in p.generics]
        if not rows or not all(r and r.spoken_description for r in rows):
            return Match(NONE)
        unique = list(dict.fromkeys(rows))
        description = " ".join(r.spoken_description.rstrip(".") + "." for r in unique)
        sources = tuple(dict.fromkeys(r.source_url for r in unique if r.source_url.startswith("https://")))
        return Match(INGREDIENT, description=description, sources=sources)
