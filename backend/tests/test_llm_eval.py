"""The LLM evaluation cases (kb/eval/llm_cases.jsonl) in offline mode, so every
pull request runs them. The live run against Groq is scripts/eval_llm.py --mode live."""
import pytest

from scripts import eval_llm

CASES = eval_llm.load_cases(eval_llm.DEFAULT_CASES)


def test_there_are_enough_cases_of_each_kind():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    assert len(CASES) >= 20
    assert sum(c.get("offline_only", False) for c in CASES) >= 5      # bad model replies and a timeout
    assert sum(c["expect"].get("product") is None for c in CASES) >= 10   # cases that must NOT get a reviewed answer


@pytest.mark.parametrize("result", eval_llm.evaluate(CASES)["results"], ids=lambda r: r["id"])
def test_case_passes(result):
    assert result["passed"], result["failed"]


# The checks themselves must catch the failures they exist for.

@pytest.mark.parametrize("expect, result", [
    ({"product": None}, {"tier": "reviewed", "matched_product_id": "bioflu-tab", "spoken_text": ""}),
    ({"not_product_other_than": "biogesic-500-tab"},
     {"tier": "reviewed", "matched_product_id": "alaxan-fr-cap", "spoken_text": ""}),
    ({"not_say": ["every 4 hours"]}, {"spoken_text": "Take one every 4 hours."}),
    ({"warn_before_description_any": ["$GENERAL_WARNING"]},
     {"spoken_text": "Advil. A pain reliever.", "description": "A pain reliever."}),
    ({}, {"spoken_text": "", "description": "Take 2 tablets every 4 hours."}),
])
def test_unsafe_results_are_critical(expect, result):
    failed, critical = eval_llm.check(expect, result)
    assert failed and critical


def test_a_safe_miss_fails_but_is_not_critical():
    failed, critical = eval_llm.check({"product": "biogesic-500-tab"}, {"status": "rescan", "spoken_text": ""})
    assert failed and not critical


def test_report_table_has_a_row_per_case():
    report = eval_llm.evaluate(CASES[:3])
    table = eval_llm.markdown(report)
    assert "| Result | Case | Input | Expected | Actual |" in table
    assert table.count("| PASS |") == 3


def test_service_busy_is_a_safe_miss_not_critical():
    busy = {"status": "service_unavailable", "tier": None, "spoken_text": "The identification service is busy."}
    failed, critical = eval_llm.check({"status": ["ok"], "product": None,
                                       "warn_before_description_any": ["$GENERAL_WARNING"]}, busy)
    assert failed and not critical


def test_live_mode_retries_once_after_a_503(monkeypatch):
    from app import llm_client
    case = next(c for c in CASES if c["id"] == "reviewed-biogesic")
    calls = []

    def flaky(system, user, schema=None, max_tokens=300):
        calls.append(system)
        if len(calls) == 1:
            raise llm_client.LLMUnavailableError("rate limited")
        return eval_llm.RecordedLLM(case["replies"])(system, user)

    monkeypatch.setattr(llm_client, "chat", flaky)
    monkeypatch.setattr(eval_llm.time, "sleep", lambda s: None)
    report = eval_llm.evaluate([case], mode="live")
    assert report["passed"] == 1 and len(calls) == 2
