"""Tests for scripts/fda_lookup.py, on made-up portal replies (no network)."""

import csv
import json

import pytest
import requests

from scripts import fda_lookup as f

RECORD = {
    "registration_number": "DR-XY00000",
    "generic_name": "Fakeamol",
    "brand_name": "Testflu",
    "dosage_strength_form": "120 mg / 5 mL Suspension (Fake Flavor)",
    "classification": "Over-the-Counter (OTC) Drug",
    "manufacturer": "Test Pharma Inc.",
    "expiry_date": "28 October 2030",
}


def payload(*records, retrieved_on="2026-11-10"):
    return {"query": "Testflu", "retrieved_on": retrieved_on, "response": {f.DRUG_KEY: list(records), "lto_food": []}}


class FakeResponse:
    def __init__(self, status=200, body=None, content_type="application/json", headers=None):
        self.status_code = status
        self.body = {f.DRUG_KEY: [RECORD]} if body is None else body
        self.headers = {"Content-Type": content_type, **(headers or {})}

    def json(self):
        if isinstance(self.body, str):
            raise ValueError("not json")
        return self.body


@pytest.fixture
def portal_replies(monkeypatch):
    """Queue replies for requests.get; records every call and never touches the network."""
    state = {"replies": [], "calls": []}

    def fake_get(url, **kwargs):
        state["calls"].append((url, kwargs))
        reply = state["replies"].pop(0) if state["replies"] else FakeResponse()
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(f.requests, "get", fake_get)
    monkeypatch.setattr(f.time, "sleep", lambda _s: None)
    return state


def test_a_row_for_one_registration():
    row = f.build_rows("Testflu", payload(RECORD))[0]
    assert row["brand_query"] == "Testflu" and row["brand_name"] == "Testflu"
    assert row["generic_name"] == "Fakeamol"
    assert row["strength"] == "120 mg/5 mL" and row["dosage_form"] == "Suspension (Fake Flavor)"
    assert row["fda_reg_no"] == "DR-XY00000" and row["reg_expiry"] == "2030-10-28"
    assert row["classification"] == "OTC" and row["classification_raw"] == RECORD["classification"]
    assert row["manufacturer"] == "Test Pharma Inc."
    assert row["portal_checked_on"] == "2026-11-10"
    assert row["match_count"] == 1 and row["note"] == ""


def test_every_match_is_kept_and_none_is_picked():
    rows = f.build_rows("Testflu", payload(RECORD, {**RECORD, "registration_number": "DR-XY00001"}))
    assert [r["fda_reg_no"] for r in rows] == ["DR-XY00000", "DR-XY00001"]
    assert all(r["match_count"] == 2 for r in rows)


def test_portal_may_send_records_as_an_object():
    data = payload(RECORD)
    data["response"][f.DRUG_KEY] = {"0": RECORD}
    assert len(f.build_rows("Testflu", data)) == 1


@pytest.mark.parametrize("raw,expected", [
    ("Over-the-Counter (OTC) Drug", "OTC"),
    ("Prescription Drug (Rx)", "Rx"),
    ("Household Remedy (HR)", "HR"),
    ("Non-Prescription", ""),
    ("Non-Rx", ""),
    ("OTC / Rx", ""),
    ("Something new", ""),
    ("", ""),
])
def test_classification_mapping(raw, expected):
    assert f.map_classification(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("28 October 2030", "2030-10-28"),
    ("05 Jan 2023", "2023-01-05"),
    ("2030-10-28", "2030-10-28"),
    ("soon", ""),
    ("", ""),
])
def test_date_parsing(raw, expected):
    assert f.parse_date(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("500 mg Tablet", ("500 mg", "Tablet")),
    ("10 mg / 2 mg / 500 mg Film-Coated Tablet", ("10 mg/2 mg/500 mg", "Film-Coated Tablet")),
    ("100 mg/mL Oral Drops", ("100 mg/mL", "Oral Drops")),
    ("325 mg 10 mg / 2 mg Tablet", ("", "")),  # amounts left over: never half-split
    ("Tablet", ("", "")),
    ("See label", ("", "")),
])
def test_strength_and_form_split(raw, expected):
    assert f.split_strength_form(raw) == expected


