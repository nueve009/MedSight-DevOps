"""
Validate the hand-curated medicine dataset in kb/ and build it into
data/kb.sqlite for retrieval.

The CSVs are the source of truth; every row is entered from a photographed
box or insert and an FDA portal lookup, then checked by a second teammate
(see kb/README.md). This script refuses to build if any row breaks the rules,
because a wrong row here would be spoken to a user as verified fact.

Usage:
    python -m scripts.kb_build           validate, then build data/kb.sqlite
    python -m scripts.kb_build --check   validate only (run after editing the CSVs)
"""
import csv
import hashlib
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

from app import config

KB_FILES = ("products.csv", "ingredients.csv", "passages.csv")

PRODUCT_FIELDS = ["product_id", "brand_name", "aliases", "generic_names", "strengths", "dosage_form",
                  "classification", "fda_reg_no", "reg_expiry", "manufacturer", "portal_checked_on",
                  "entered_by", "checked_by"]
INGREDIENT_FIELDS = ["generic_name", "synonyms", "pnf_listed", "pnf_forms", "pnf_page"]
PASSAGE_FIELDS = ["passage_id", "product_id", "section", "text", "source_type", "source_ref",
                  "entered_by", "checked_by"]

OPTIONAL_PRODUCT_FIELDS = {"aliases", "manufacturer"}
OPTIONAL_INGREDIENT_FIELDS = {"synonyms", "pnf_forms", "pnf_page"}

CLASSIFICATIONS = {"OTC", "Rx", "HR"}  # over-the-counter, prescription, household remedy
SECTIONS = {"indications", "warnings"}
SOURCE_TYPES = {"box", "insert", "pnf"}
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REG_NO_RE = re.compile(r"^[A-Z]{2,6}-[A-Z0-9-]+$")  # e.g. DR-XY12345; warning only


def split_list(value: str) -> list[str]:
    """'Paracetamol | Phenylephrine' -> ['Paracetamol', 'Phenylephrine']"""
    return [part.strip() for part in value.split("|") if part.strip()]


def kb_version(kb_dir: Path = config.KB_DIR) -> str:
    """Hash of the CSV contents; changes whenever the dataset changes."""
    digest = hashlib.sha256()
    for name in KB_FILES:
        digest.update((kb_dir / name).read_bytes())
    return digest.hexdigest()[:12]


class Validator:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def warn(self, where: str, message: str) -> None:
        self.warnings.append(f"{where}: {message}")

    def read(self, path: Path, expected_fields: list[str]) -> list[tuple[str, dict]]:
        """Returns [(where, row)]. utf-8-sig tolerates the BOM Excel adds."""
        if not path.exists():
            self.error(path.name, "file is missing")
            return []
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != expected_fields:
                self.error(path.name, f"header must be exactly: {','.join(expected_fields)}")
                return []
            rows = []
            for line_no, row in enumerate(reader, start=2):
                if None in row:
                    self.error(f"{path.name}:{line_no}", "too many columns (an unquoted comma in a value?)")
                    continue
                rows.append((f"{path.name}:{line_no}",
                             {k: (v or "").strip() for k, v in row.items()}))
            return rows

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


