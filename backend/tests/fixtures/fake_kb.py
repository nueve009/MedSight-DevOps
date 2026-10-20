"""
Fake kb/ datasets for tests. Every name, number and sentence here is made up
("Testflu", "Fakeamol"); none of it may be copied into the real kb/.
"""
import csv
from pathlib import Path

from scripts import kb_build


def product(pid: str, brand: str, generics: str, strengths: str, **overrides) -> dict:
    row = {
        "product_id": pid, "brand_name": brand, "aliases": brand.upper(),
        "generic_names": generics, "strengths": strengths, "dosage_form": "Tablet",
        "classification": "OTC", "fda_reg_no": f"DR-XY{abs(hash(pid)) % 100000:05d}",
        "reg_expiry": "2030-01-01", "manufacturer": "Test Co", "portal_checked_on": "2026-10-08",
        "entered_by": "Ana", "checked_by": "Ben",
    }
    row.update(overrides)
    return row


def ingredient(name: str, synonyms: str = "", **overrides) -> dict:
    row = {"generic_name": name, "synonyms": synonyms, "pnf_listed": "no", "pnf_forms": "", "pnf_page": ""}
    row.update(overrides)
    return row


def passage(pid: str, text: str = "For the relief of test symptoms.", n: int = 1, **overrides) -> dict:
    row = {
        "passage_id": f"{pid}-ind-{n}", "product_id": pid, "section": "indications", "text": text,
        "source_type": "box", "source_ref": f"evidence/{pid}/back.jpg", "entered_by": "Ana", "checked_by": "Ben",
    }
    row.update(overrides)
    return row


def default_rows() -> dict:
    return {
        "products": [
            product("testflu-tab", "Testflu", "Fakeamol|Fakephrine", "500 mg|10 mg"),
            product("fakeamol-500-tab", "Fakemol", "Fakeamol", "500 mg"),
        ],
        "ingredients": [ingredient("Fakeamol", "Fakeaminophen"), ingredient("Fakephrine")],
        "passages": [passage("testflu-tab"), passage("fakeamol-500-tab")],
    }


def write_kb(kb_dir: Path, rows: dict | None = None) -> Path:
    rows = default_rows() if rows is None else rows
    kb_dir.mkdir(parents=True, exist_ok=True)
    for name, fields in (("products", kb_build.PRODUCT_FIELDS), ("ingredients", kb_build.INGREDIENT_FIELDS),
                         ("passages", kb_build.PASSAGE_FIELDS)):
        with open(kb_dir / f"{name}.csv", "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows.get(name, []))
    return kb_dir