def test_unclear_values_stay_empty_and_are_noted():
    row = f.build_rows("Testflu", payload({
        **RECORD, "expiry_date": "soon", "classification": "New class", "dosage_strength_form": "See label",
    }))[0]
    assert row["reg_expiry"] == "" and row["classification"] == "" and row["strength"] == ""
    assert "expiry" in row["note"] and "classification" in row["note"] and "strength" in row["note"]


def test_notes_for_the_reviewer():
    expired = f.build_rows("Testflu", payload({**RECORD, "expiry_date": "01 January 2020"}))[0]
    assert "expired on 2020-01-01" in expired["note"]
    other = f.build_rows("Testflu", payload({**RECORD, "brand_name": "Otherbrand"}))[0]
    assert "doesn't contain" in other["note"]
    drhr = f.build_rows("Testflu", payload({
        **RECORD, "registration_number": "DRHR-1", "classification": "Prescription Drug (Rx)"}))[0]
    assert "DRHR" in drhr["note"]


def test_null_fields_become_empty():
    row = f.build_rows("Testflu", payload({**RECORD, "manufacturer": None}))[0]
    assert row["manufacturer"] == ""


def test_lookup_saves_then_reuses_the_cache(portal_replies, tmp_path):
    portal = f.Portal()
    _, cached = f.lookup("Test Flu", portal, tmp_path)
    assert cached is False and len(portal_replies["calls"]) == 1
    assert (tmp_path / "test-flu.json").exists()

    again, cached = f.lookup("Test Flu", portal, tmp_path)
    assert cached is True and len(portal_replies["calls"]) == 1
    assert again["response"][f.DRUG_KEY][0]["brand_name"] == "Testflu"


def test_cached_lookup_keeps_the_day_the_portal_was_asked(portal_replies, tmp_path):
    (tmp_path / "testflu.json").write_text(json.dumps(payload(RECORD, retrieved_on="2026-01-02")), encoding="utf-8")
    data, cached = f.lookup("Testflu", f.Portal(), tmp_path)
    assert cached is True and portal_replies["calls"] == []
    assert f.build_rows("Testflu", data)[0]["portal_checked_on"] == "2026-01-02"


def test_refresh_asks_the_portal_again(portal_replies, tmp_path):
    portal = f.Portal()
    f.lookup("Testflu", portal, tmp_path)
    f.lookup("Testflu", portal, tmp_path, refresh=True)
    assert len(portal_replies["calls"]) == 2


def test_broken_cache_file_is_looked_up_again(portal_replies, tmp_path):
    (tmp_path / "testflu.json").write_text("{oops", encoding="utf-8")
    _, cached = f.lookup("Testflu", f.Portal(), tmp_path)
    assert cached is False and len(portal_replies["calls"]) == 1


def test_request_identifies_itself_and_sends_the_query(portal_replies):
    f.Portal().search("Neozep Forte")
    url, kwargs = portal_replies["calls"][0]
    assert url == f.SEARCH_URL and kwargs["params"] == {"q": "Neozep Forte"}
    assert kwargs["headers"]["User-Agent"] == f.USER_AGENT
    assert kwargs["timeout"] == f.TIMEOUT_S and kwargs["allow_redirects"] is False


