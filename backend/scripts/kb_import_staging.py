"""
Turn the drafts in kb/staging/ into pending rows for the review console.

Reads tfd_products_draft.csv, tfd_ingredients_draft.csv, tfd_passages_draft.csv
and descriptions_review.csv, maps them to the dataset's columns and writes
products.csv, ingredients.csv and passages.csv to --out for inspection. With
--push it also adds them to Supabase as unapproved rows. A second teammate
checks each one in the console.

Usage (from backend/):
    python -m scripts.kb_import_staging --by "Ana"            write the CSVs only
    python -m scripts.kb_import_staging --by "Ana" --push     write them and add the rows to Supabase

--by is who takes responsibility for entering these rows (entered_by).

What is left empty on purpose:
    classification, fda_reg_no, reg_expiry, manufacturer, portal_checked_on
        come from the FDA portal, picked from the candidates in the console
    pnf_listed, pnf_forms, pnf_page
        aren't in the drafts. kb_ingredients can't hold "unknown", so a new
        row gets the column's default ('no') until a reviewer sets it
    checked_by, description_checked_by
        the checker fills them

Nothing is invented. A row that can't be mapped is skipped and listed. Running
it again never touches a row that was approved, sent back or edited in the
console. An edit made in the Supabase Table Editor records no editor, so a
still-pending row edited there can be updated by a later run.
"""
import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app import config
from app.kb_validate import (
    ID_RE,
    INGREDIENT_FIELDS,
    MAX_SPOKEN_CHARS,
    PASSAGE_FIELDS,
    PRODUCT_FIELDS,
    SECTIONS,
    TFD_URL_RE,
    Validator,
    split_list,
    validate_rows,
)
from app.utils import contains_dosing
from scripts import kb_build

PRODUCTS_FILE = "tfd_products_draft.csv"
INGREDIENTS_FILE = "tfd_ingredients_draft.csv"
PASSAGES_FILE = "tfd_passages_draft.csv"
DESCRIPTIONS_FILE = "descriptions_review.csv"

REQUIRED_COLUMNS = {
    PRODUCTS_FILE: ["product_id", "brand_name", "aliases", "generic_names", "strengths", "dosage_form"],
    INGREDIENTS_FILE: ["generic_name", "synonyms"],
    PASSAGES_FILE: ["passage_id", "product_id", "section", "text", "source_ref"],
    DESCRIPTIONS_FILE: ["kind", "id", "draft", "flags", "source_url", "approved_description"],
}


@dataclass
class Report:
    skipped: list[str] = field(default_factory=list)
    derived_ids: list[str] = field(default_factory=list)
    descriptions_empty: list[str] = field(default_factory=list)
    descriptions_flagged: list[str] = field(default_factory=list)
    several_sources: list[str] = field(default_factory=list)
    dose_like_passages: list[str] = field(default_factory=list)


def read_staging(staging_dir: Path, name: str) -> list[dict]:
    """The rows of one staging file, with values stripped. A damaged file stops the import."""
    path = staging_dir / name
    if not path.exists():
        sys.exit(f"{path} is missing.")
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        absent = [c for c in REQUIRED_COLUMNS[name] if c not in (reader.fieldnames or [])]
        if absent:
            sys.exit(f"{path} has no column: {', '.join(absent)}")
        rows = []
        for line_no, row in enumerate(reader, start=2):
            if None in row:
                sys.exit(f"{path}:{line_no}: too many columns (an unquoted comma in a value?)")
            rows.append({k: (v or "").strip() for k, v in row.items()})
        return rows


