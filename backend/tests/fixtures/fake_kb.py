"""
Fake kb/ datasets for tests. Every description, registration number and URL
here is made up (descriptions start with "TEST"); none of it may be copied
into the real kb/.

default_rows(): a tiny made-up dataset ("Testflu", "Fakeamol") for kb_build.
review_rows():  real-looking brands, so the OCR labels in labels.py can match,
                arranged to cover the risky pairs: the same three ingredients
                under two brands (Bioflu vs NEOZEP), a combination sharing an
                ingredient with a single-ingredient product (Alaxan FR vs
                Biogesic), two forms of one brand (Tempra), and an Rx product.
"""
import csv
from pathlib import Path

from app.retrieval import KnowledgeBase
from scripts import kb_build


def product(pid: str, brand: str, generics: str, strengths: str, **overrides) -> dict:
    row = {
        "product_id": pid, "brand_name": brand, "aliases": brand.upper(),
        "generic_names": generics, "strengths": strengths, "dosage_form": "Tablet",
        "classification": "OTC", "fda_reg_no": f"DR-XY{abs(hash(pid)) % 100000:05d}",
        "reg_expiry": "2030-01-01", "manufacturer": "Test Co", "portal_checked_on": "2026-10-08",
        "entered_by": "Ana", "checked_by": "Ben",
        "spoken_description": f"TEST description for {brand}.", "description_checked_by": "Cy",
    }
    row.update(overrides)
    return row


def ingredient(name: str, synonyms: str = "", description: str = "", **overrides) -> dict:
    """Names only by default; pass a description to make it usable for the ingredient tier."""
    row = {"generic_name": name, "synonyms": synonyms, "pnf_listed": "no", "pnf_forms": "", "pnf_page": "",
           "spoken_description": "", "source_url": "", "entered_by": "", "checked_by": ""}
    if description:
        row.update(spoken_description=description, entered_by="Ana", checked_by="Ben",
                   source_url=f"https://thefilipinodoctor.com/drug/test-{name.lower().replace(' ', '-')}")
    row.update(overrides)
    return row


def passage(pid: str, text: str = "For the relief of test symptoms.", n: int = 1, **overrides) -> dict:
    row = {
        "passage_id": f"{pid}-ind-{n}", "product_id": pid, "section": "indications", "text": text,
        "source_type": "box", "source_ref": f"evidence/{pid}/back.jpg", "entered_by": "Ana", "checked_by": "Ben",
    }
    row.update(overrides)
    return row


def tfd_passage(pid: str) -> dict:
    return passage(pid, source_type="tfd", source_ref=f"https://thefilipinodoctor.com/drug/test-{pid}")


def default_rows() -> dict:
    return {
        "products": [
            product("testflu-tab", "Testflu", "Fakeamol|Fakephrine", "500 mg|10 mg"),
            product("fakeamol-500-tab", "Fakemol", "Fakeamol", "500 mg"),
        ],
        "ingredients": [ingredient("Fakeamol", "Fakeaminophen"), ingredient("Fakephrine")],
        "passages": [passage("testflu-tab"), passage("fakeamol-500-tab")],
    }


def review_rows() -> dict:
    products = [
        product("biogesic-500-tab", "Biogesic", "Paracetamol", "500 mg",
                spoken_description="TEST: relieves mild pain and fever."),
        product("alaxan-fr-cap", "Alaxan FR", "Ibuprofen|Paracetamol", "200 mg|325 mg", dosage_form="Capsule",
                aliases="ALAXAN"),
        product("bioflu-tab", "Bioflu", "Phenylephrine HCl|Chlorphenamine Maleate|Paracetamol",
                "10 mg|2 mg|500 mg"),
        product("tempra-325-tab", "Tempra", "Paracetamol", "325 mg"),
        product("tempra-120-syr", "Tempra", "Paracetamol", "120 mg/5 mL", dosage_form="Syrup"),
        product("dolfenal-500-tab", "Dolfenal", "Mefenamic acid", "500 mg", classification="Rx"),
        product("buscopan-10-tab", "Buscopan", "Hyoscine-N-butylbromide", "10 mg"),
    ]
    return {
        "products": products,
        "ingredients": [
            ingredient("Paracetamol", "Acetaminophen", "TEST: Paracetamol relieves pain and fever."),
            ingredient("Ibuprofen", "", "TEST: Ibuprofen relieves pain and swelling."),
            ingredient("Phenylephrine HCl", "Phenylephrine hydrochloride",
                       "TEST: Phenylephrine relieves a blocked nose."),
            ingredient("Chlorphenamine Maleate", "Chlorpheniramine maleate",
                       "TEST: Chlorphenamine relieves sneezing and a runny nose."),
            ingredient("Mefenamic acid"),            # names only: no ingredient tier for it
            ingredient("Hyoscine-N-butylbromide"),
        ],
        "passages": [tfd_passage(p["product_id"]) for p in products],
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


def build_kb(tmp_path: Path, rows: dict | None = None) -> KnowledgeBase:
    """Validate and build a fake dataset into tmp_path/kb.sqlite, the way a deploy does, then load it."""
    kb_dir = write_kb(tmp_path / "kb", review_rows() if rows is None else rows)
    v, data = kb_build.validate(kb_dir)
    assert v.errors == [], v.errors
    db_path = tmp_path / "kb.sqlite"
    kb_build.build(data, kb_build.kb_version(kb_dir), db_path)
    return KnowledgeBase.load(db_path)
