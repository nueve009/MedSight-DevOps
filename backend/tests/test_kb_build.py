"""kb_build.py validation and build, on fake datasets only."""
import sqlite3

from scripts import kb_build
from tests.fixtures.fake_kb import default_rows, product, write_kb


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