def validate(kb_dir: Path = config.KB_DIR) -> tuple[Validator, dict]:
    v = Validator()
    products = v.read(kb_dir / "products.csv", PRODUCT_FIELDS)
    ingredients = v.read(kb_dir / "ingredients.csv", INGREDIENT_FIELDS)
    passages = v.read(kb_dir / "passages.csv", PASSAGE_FIELDS)

    # --- ingredients ---
    known_generics: dict[str, str] = {}  # lowercased name or synonym -> generic_name
    for where, row in ingredients:
        v.require(where, row, OPTIONAL_INGREDIENT_FIELDS)
        if row["pnf_listed"] not in ("yes", "no"):
            v.error(where, "'pnf_listed' must be 'yes' or 'no'")
        if row["pnf_listed"] == "yes" and not row["pnf_page"]:
            v.warn(where, "listed in the PNF but 'pnf_page' is empty")
        for name in [row["generic_name"], *split_list(row["synonyms"])]:
            key = name.lower()
            if key in known_generics:
                v.error(where, f"'{name}' already appears in ingredients.csv")
            known_generics[key] = row["generic_name"]

    # --- products ---
    product_ids: set[str] = set()
    reg_nos: set[str] = set()
    for where, row in products:
        v.require(where, row, OPTIONAL_PRODUCT_FIELDS)
        pid = row["product_id"]
        if pid and not ID_RE.match(pid):
            v.error(where, f"product_id '{pid}' must be lowercase letters, digits and dashes")
        if pid in product_ids:
            v.error(where, f"duplicate product_id '{pid}'")
        product_ids.add(pid)

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

    # --- passages ---
    passage_ids: set[str] = set()
    products_with_indications: set[str] = set()
    for where, row in passages:
        v.require(where, row, set())
        if row["passage_id"] in passage_ids:
            v.error(where, f"duplicate passage_id '{row['passage_id']}'")
        passage_ids.add(row["passage_id"])
        if row["product_id"] and row["product_id"] not in product_ids:
            v.error(where, f"product_id '{row['product_id']}' is not in products.csv")
        if row["section"] and row["section"] not in SECTIONS:
            v.error(where, f"'section' must be one of {sorted(SECTIONS)}")
        if row["source_type"] and row["source_type"] not in SOURCE_TYPES:
            v.error(where, f"'source_type' must be one of {sorted(SOURCE_TYPES)}")
        if row["section"] == "indications":
            products_with_indications.add(row["product_id"])
        v.check_two_people(where, row)

    for pid in sorted(product_ids - products_with_indications):
        if pid:
            v.error("passages.csv", f"product '{pid}' has no 'indications' passage")

    if not products:
        v.error("products.csv", "no products yet")
    elif len(products) != 10:
        v.warn("products.csv", f"{len(products)} products (the prototype targets 10)")

    data = {
        "products": [row for _, row in products],
        "ingredients": [row for _, row in ingredients],
        "passages": [row for _, row in passages],
    }
    return v, data


def build(data: dict, version: str, db_path: Path = config.KB_DB_PATH) -> None:
    """Write a fresh kb.sqlite, then swap it in so readers never see a half-built file."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(".tmp")
    tmp_path.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp_path)
    try:
        conn.executescript(f"""
            CREATE TABLE products ({", ".join(f"{f} TEXT NOT NULL" for f in PRODUCT_FIELDS)},
                                   PRIMARY KEY (product_id));
            CREATE TABLE ingredients ({", ".join(f"{f} TEXT NOT NULL" for f in INGREDIENT_FIELDS)},
                                      PRIMARY KEY (generic_name));
            CREATE TABLE passages ({", ".join(f"{f} TEXT NOT NULL" for f in PASSAGE_FIELDS)},
                                   PRIMARY KEY (passage_id));
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            -- Lexical search fallback over names, aliases and passage text.
            CREATE VIRTUAL TABLE search USING fts5(product_id UNINDEXED, passage_id UNINDEXED, content);
        """)
        for table, fields in (("products", PRODUCT_FIELDS), ("ingredients", INGREDIENT_FIELDS),
                              ("passages", PASSAGE_FIELDS)):
            conn.executemany(
                f"INSERT INTO {table} VALUES ({', '.join('?' for _ in fields)})",
                [[row[f] for f in fields] for row in data[table]],
            )
        conn.executemany(
            "INSERT INTO search VALUES (?, '', ?)",
            [(p["product_id"], " ".join([p["brand_name"], p["aliases"].replace("|", " "),
                                         p["generic_names"].replace("|", " ")]))
             for p in data["products"]],
        )
        conn.executemany(
            "INSERT INTO search VALUES (?, ?, ?)",
            [(p["product_id"], p["passage_id"], p["text"]) for p in data["passages"]],
        )
        conn.execute("INSERT INTO meta VALUES ('kb_version', ?)", (version,))
        conn.execute("INSERT INTO meta VALUES ('built_on', ?)", (date.today().isoformat(),))
        conn.commit()
    finally:
        conn.close()
    tmp_path.replace(db_path)


def main() -> None:
    check_only = "--check" in sys.argv[1:]
    v, data = validate()

    for w in v.warnings:
        print(f"warning  {w}")
    for e in v.errors:
        print(f"ERROR    {e}")

    counts = ", ".join(f"{len(rows)} {name}" for name, rows in data.items())
    if v.errors:
        print(f"\n{len(v.errors)} error(s) in kb/ ({counts}). Fix them and run again.")
        sys.exit(1)

    print(f"kb/ is valid: {counts}.")
    if not check_only:
        version = kb_version()
        build(data, version)
        print(f"Built {config.KB_DB_PATH} (kb_version {version}).")


if __name__ == "__main__":
    main()
