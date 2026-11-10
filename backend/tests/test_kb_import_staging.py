"""Importing kb/staging/ drafts as pending rows (scripts/kb_import_staging.py).
Fake staging files only, never the team's real drafts.

The Postgres half needs MEDSIGHT_TEST_DATABASE_URL (a throwaway database, as
for test_store.py); the rest runs everywhere."""
import csv
import sys

import pytest

from app import config
from app.kb_validate import validate_rows
from scripts import kb_build, kb_import_staging
from tests.test_store import PG_SSLMODE, PG_URL, _reset_postgres, pg_execute

URL = "https://thefilipinodoctor.com/drug/testamol"
FDA_FIELDS = ("classification", "fda_reg_no", "reg_expiry", "manufacturer", "portal_checked_on")


def product(pid="testamol-500-tab", **changes) -> dict:
    return {"product_id": pid, "brand_name": "Testamol", "aliases": "", "generic_names": "Fakeamol",
            "strengths": "500 mg", "dosage_form": "Tablet", "classification": "OTC", "fda_reg_no": "",
            "reg_expiry": "", "manufacturer": "Test Co", "portal_checked_on": "", "entered_by": "",
            "checked_by": "", "source_url": URL, "retrieved_at": "2026-10-26", "scrape_notes": "", **changes}


def ingredient(name="Fakeamol") -> dict:
    return {"generic_name": name, "synonyms": "", "pnf_listed": "", "pnf_forms": "", "pnf_page": "",
            "source_url": URL, "retrieved_at": "2026-10-26"}


def passage(pid="testamol-500-tab", section="indications", number=1, **changes) -> dict:
    code = "ind" if section == "indications" else "warn"
    return {"passage_id": f"{pid}-{code}-{number}", "product_id": pid, "section": section,
            "text": "For test pain.", "source_type": "tfd", "source_ref": URL, "entered_by": "",
            "checked_by": "", "brand_query": "Testamol", "retrieved_at": "2026-10-26", **changes}


def description(kind="product", id_="testamol-500-tab", draft="Used to relieve test pain.", **changes) -> dict:
    return {"kind": kind, "id": id_, "name": id_, "draft": draft, "flags": "", "source_text": "For test pain.",
            "source_url": URL, "approved_description": "", "checked_by": "", **changes}


def write(path, rows) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def staging(tmp_path, products=None, ingredients=None, passages=None, descriptions=None):
    folder = tmp_path / "staging"
    folder.mkdir()
    write(folder / kb_import_staging.PRODUCTS_FILE, products or [product()])
    write(folder / kb_import_staging.INGREDIENTS_FILE, ingredients or [ingredient()])
    write(folder / kb_import_staging.PASSAGES_FILE, passages or [passage()])
    write(folder / kb_import_staging.DESCRIPTIONS_FILE,
          descriptions or [description(), description("ingredient", "Fakeamol", "Fakeamol is used for test pain.")])
    return folder


def run(tmp_path, **parts):
    return kb_import_staging.map_staging(staging(tmp_path, **parts), "Ana")


def test_rows_are_mapped_to_the_dataset_columns(tmp_path):
    data, report = run(tmp_path)
    p = data["products"][0]
    assert list(p) == kb_build.PRODUCT_FIELDS
    assert (p["product_id"], p["brand_name"], p["generic_names"], p["strengths"], p["dosage_form"]) == (
        "testamol-500-tab", "Testamol", "Fakeamol", "500 mg", "Tablet")
    assert p["entered_by"] == "Ana" and p["spoken_description"] == "Used to relieve test pain."
    i = data["ingredients"][0]
    assert list(i) == kb_build.INGREDIENT_FIELDS
    assert (i["entered_by"], i["source_url"], i["spoken_description"]) == (
        "Ana", URL, "Fakeamol is used for test pain.")
    t = data["passages"][0]
    assert list(t) == kb_build.PASSAGE_FIELDS
    assert (t["source_type"], t["source_ref"], t["entered_by"]) == ("tfd", URL, "Ana")
    assert report.skipped == [] and report.derived_ids == []
    assert kb_import_staging.validate_import(data).errors == []