def test_requests_are_at_least_the_minimum_interval_apart(monkeypatch):
    assert f.MIN_INTERVAL_S == 5
    clock = {"now": 100.0}
    sleeps, sent_at = [], []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    def fake_get(url, **kwargs):
        sent_at.append(clock["now"])
        clock["now"] += 1.0  # the request itself takes a second
        return FakeResponse()

    monkeypatch.setattr(f.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(f.time, "sleep", fake_sleep)
    monkeypatch.setattr(f.requests, "get", fake_get)

    portal = f.Portal()
    for _ in range(3):
        portal.search("Testflu")
    assert sleeps == [5.0, 5.0]  # measured from the end of the last request
    assert all(b - a >= 5 for a, b in zip(sent_at, sent_at[1:], strict=False))


@pytest.mark.parametrize("reply", [
    FakeResponse(429, headers={"Retry-After": "60"}),
    FakeResponse(403),
    FakeResponse(401),
    FakeResponse(419),
    FakeResponse(302),
    FakeResponse(200, "<html>captcha</html>", content_type="text/html"),
    FakeResponse(200, "not json"),
    FakeResponse(200, {"something_else": []}),
])
def test_refusals_and_surprises_stop_the_run_and_cache_nothing(portal_replies, tmp_path, reply):
    portal_replies["replies"] = [reply]
    with pytest.raises(f.StopRun):
        f.lookup("Testflu", f.Portal(), tmp_path)
    assert not (tmp_path / "testflu.json").exists()


def test_run_stops_at_429_without_asking_for_more(portal_replies, tmp_path):
    portal_replies["replies"] = [FakeResponse(), FakeResponse(429), FakeResponse()]
    code = f.run(["Testflu", "Second", "Third"], tmp_path / "cache", tmp_path)
    assert code == 2 and len(portal_replies["calls"]) == 2
    assert (tmp_path / "fda_lookup_draft.csv").exists()


def test_run_writes_all_matches_and_not_found(portal_replies, tmp_path):
    portal_replies["replies"] = [
        FakeResponse(body={f.DRUG_KEY: [RECORD, {**RECORD, "registration_number": "DR-XY00001"}]}),
        FakeResponse(body={f.DRUG_KEY: []}),
    ]
    assert f.run(["Testflu", "Nosuch"], tmp_path / "cache", tmp_path) == 0
    with (tmp_path / "fda_lookup_draft.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["fda_reg_no"] for r in rows] == ["DR-XY00000", "DR-XY00001"]
    assert list(rows[0]) == f.DRAFT_FIELDS
    assert (tmp_path / "fda_not_found.txt").read_text(encoding="utf-8") == "Nosuch\n"


@pytest.mark.parametrize("error", [
    requests.ConnectionError("down"),
    FakeResponse(500),
])
def test_a_failed_lookup_is_not_reported_as_not_found(portal_replies, tmp_path, error):
    portal_replies["replies"] = [error, FakeResponse()]
    code = f.run(["Broken", "Testflu"], tmp_path / "cache", tmp_path)
    assert code == 1 and len(portal_replies["calls"]) == 2
    assert (tmp_path / "fda_not_found.txt").read_text(encoding="utf-8") == ""
    assert not (tmp_path / "cache" / "broken.json").exists()


def write_team_files(folder):
    (folder / "brands.txt").write_text(
        "# comment\nBiogesic\nNeozep Forte\nhttps://example.test/drug/x\n\n", encoding="utf-8")
    with (folder / "tfd_products_draft.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["product_id", "brand_name"])
        writer.writeheader()
        writer.writerows([{"product_id": "a", "brand_name": "BIOGESIC"}, {"product_id": "b", "brand_name": "Tempra"},
                          {"product_id": "c", "brand_name": "Tempra"}])


def test_team_list_merges_both_files_without_repeats(tmp_path):
    write_team_files(tmp_path)
    assert f.team_brands(tmp_path) == ["Biogesic", "Neozep Forte", "Tempra"]


def test_a_brand_outside_the_team_list_is_refused():
    with pytest.raises(ValueError, match="Crawlme"):
        f.choose_brands(["Biogesic"], ["Biogesic", "Crawlme"], None)


def test_choose_brands_restricts_and_limits():
    team = ["Biogesic", "Neozep Forte", "Tempra"]
    assert f.choose_brands(team, ["tempra", "TEMPRA", "biogesic"], None) == ["Tempra", "Biogesic"]
    assert f.choose_brands(team, None, 2) == ["Biogesic", "Neozep Forte"]
