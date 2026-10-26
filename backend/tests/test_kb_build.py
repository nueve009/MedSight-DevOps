"""kb_build.py validation and build, on fake datasets only."""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import kb_build
from tests.fixtures.fake_kb import default_rows, ingredient, passage, product, review_rows, tfd_passage, write_kb

BACKEND_DIR = Path(__file__).resolve().parent.parent


def errors_for(tmp_path, rows) -> list[str]:
    v, _ = kb_build.validate(write_kb(tmp_path / "kb", rows))
    return v.errors


def test_valid_fake_dataset_builds(tmp_path):
    kb_dir = write_kb(tmp_path / "kb")
    v, data = kb_build.validate(kb_dir)
    assert v.errors == []
    db_path = tmp_path / "kb.sqlite"
    kb_build.build(data, kb_build.kb_version(kb_dir), db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 2


def test_empty_dataset_is_an_error(tmp_path):
    assert any("no products yet" in e for e in errors_for(tmp_path, {}))


def test_empty_dataset_is_only_a_warning_when_allowed(tmp_path):
    v, _ = kb_build.validate(write_kb(tmp_path / "kb", {}), allow_empty=True)
    assert v.errors == []
    assert any("no products yet" in w for w in v.warnings)


def test_checker_must_differ_from_entrant(tmp_path):
    rows = default_rows()
    rows["products"][0]["checked_by"] = "ana"
    assert any("different person" in e for e in errors_for(tmp_path, rows))


def test_ingredient_and_strength_counts_must_match(tmp_path):
    rows = default_rows()
    rows["products"].append(product("bad-tab", "Badflu", "Fakeamol|Fakephrine", "500 mg"))
    assert any("2 generic name(s) but 1 strength(s)" in e for e in errors_for(tmp_path, rows))


def test_unknown_generic_is_rejected(tmp_path):
    rows = default_rows()
    rows["products"][1]["generic_names"] = "Madeupine"
    assert any("'Madeupine' is not in ingredients.csv" in e for e in errors_for(tmp_path, rows))


# --- M2: TFD source, spoken descriptions, described ingredients ---

def test_review_dataset_is_valid(tmp_path):
    assert errors_for(tmp_path, review_rows()) == []


def test_tfd_passage_needs_a_tfd_url(tmp_path):
    rows = default_rows()
    rows["passages"][0] = passage("testflu-tab", source_type="tfd", source_ref="https://example.com/testflu")
    assert any("TheFilipinoDoctor page URL" in e for e in errors_for(tmp_path, rows))


def test_tfd_passage_with_its_url_is_valid(tmp_path):
    rows = default_rows()
    rows["passages"][0] = tfd_passage("testflu-tab")
    assert errors_for(tmp_path, rows) == []


def test_unknown_source_type_is_rejected(tmp_path):
    rows = default_rows()
    rows["passages"][0] = passage("testflu-tab", source_type="mims")
    assert any("'source_type' must be one of" in e for e in errors_for(tmp_path, rows))


@pytest.mark.parametrize("field", ["spoken_description", "description_checked_by"])
def test_product_needs_a_checked_spoken_description(tmp_path, field):
    rows = default_rows()
    rows["products"][0][field] = ""
    assert any(f"'{field}' is empty" in e for e in errors_for(tmp_path, rows))


@pytest.mark.parametrize("text, message", [
    ("Take 2 tablets every 6 hours.", "dose or schedule"),
    ("TEST " * 70, "keep it under 300"),
])
def test_spoken_description_gates(tmp_path, text, message):
    rows = default_rows()
    rows["products"][0]["spoken_description"] = text
    assert any(message in e for e in errors_for(tmp_path, rows))


def test_names_only_ingredient_is_valid(tmp_path):
    rows = default_rows()
    assert rows["ingredients"][1]["spoken_description"] == ""
    assert errors_for(tmp_path, rows) == []


@pytest.mark.parametrize("missing", ["source_url", "entered_by", "checked_by"])
def test_described_ingredient_needs_every_description_field(tmp_path, missing):
    rows = default_rows()
    rows["ingredients"][0] = ingredient("Fakeamol", "Fakeaminophen", "TEST: fake relief.", **{missing: ""})
    assert any(f"'{missing}' is empty" in e for e in errors_for(tmp_path, rows))


def test_described_ingredient_needs_two_people(tmp_path):
    rows = default_rows()
    rows["ingredients"][0] = ingredient("Fakeamol", "", "TEST: fake relief.", checked_by="ana")
    assert any("different person" in e for e in errors_for(tmp_path, rows))


def test_described_ingredient_source_must_be_https(tmp_path):
    rows = default_rows()
    rows["ingredients"][0] = ingredient("Fakeamol", "", "TEST: fake relief.", source_url="evidence/x.jpg")
    assert any("https://" in e for e in errors_for(tmp_path, rows))


def test_old_header_is_rejected(tmp_path):
    kb_dir = write_kb(tmp_path / "kb")
    (kb_dir / "products.csv").write_text(",".join(kb_build.PRODUCT_FIELDS[:13]) + "\n", encoding="utf-8")
    v, _ = kb_build.validate(kb_dir)
    assert any("header must be exactly" in e for e in v.errors)


def test_cli_validates_another_folder(tmp_path):
    kb_dir = write_kb(tmp_path / "draft", review_rows())
    run = subprocess.run([sys.executable, "-m", "scripts.kb_build", "--check", "--kb-dir", str(kb_dir)],
                         cwd=BACKEND_DIR, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "is valid: 7 products, 6 ingredients, 7 passages (6 brands)" in run.stdout


def test_cli_refuses_allow_empty_without_check():
    run = subprocess.run([sys.executable, "-m", "scripts.kb_build", "--allow-empty"],
                         cwd=BACKEND_DIR, capture_output=True, text=True)
    assert run.returncode != 0