def test_fda_values_and_checker_columns_stay_empty_even_when_the_draft_has_them(tmp_path):
    draft = product(classification="Rx", fda_reg_no="DR-XY1", reg_expiry="2030-01-01",
                    manufacturer="Test Co", portal_checked_on="2026-01-01", checked_by="Ben")
    data, _ = run(tmp_path, products=[draft])
    p = data["products"][0]
    assert all(p[f] == "" for f in FDA_FIELDS)
    assert p["checked_by"] == "" and p["description_checked_by"] == ""
    assert data["ingredients"][0]["checked_by"] == "" and data["passages"][0]["checked_by"] == ""


def test_finished_rules_still_refuse_a_row_without_fda_values(tmp_path):
    data, _ = run(tmp_path)
    rows = [(f"products.csv:{i}", r) for i, r in enumerate(data["products"])]
    ingredients = [("ingredients.csv:1", data["ingredients"][0])]
    passages = [("passages.csv:1", data["passages"][0])]
    errors = validate_rows(rows, ingredients, passages, for_review=True).errors
    assert any("'fda_reg_no' is empty" in e for e in errors)
    assert any("'pnf_listed' must be" in e for e in errors)


def test_many_products_without_a_registration_are_not_duplicates(tmp_path):
    second = product("testamol-250-tab", strengths="250 mg")
    data, _ = run(tmp_path, products=[product(), second],
                  passages=[passage(), passage("testamol-250-tab")])
    assert kb_import_staging.validate_import(data).errors == []


@pytest.mark.parametrize("draft", ["TEST: take 2 tablets every 4 hours.", "Used for test pain. " * 20])
def test_a_description_with_a_dose_or_too_long_is_dropped_and_listed(tmp_path, draft):
    data, report = run(tmp_path, descriptions=[description(draft=draft)])
    assert data["products"][0]["spoken_description"] == ""
    assert any(line.startswith("testamol-500-tab:") for line in report.descriptions_empty)
    assert kb_import_staging.validate_import(data).errors == []


def test_an_ingredient_without_a_description_has_no_source_url(tmp_path):
    data, report = run(tmp_path, descriptions=[
        description(), description("ingredient", "Fakeamol", "", flags="no source")])
    assert data["ingredients"][0]["spoken_description"] == "" and data["ingredients"][0]["source_url"] == ""
    assert "Fakeamol: no source" in report.descriptions_empty
    assert kb_import_staging.validate_import(data).errors == []


def test_an_ingredient_description_from_two_pages_keeps_one_link(tmp_path):
    two = f"{URL} https://thefilipinodoctor.com/drug/other"
    data, report = run(tmp_path, descriptions=[
        description(), description("ingredient", "Fakeamol", "Fakeamol is used for test pain.", source_url=two)])
    assert data["ingredients"][0]["source_url"] == URL
    assert len(report.several_sources) == 1


def test_flagged_drafts_are_kept_and_listed(tmp_path):
    data, report = run(tmp_path, descriptions=[description(flags="new words: zebra")])
    assert data["products"][0]["spoken_description"]
    assert report.descriptions_flagged == ["testamol-500-tab: new words: zebra"]


def test_a_sentence_a_person_wrote_wins_over_the_draft(tmp_path):
    data, _ = run(tmp_path, descriptions=[description(approved_description="Used for test aches.")])
    assert data["products"][0]["spoken_description"] == "Used for test aches."
    assert data["products"][0]["description_checked_by"] == ""


