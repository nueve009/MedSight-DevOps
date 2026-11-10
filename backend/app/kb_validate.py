"""
The rules every row of the reviewed dataset must follow, wherever the rows
come from: the CSV files in kb/ (scripts/kb_build.py) or the kb_* tables in
Supabase (app/kb_source.py, which drops any approved row that breaks them).
migrations/0005_kb_dataset.sql checks the same rules when a row is approved.

Rows are dicts of strings with the CSV column names. Each comes with a
`where` label ("products.csv:3" or "kb_products:biogesic-500-tab") that
starts every error about it.
"""
import re
from datetime import date

from app.utils import contains_dosing

PRODUCT_FIELDS = ["product_id", "brand_name", "aliases", "generic_names", "strengths", "dosage_form",
                  "classification", "fda_reg_no", "reg_expiry", "manufacturer", "portal_checked_on",
                  "entered_by", "checked_by", "spoken_description", "description_checked_by"]
INGREDIENT_FIELDS = ["generic_name", "synonyms", "pnf_listed", "pnf_forms", "pnf_page",
                     "spoken_description", "source_url", "entered_by", "checked_by"]
PASSAGE_FIELDS = ["passage_id", "product_id", "section", "text", "source_type", "source_ref",
                  "entered_by", "checked_by"]

OPTIONAL_PRODUCT_FIELDS = {"aliases", "manufacturer"}
# An ingredient may be listed only for its names (to match synonyms). Its
# spoken_description and source_url are then empty; once either is filled,
# all four description fields are required (see validate_rows()).
INGREDIENT_DESCRIPTION_FIELDS = {"spoken_description", "source_url", "entered_by", "checked_by"}
OPTIONAL_INGREDIENT_FIELDS = {"synonyms", "pnf_forms", "pnf_page"} | INGREDIENT_DESCRIPTION_FIELDS
# Filled in by the checker when they approve, so a draft waiting for review may leave them empty.
CHECKER_FIELDS = {"checked_by", "description_checked_by"}

CLASSIFICATIONS = {"OTC", "Rx", "HR"}  # over-the-counter, prescription, household remedy
SECTIONS = {"indications", "warnings"}
# tfd = a TheFilipinoDoctor drug page (team decision 2026-10-26); source_ref is its URL.
SOURCE_TYPES = {"box", "insert", "pnf", "tfd"}
TFD_URL_RE = re.compile(r"^https://(www\.)?thefilipinodoctor\.com/\S+$")
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REG_NO_RE = re.compile(r"^[A-Z]{2,6}-[A-Z0-9-]+$")  # e.g. DR-XY12345; warning only
MAX_SPOKEN_CHARS = 300

Rows = list[tuple[str, dict]]


def split_list(value: str) -> list[str]:
    """'Paracetamol | Phenylephrine' -> ['Paracetamol', 'Phenylephrine']"""
    return [part.strip() for part in (value or "").split("|") if part.strip()]


class Validator:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def warn(self, where: str, message: str) -> None:
        self.warnings.append(f"{where}: {message}")

    def require(self, where: str, row: dict, optional: set[str]) -> None:
        for field, value in row.items():
            if field not in optional and not value:
                self.error(where, f"'{field}' is empty")

    def check_date(self, where: str, field: str, value: str) -> date | None:
        try:
            return date.fromisoformat(value)
        except ValueError:
            if value:
                self.error(where, f"'{field}' must be a date like 2027-03-31, got '{value}'")
            return None

    def check_two_people(self, where: str, row: dict) -> None:
        if row["entered_by"] and row["entered_by"].lower() == row["checked_by"].lower():
            self.error(where, "checked_by must be a different person from entered_by")

    def check_spoken(self, where: str, text: str) -> None:
        """What a user will hear as reviewed: short, and never a dose or schedule."""
        if len(text) > MAX_SPOKEN_CHARS:
            self.error(where, f"'spoken_description' is {len(text)} characters; keep it under {MAX_SPOKEN_CHARS}")
        if contains_dosing(text):
            self.error(where, "'spoken_description' looks like it gives a dose or schedule; "
                              "descriptions say what a medicine is for, never how much to take")