def derive_id(brand: str, strengths: list[str], form: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{brand} {' '.join(strengths)} {form}".lower()).strip("-")


def usable_description(sheet_row: dict | None) -> tuple[str, str]:
    """(text, why it can't be used). A sentence a person wrote wins over the LLM draft."""
    if sheet_row is None:
        return "", f"no row in {DESCRIPTIONS_FILE}"
    text = sheet_row["approved_description"] or sheet_row["draft"]
    if not text:
        return "", sheet_row["flags"] or "no draft"
    if len(text) > MAX_SPOKEN_CHARS:
        return "", f"{len(text)} characters, over {MAX_SPOKEN_CHARS}"
    if contains_dosing(text):
        return "", "looks like it gives a dose or schedule"
    return text, ""


def note_description(report: Report, label: str, sheet_row: dict | None, why: str) -> None:
    if why:
        report.descriptions_empty.append(f"{label}: {why}")
    elif sheet_row["flags"]:
        report.descriptions_flagged.append(f"{label}: {sheet_row['flags']}")


def map_ingredients(staging: list[dict], sheet: dict, by: str, report: Report) -> list[dict]:
    ingredients: dict[str, dict] = {}
    for row in staging:
        name = row["generic_name"]
        if not name:
            report.skipped.append("ingredient with no name")
            continue
        if name.lower() in ingredients:
            report.skipped.append(f"ingredient {name}: listed twice")
            continue
        sheet_row = sheet.get(("ingredient", name))
        text, why = usable_description(sheet_row)
        urls = sheet_row["source_url"].split() if sheet_row else []
        if text and not (urls and urls[0].startswith("https://")):
            text, why = "", f"no https source link in {DESCRIPTIONS_FILE}"
        note_description(report, name, sheet_row, why)
        if text and len(urls) > 1:
            # One link only: the API returns source_url as it is.
            report.several_sources.append(f"{name}: kept {urls[0]}, also drew on {' '.join(urls[1:])}")
        ingredients[name.lower()] = {
            **dict.fromkeys(INGREDIENT_FIELDS, ""),
            "generic_name": name, "synonyms": row["synonyms"], "spoken_description": text,
            "source_url": urls[0] if text else "", "entered_by": by,
        }
    return list(ingredients.values())


def map_products(staging: list[dict], sheet: dict, known_generics: set[str], by: str,
                 report: Report) -> tuple[list[dict], dict[str, str]]:
    """Returns (rows, {staging product_id: imported product_id})."""
    candidates: dict[str, list[tuple[dict, dict | None, str]]] = {}
    for row in staging:
        given, brand, form = row["product_id"], row["brand_name"], row["dosage_form"]
        generics, strengths = split_list(row["generic_names"]), split_list(row["strengths"])
        label = given or brand or "product with no ID or brand"
        unknown = [g for g in generics if g.lower() not in known_generics]
        if not (brand and generics and form):
            report.skipped.append(f"{label}: brand, ingredients or dosage form is empty")
        elif len(generics) != len(strengths):
            report.skipped.append(f"{label}: {len(generics)} ingredient(s) but {len(strengths)} strength(s)")
        elif unknown:
            report.skipped.append(f"{label}: ingredient not in {INGREDIENTS_FILE}: {', '.join(unknown)}")
        else:
            pid = given if ID_RE.match(given) else derive_id(brand, strengths, form)
            if not pid:
                report.skipped.append(f"{label}: no usable product_id")
                continue
            sheet_row = sheet.get(("product", given))
            candidates.setdefault(pid, []).append((row, sheet_row, given))

    products: list[dict] = []
    id_map: dict[str, str] = {}
    content = ("brand_name", "generic_names", "strengths", "dosage_form")
    for pid, entries in candidates.items():
        if len({tuple(row[c] for c in content) for row, _, _ in entries}) > 1:
            names = ", ".join(given or row["brand_name"] for row, _, given in entries)
            report.skipped.append(f"{pid}: different products share this ID, none imported ({names})")
            continue
        if len(entries) > 1:
            report.skipped.append(f"{pid}: listed {len(entries)} times, kept the first")
        row, sheet_row, given = entries[0]
        if pid != given:
            report.derived_ids.append(f"{given or '(empty)'} -> {pid}")
        text, why = usable_description(sheet_row)
        note_description(report, pid, sheet_row, why)
        id_map.update({g: pid for _, _, g in entries if g})
        products.append({
            **dict.fromkeys(PRODUCT_FIELDS, ""),
            "product_id": pid, "brand_name": row["brand_name"], "aliases": row["aliases"],
            "generic_names": row["generic_names"], "strengths": row["strengths"],
            "dosage_form": row["dosage_form"], "entered_by": by, "spoken_description": text,
        })
    return products, id_map


def map_passages(staging: list[dict], id_map: dict[str, str], by: str, report: Report) -> list[dict]:
    passages: dict[str, dict] = {}
    for row in staging:
        given, label = row["product_id"], row["passage_id"] or "passage with no ID"
        if given not in id_map:
            report.skipped.append(f"{label}: its product wasn't imported ({given or 'no product_id'})")
        elif not (row["passage_id"] and row["text"]):
            report.skipped.append(f"{label}: ID or text is empty")
        elif row["section"] not in SECTIONS:
            report.skipped.append(f"{label}: unknown section '{row['section']}'")
        elif not TFD_URL_RE.match(row["source_ref"]):
            report.skipped.append(f"{label}: no TheFilipinoDoctor page URL")
        else:
            pid = id_map[given]
            prefixed = row["passage_id"].startswith(f"{given}-")
            passage_id = pid + row["passage_id"][len(given):] if prefixed else row["passage_id"]
            if passage_id in passages:
                report.skipped.append(f"{label}: listed twice")
                continue
            if contains_dosing(row["text"]):
                report.dose_like_passages.append(passage_id)
            passages[passage_id] = {
                **dict.fromkeys(PASSAGE_FIELDS, ""),
                "passage_id": passage_id, "product_id": pid, "section": row["section"], "text": row["text"],
                "source_type": "tfd", "source_ref": row["source_ref"], "entered_by": by,
            }
    return list(passages.values())


def map_staging(staging_dir: Path, by: str) -> tuple[dict, Report]:
    """Read the staging files. Returns (rows for kb_build.write_csvs and push, what happened)."""
    report = Report()
    sheet = {(r["kind"], r["id"]): r for r in read_staging(staging_dir, DESCRIPTIONS_FILE)}
    ingredients = map_ingredients(read_staging(staging_dir, INGREDIENTS_FILE), sheet, by, report)
    known = {i["generic_name"].lower() for i in ingredients}
    products, id_map = map_products(read_staging(staging_dir, PRODUCTS_FILE), sheet, known, by, report)
    passages = map_passages(read_staging(staging_dir, PASSAGES_FILE), id_map, by, report)

    # Approving a product needs its indications text, so don't queue one without it.
    with_indications = {p["product_id"] for p in passages if p["section"] == "indications"}
    for product in products:
        if product["product_id"] not in with_indications:
            report.skipped.append(f"{product['product_id']}: no indications passage")
    products = [p for p in products if p["product_id"] in with_indications]
    passages = [p for p in passages if p["product_id"] in with_indications]
    return {"products": products, "ingredients": ingredients, "passages": passages}, report


def validate_import(data: dict) -> Validator:
    keys = {"products": "product_id", "ingredients": "generic_name", "passages": "passage_id"}
    products, ingredients, passages = ([(f"{name}.csv:{row[keys[name]]}", row) for row in data[name]]
                                       for name in ("products", "ingredients", "passages"))
    return validate_rows(products, ingredients, passages, for_review=True, unfinished=True)


def print_summary(data: dict, report: Report) -> None:
    print("Mapped: " + ", ".join(f"{len(rows)} {name}" for name, rows in data.items()))
    sections = [("Skipped", report.skipped),
                ("Product IDs derived", report.derived_ids),
                ("Descriptions left empty", report.descriptions_empty),
                ("Descriptions the draft sheet flagged (check these first)", report.descriptions_flagged),
                ("Ingredient descriptions from more than one page", report.several_sources),
                ("Passages with dose-like wording (kept, not spoken)", report.dose_like_passages)]
    for title, lines in sections:
        print(f"\n{title}: {len(lines)}")
        for line in lines:
            print(f"  {line}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Turn kb/staging/ drafts into pending rows for the review console.")
    parser.add_argument("--by", required=True, help="who takes responsibility for entering these rows")
    parser.add_argument("--staging-dir", type=Path, default=config.KB_DIR / "staging")
    parser.add_argument("--out", type=Path, default=None,
                        help="where to write the CSVs (default: <staging-dir>/import)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--push", action="store_true", help="also add the rows to Supabase, unapproved")
    mode.add_argument("--dry-run", action="store_true", help="only write the CSVs (the default)")
    args = parser.parse_args()

    by = args.by.strip()
    if not by:
        sys.exit("--by can't be blank: every row records who entered it.")
    out = args.out or args.staging_dir / "import"
    if out.resolve() == config.KB_DIR.resolve():
        sys.exit("Refusing to write into kb/: those CSVs hold only reviewed rows.")

    data, report = map_staging(args.staging_dir, by)
    print_summary(data, report)
    kb_build.write_csvs(data, out)
    print(f"\nWrote products.csv, ingredients.csv and passages.csv to {out}")

    v = validate_import(data)
    for w in v.warnings:
        print(f"warning  {w}")
    for e in v.errors:
        print(f"ERROR    {e}")
    if v.errors:
        sys.exit(f"{len(v.errors)} error(s). Nothing was added to Supabase.")

    if not args.push:
        print("Nothing was added to Supabase (no --push).")
        return
    conninfo = kb_build.admin_conninfo()
    if not conninfo:
        sys.exit("Set KB_ADMIN_DATABASE_URL (or MIGRATION_DATABASE_URL) to the database owner's connection string.")
    for table, c in kb_build.push(data, conninfo, drafts=True).items():
        print(f"{table}: {c['added']} added, {c['updated']} updated, {c['unchanged']} unchanged, "
              f"{c['protected']} kept as they are (already reviewed or edited)")
    print("The rows are pending. A second teammate checks each one in the review console.")


if __name__ == "__main__":
    main()