def test_a_bad_product_id_is_derived_and_its_passages_follow(tmp_path):
    bad = "Bad ID!"
    data, report = run(tmp_path, products=[product(bad)], passages=[passage(bad)],
                       descriptions=[description(id_=bad)])
    derived = "testamol-500-mg-tablet"
    assert data["products"][0]["product_id"] == derived
    assert data["passages"][0]["product_id"] == derived
    assert data["passages"][0]["passage_id"] == f"{derived}-ind-1"
    assert report.derived_ids == [f"{bad} -> {derived}"]
    assert data["products"][0]["spoken_description"]
    assert kb_import_staging.validate_import(data).errors == []


def test_two_different_products_with_one_id_are_never_merged(tmp_path):
    other = product(brand_name="Otheramol")
    data, report = run(tmp_path, products=[product(), other])
    assert data["products"] == [] and data["passages"] == []
    assert any("different products share this ID" in line for line in report.skipped)


def test_the_same_product_listed_twice_is_imported_once(tmp_path):
    data, report = run(tmp_path, products=[product(), product()])
    assert len(data["products"]) == 1
    assert any("listed 2 times" in line for line in report.skipped)


def test_a_passage_of_a_skipped_product_is_dropped(tmp_path):
    bad = product("testamol-x-tab", strengths="")      # one ingredient, no strength
    data, report = run(tmp_path, products=[product(), bad],
                       passages=[passage(), passage("testamol-x-tab"), passage("nobody-tab")])
    assert [p["product_id"] for p in data["products"]] == ["testamol-500-tab"]
    assert [p["product_id"] for p in data["passages"]] == ["testamol-500-tab"]
    assert any("testamol-x-tab-ind-1" in line and "wasn't imported" in line for line in report.skipped)
    assert any("nobody-tab-ind-1" in line for line in report.skipped)


def test_a_product_with_an_unlisted_ingredient_is_skipped(tmp_path):
    data, report = run(tmp_path, products=[product(generic_names="Unknownol")])
    assert data["products"] == []
    assert any("Unknownol" in line for line in report.skipped)


def test_a_product_without_indications_is_skipped_with_its_warnings(tmp_path):
    data, report = run(tmp_path, passages=[passage(section="warnings")])
    assert data["products"] == [] and data["passages"] == []
    assert "testamol-500-tab: no indications passage" in report.skipped


def test_a_passage_without_a_tfd_link_is_skipped(tmp_path):
    bad = passage(section="warnings", source_ref="https://example.com/drug")
    data, report = run(tmp_path, passages=[passage(), bad])
    assert len(data["passages"]) == 1
    assert any("no TheFilipinoDoctor page URL" in line for line in report.skipped)


# --- the command ---

def command(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["kb_import_staging", *args])
    kb_import_staging.main()