def validate_rows(products: Rows, ingredients: Rows, passages: Rows, v: Validator | None = None,
                  allow_empty: bool = False, for_review: bool = False) -> Validator:
    """Check the rows; errors and warnings are added to v (a new Validator if None).

    for_review: the rows are drafts going to Supabase for a teammate to check,
    so the checker's columns may still be empty (they're filled when approving).
    """
    v = v or Validator()
    checker_optional = CHECKER_FIELDS if for_review else set()
    description_fields = INGREDIENT_DESCRIPTION_FIELDS - checker_optional

    # --- ingredients ---
    known_generics: dict[str, str] = {}  # lowercased name or synonym -> generic_name
    for where, row in ingredients:
        v.require(where, row, OPTIONAL_INGREDIENT_FIELDS)
        if for_review and not row["entered_by"]:
            v.error(where, "'entered_by' is empty; Supabase needs who entered every row")
        if row["pnf_listed"] not in ("yes", "no"):
            v.error(where, "'pnf_listed' must be 'yes' or 'no'")
        if row["pnf_listed"] == "yes" and not row["pnf_page"]:
            v.warn(where, "listed in the PNF but 'pnf_page' is empty")
        for name in [row["generic_name"], *split_list(row["synonyms"])]:
            key = name.lower()
            if key in known_generics:
                v.error(where, f"'{name}' already appears in ingredients.csv")
            known_generics[key] = row["generic_name"]

        # Names (entered_by, checked_by) alone don't make it described: in Supabase every row has them.
        if row["spoken_description"] or row["source_url"]:
            for field in sorted(description_fields):
                if not row[field]:
                    v.error(where, f"'{field}' is empty; a described ingredient needs "
                                   "spoken_description, source_url, entered_by and checked_by")
            v.check_two_people(where, row)
            if row["source_url"] and not row["source_url"].startswith("https://"):
                v.error(where, "'source_url' must be an https:// link to the page the description came from")
            v.check_spoken(where, row["spoken_description"])

    # --- products ---
    product_where: dict[str, str] = {}
    reg_nos: set[str] = set()
    for where, row in products:
        v.require(where, row, OPTIONAL_PRODUCT_FIELDS | checker_optional)
        pid = row["product_id"]
        if pid and not ID_RE.match(pid):
            v.error(where, f"product_id '{pid}' must be lowercase letters, digits and dashes")
        if pid in product_where:
            v.error(where, f"duplicate product_id '{pid}'")
        product_where.setdefault(pid, where)

        if row["classification"] and row["classification"] not in CLASSIFICATIONS:
            v.error(where, f"'classification' must be one of {sorted(CLASSIFICATIONS)}")
        reg_no = row["fda_reg_no"]
        if reg_no in reg_nos:
            v.error(where, f"duplicate fda_reg_no '{reg_no}'")
        reg_nos.add(reg_no)
        if reg_no and not REG_NO_RE.match(reg_no):
            v.warn(where, f"fda_reg_no '{reg_no}' doesn't look like 'DR-XY12345'; double-check it against the box")

        generics = split_list(row["generic_names"])
        strengths = split_list(row["strengths"])
        if len(generics) != len(strengths):
            v.error(where, f"{len(generics)} generic name(s) but {len(strengths)} strength(s); "
                           "list them in the same order, separated by '|'")
        for generic in generics:
            if generic.lower() not in known_generics:
                v.error(where, f"generic '{generic}' is not in ingredients.csv")

        expiry = v.check_date(where, "reg_expiry", row["reg_expiry"])
        if expiry and expiry < date.today():
            v.error(where, f"FDA registration expired on {expiry}; re-check the portal")
        v.check_date(where, "portal_checked_on", row["portal_checked_on"])
        v.check_two_people(where, row)
        v.check_spoken(where, row["spoken_description"])

    # --- passages ---
    passage_ids: set[str] = set()
    products_with_indications: set[str] = set()
    for where, row in passages:
        v.require(where, row, checker_optional)
        if row["passage_id"] in passage_ids:
            v.error(where, f"duplicate passage_id '{row['passage_id']}'")
        passage_ids.add(row["passage_id"])
        if row["product_id"] and row["product_id"] not in product_where:
            v.error(where, f"product_id '{row['product_id']}' is not in products.csv")
        if row["section"] and row["section"] not in SECTIONS:
            v.error(where, f"'section' must be one of {sorted(SECTIONS)}")
        if row["source_type"] and row["source_type"] not in SOURCE_TYPES:
            v.error(where, f"'source_type' must be one of {sorted(SOURCE_TYPES)}")
        if row["source_type"] == "tfd" and row["source_ref"] and not TFD_URL_RE.match(row["source_ref"]):
            v.error(where, "a 'tfd' passage needs the TheFilipinoDoctor page URL in 'source_ref'")
        if row["section"] == "indications":
            products_with_indications.add(row["product_id"])
        v.check_two_people(where, row)

    for pid, where in product_where.items():
        if pid and pid not in products_with_indications:
            v.error(where, f"product '{pid}' has no 'indications' passage")

    if not products:
        (v.warn if allow_empty else v.error)("products.csv", "no products yet")
    return v


def servable(products: Rows, ingredients: Rows, passages: Rows) -> tuple[dict, list[str]]:
    """Drop every row that breaks a rule, and every row that then depends on a
    dropped one (a product whose ingredient or indications passage was
    dropped, a passage whose product was dropped), until the rest is valid.

    Returns ({"products": [...], "ingredients": [...], "passages": [...]}, the errors that dropped rows).
    """
    tables = {"products": list(products), "ingredients": list(ingredients), "passages": list(passages)}
    dropped: list[str] = []
    for _ in range(sum(len(t) for t in tables.values()) + 1):
        v = validate_rows(tables["products"], tables["ingredients"], tables["passages"], allow_empty=True)
        bad = {e.split(": ", 1)[0] for e in v.errors}
        if not bad:
            break
        dropped += v.errors
        kept = {name: [(w, r) for w, r in rows if w not in bad] for name, rows in tables.items()}
        if sum(map(len, kept.values())) == sum(map(len, tables.values())):
            break   # an error not tied to one row: nothing more to drop
        tables = kept
    return {name: [r for _, r in rows] for name, rows in tables.items()}, dropped
