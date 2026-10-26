"""
Evaluate the whole pipeline on the team's labeled scans: does each scan get
the right reviewed answer, and does anything get a WRONG one?

The release gate (development plan, section 8): 0 wrong reviewed answers on
at least 100 labeled scans, and at least 95% of unlisted medicines not
matched. The script exits with code 1 if any reviewed answer is wrong.

It calls the real LLM (whatever LLM_PROVIDER says) and uses the built
dataset (data/kb.sqlite, from scripts.kb_build), with a fresh temporary cache
so earlier answers can't hide a problem.

Each line of kb/eval/scans.jsonl:
    {"id": "biogesic-phone-1", "ocr_text": "BIOGESIC\\nParacetamol 500 mg", "ocr_confidence": 0.91,
     "blocks": [{"text": "BIOGESIC", "box": [0.1, 0.2, 0.6, 0.3]}],          (optional)
     "expected": "biogesic-500-tab"}     or "not_listed", "multiple", "rescan"

Usage:
    python -m scripts.eval_kb [--scans kb/eval/scans.jsonl] [--kb-db data/kb.sqlite] [--out report.json]
"""
import argparse
import json
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app import config, llm_client
from app.cache_store import CacheStore
from app.pipeline import ScanRequest, identify_scan
from app.retrieval import KnowledgeBase
from app.scan_rules import Block

NOT_LISTED, MULTIPLE, RESCAN = "not_listed", "multiple", "rescan"
NO_ANSWER = ("rescan", "gave_up")

# Outcome -> is it acceptable? "wrong" is the only outcome that fails the gate.
OUTCOMES = {
    "correct_match": True,          # expected product, got its reviewed answer
    "wrong": False,                 # a reviewed answer for the wrong product, or for anything not expected
    "missed_rescan": True,          # expected product, asked to rescan (safe, but costs the user)
    "missed_multiple": True,        # expected product, said "more than one medicine"
    "missed_unmatched": True,       # expected product, answered from a lower tier
    "correct_not_listed": True,     # unlisted medicine, no reviewed answer
    "correct_multiple": True,
    "multiple_not_detected": True,  # expected multiple, answered or rescanned (no reviewed answer)
    "correct_rescan": True,
    "answered_unreadable": True,    # expected rescan, got a non-reviewed answer: review by hand
}


def classify(expected: str, result: dict) -> str:
    reviewed = result["tier"] == "reviewed"
    got = result.get("matched_product_id")
    if expected not in (NOT_LISTED, MULTIPLE, RESCAN):
        if reviewed:
            return "correct_match" if got == expected else "wrong"
        if result["status"] in NO_ANSWER:
            return "missed_rescan"
        return "missed_multiple" if result["status"] == "multiple" else "missed_unmatched"
    if reviewed:
        return "wrong"
    if expected == NOT_LISTED:
        return "correct_not_listed"
    if expected == MULTIPLE:
        return "correct_multiple" if result["status"] == "multiple" else "multiple_not_detected"
    return "correct_rescan" if result["status"] in NO_ANSWER + ("multiple",) else "answered_unreadable"


def _blocks(raw: list | None) -> list[Block]:
    return [Block(b.get("text", ""), b.get("confidence"), tuple(b["box"]) if b.get("box") else None)
            for b in raw or []]


def load_scans(path: Path, kb: KnowledgeBase) -> list[dict]:
    known = {p.product_id for p in kb.products}
    scans = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        scan = json.loads(line)
        expected = scan.get("expected")
        if expected not in known | {NOT_LISTED, MULTIPLE, RESCAN}:
            raise ValueError(f"{path.name}:{line_no} ({scan.get('id')}): 'expected' must be a product_id "
                             f"in the dataset or one of not_listed, multiple, rescan; got {expected!r}")
        scans.append(scan)
    return scans


def evaluate(scans: list[dict], kb: KnowledgeBase, cache: CacheStore) -> dict:
    results = []
    for scan in scans:
        r = identify_scan(ScanRequest(scan["ocr_text"], blocks=_blocks(scan.get("blocks")),
                                      ocr_confidence=scan.get("ocr_confidence"), meta={"client": "eval"}),
                          cache, kb)
        outcome = classify(scan["expected"], r)
        results.append({"id": scan.get("id"), "expected": scan["expected"], "outcome": outcome,
                        "status": r["status"], "tier": r["tier"], "matched_product_id": r["matched_product_id"],
                        "rescan_reason": r["rescan_reason"], "spoken_text": r["spoken_text"]})

    counts = Counter(r["outcome"] for r in results)
    not_listed = [r for r in results if r["expected"] == NOT_LISTED]
    return {
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": config.GROQ_MODEL if config.LLM_PROVIDER == "groq" else f"ollama:{config.OLLAMA_MODEL}",
        "kb_version": kb.version,
        "scans": len(results),
        "counts": dict(sorted(counts.items())),
        "wrong_reviewed_answers": counts["wrong"],
        "unlisted_not_matched_rate": (sum(r["outcome"] != "wrong" for r in not_listed) / len(not_listed)
                                      if not_listed else None),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the labeled scans and report wrong reviewed answers.")
    parser.add_argument("--scans", type=Path, default=config.KB_DIR / "eval" / "scans.jsonl")
    parser.add_argument("--kb-db", type=Path, default=None, help="default: data/kb.sqlite")
    parser.add_argument("--out", type=Path, help="write the full report as JSON")
    args = parser.parse_args()

    kb_db = args.kb_db or config.KB_DB_PATH
    if not kb_db.exists():
        sys.exit(f"{kb_db} not found. Build the dataset first: python -m scripts.kb_build")
    kb = KnowledgeBase.load(kb_db)
    scans = load_scans(args.scans, kb)
    if not scans:
        sys.exit(f"No scans in {args.scans}.")

    with tempfile.TemporaryDirectory(prefix="medsight_eval_") as tmp:
        config.RUN_LOG_PATH = Path(tmp) / "run_log.jsonl"   # keep eval scans out of the real run log
        try:
            report = evaluate(scans, kb, CacheStore(Path(tmp) / "cache.sqlite"))
        except llm_client.LLMUnavailableError as e:
            sys.exit(f"The LLM is unavailable: {e}")

    for r in report["results"]:
        if r["outcome"] == "wrong":
            print(f"WRONG  {r['id']}: expected {r['expected']}, got reviewed {r['matched_product_id']}")
    print(f"\n{report['scans']} scans, model {report['model']}, kb_version {report['kb_version']}")
    for outcome, n in report["counts"].items():
        print(f"  {outcome:<22} {n}")
    rate = report["unlisted_not_matched_rate"]
    print(f"\nWrong reviewed answers: {report['wrong_reviewed_answers']} (must be 0)")
    print(f"Unlisted medicines not matched: {'n/a' if rate is None else f'{rate:.0%}'} (target 95% or more)")
    if len(scans) < 100:
        print(f"Note: the release gate needs at least 100 labeled scans; this set has {len(scans)}.")
    if args.out:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Full report: {args.out}")
    sys.exit(1 if report["wrong_reviewed_answers"] else 0)


if __name__ == "__main__":
    main()