def test_by_is_required(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as e:
        command(monkeypatch, "--staging-dir", str(staging(tmp_path)))
    assert e.value.code == 2


def test_by_cannot_be_blank(monkeypatch, tmp_path):
    with pytest.raises(SystemExit, match="can't be blank"):
        command(monkeypatch, "--by", " ", "--staging-dir", str(staging(tmp_path)))


def test_without_push_it_writes_csvs_and_never_connects(monkeypatch, tmp_path, capsys):
    def no_push(*args, **kwargs):
        raise AssertionError("pushed without --push")

    monkeypatch.setattr(kb_build, "push", no_push)
    folder = staging(tmp_path)
    command(monkeypatch, "--by", "Ana", "--staging-dir", str(folder))
    v, data = kb_build.validate(folder / "import", for_review=True)
    assert len(data["products"]) == 1 and data["products"][0]["entered_by"] == "Ana"
    assert "Nothing was added to Supabase" in capsys.readouterr().out


def test_dry_run_and_push_cannot_be_combined(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as e:
        command(monkeypatch, "--by", "Ana", "--staging-dir", str(staging(tmp_path)), "--push", "--dry-run")
    assert e.value.code == 2


def test_it_refuses_to_write_into_the_reviewed_folder(monkeypatch, tmp_path):
    with pytest.raises(SystemExit, match="Refusing to write into kb/"):
        command(monkeypatch, "--by", "Ana", "--staging-dir", str(staging(tmp_path)), "--out", str(config.KB_DIR))


def test_push_uses_the_drafts_mode(monkeypatch, tmp_path):
    seen = {}

    def fake_push(data, conninfo, drafts=False):
        seen.update(conninfo=conninfo, drafts=drafts, products=len(data["products"]))
        return {"kb_products": {"added": 1, "updated": 0, "unchanged": 0, "protected": 0}}

    monkeypatch.setattr(kb_build, "push", fake_push)
    monkeypatch.setattr(kb_build, "admin_conninfo", lambda: "postgresql://unused")
    command(monkeypatch, "--by", "Ana", "--staging-dir", str(staging(tmp_path)), "--push")
    assert seen == {"conninfo": "postgresql://unused", "drafts": True, "products": 1}


def test_invalid_rows_are_never_pushed(monkeypatch, tmp_path):
    def no_push(*args, **kwargs):
        raise AssertionError("pushed invalid rows")

    monkeypatch.setattr(kb_build, "push", no_push)
    monkeypatch.setattr(kb_build, "admin_conninfo", lambda: "postgresql://unused")
    # Two ingredients whose synonyms clash: only the validator catches this.
    ingredients = [ingredient(), {**ingredient("Otheramol"), "synonyms": "Fakeamol"}]
    with pytest.raises(SystemExit, match="Nothing was added"):
        command(monkeypatch, "--by", "Ana", "--staging-dir", str(staging(tmp_path, ingredients=ingredients)), "--push")


# --- what reaches the database (fake connection) ---

class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def fetchone(self):
        return (True,)


class FakeConnection:
    def __init__(self, protected):
        self.protected = protected
        self.inserts = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return self

    def execute(self, sql, params=None):
        if "FOR UPDATE" in sql:
            table = sql.split(" FROM ")[1].split()[0]
            return FakeCursor([(key,) for key in self.protected.get(table, [])])
        if sql.startswith("INSERT"):
            self.inserts.append((sql, params))
        return FakeCursor([])


def fake_push(monkeypatch, tmp_path, protected):
    import psycopg

    conn = FakeConnection(protected)
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: conn)
    data, _ = run(tmp_path)
    return conn, kb_build.push(data, "postgresql://unused", drafts=True)


def test_drafts_never_write_reviewer_or_checker_columns(monkeypatch, tmp_path):
    conn, report = fake_push(monkeypatch, tmp_path, {})
    assert len(conn.inserts) == 3 and report["kb_products"]["added"] == 1
    for sql, _ in conn.inserts:
        columns = sql.split("(", 2)[1].split(")")[0]
        for field in (*kb_build.REVIEWER_FILLED_FIELDS, *kb_build.CHECKER_FIELDS):
            assert field not in columns.split(", ")


def test_drafts_skip_rows_a_person_has_handled(monkeypatch, tmp_path):
    conn, report = fake_push(monkeypatch, tmp_path, {"kb_products": ["testamol-500-tab"]})
    assert report["kb_products"]["protected"] == 1 and report["kb_products"]["added"] == 0
    assert not any("kb_products" in sql for sql, _ in conn.inserts)
    assert report["kb_ingredients"]["added"] == 1


# --- Supabase (Postgres) ---

@pytest.fixture
def pg(monkeypatch):
    if not PG_URL:
        pytest.skip("MEDSIGHT_TEST_DATABASE_URL not set")
    monkeypatch.setattr(config, "DATABASE_SSLMODE", PG_SSLMODE)
    _reset_postgres()
    return PG_URL


def import_rows(tmp_path, subfolder="a", **parts):
    folder = tmp_path / subfolder
    folder.mkdir()
    data, _ = run(folder, **parts)
    return kb_build.push(data, PG_URL, drafts=True)


def changed_draft() -> list[dict]:
    return [description(draft="Used to relieve other test pain."),
            description("ingredient", "Fakeamol", "Fakeamol is used for test pain.")]


def spoken() -> str:
    return pg_execute("SELECT spoken_description FROM kb_products WHERE product_id = 'testamol-500-tab'")[0][0]


def test_new_rows_arrive_pending_with_fda_values_empty(pg, tmp_path):
    report = import_rows(tmp_path)
    assert report["kb_products"]["added"] == 1 and report["kb_passages"]["added"] == 1
    row = pg_execute("SELECT review_state, approved, entered_by, fda_reg_no, reg_expiry, portal_checked_on, "
                     "classification, manufacturer, checked_by FROM kb_products")[0]
    assert row == ("pending", False, "Ana", None, None, None, "", "", "")
    assert pg_execute("SELECT pnf_listed, review_state FROM kb_ingredients") == [("no", "pending")]


def test_importing_again_changes_nothing(pg, tmp_path):
    import_rows(tmp_path, "a")
    report = import_rows(tmp_path, "b")
    assert report["kb_products"]["unchanged"] == 1 and report["kb_products"]["updated"] == 0


def test_a_pending_untouched_row_gets_the_new_draft(pg, tmp_path):
    import_rows(tmp_path, "a")
    report = import_rows(tmp_path, "b", descriptions=changed_draft())
    assert report["kb_products"]["updated"] == 1
    assert spoken() == "Used to relieve other test pain."


def test_an_approved_row_is_not_overwritten_or_unapproved(pg, tmp_path):
    import_rows(tmp_path, "a")
    pg_execute("UPDATE kb_ingredients SET checked_by = 'Ben'")
    pg_execute("UPDATE kb_ingredients SET approved = true")
    pg_execute("UPDATE kb_products SET fda_reg_no = 'DR-XY1', reg_expiry = '2030-01-01', "
               "portal_checked_on = '2026-11-01', classification = 'OTC'")
    pg_execute("UPDATE kb_products SET checked_by = 'Ben', description_checked_by = 'Cy'")
    pg_execute("UPDATE kb_products SET approved = true")

    report = import_rows(tmp_path, "b", descriptions=changed_draft())

    assert report["kb_products"]["protected"] == 1 and report["kb_products"]["updated"] == 0
    row = pg_execute("SELECT approved, review_state, checked_by, fda_reg_no FROM kb_products")[0]
    assert row == (True, "approved", "Ben", "DR-XY1")
    assert spoken() == "Used to relieve test pain."


def test_a_row_edited_in_the_console_is_not_overwritten(pg, tmp_path):
    import_rows(tmp_path, "a")
    pg_execute("UPDATE kb_products SET fda_reg_no = 'DR-XY1', last_edited_by = 'Ben'")
    report = import_rows(tmp_path, "b", descriptions=changed_draft())
    assert report["kb_products"]["protected"] == 1
    assert pg_execute("SELECT fda_reg_no FROM kb_products") == [("DR-XY1",)]
    assert spoken() == "Used to relieve test pain."


def test_a_row_sent_back_is_not_put_in_the_queue_again(pg, tmp_path):
    import_rows(tmp_path, "a")
    pg_execute("UPDATE kb_products SET review_state = 'changes_requested'")
    report = import_rows(tmp_path, "b", descriptions=changed_draft())
    assert report["kb_products"]["protected"] == 1
    assert pg_execute("SELECT review_state FROM kb_products") == [("changes_requested",)]


def test_fda_values_typed_on_a_pending_row_survive_an_import(pg, tmp_path):
    import_rows(tmp_path, "a")
    pg_execute("UPDATE kb_products SET fda_reg_no = 'DR-XY1', classification = 'OTC'")   # Table Editor: no editor name
    report = import_rows(tmp_path, "b", descriptions=changed_draft())
    assert report["kb_products"]["updated"] == 1
    assert pg_execute("SELECT fda_reg_no, classification FROM kb_products") == [("DR-XY1", "OTC")]
