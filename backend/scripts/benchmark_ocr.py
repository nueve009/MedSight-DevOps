"""
Benchmark PaddleOCR vs EasyOCR against a folder of test images and lock in
the winner for the main pipeline (handoff Section 6, open item #2).

The Kaggle "Pharmaceutical Drugs and Vitamins Dataset V2" has NO OCR ground
truth (it's a classification dataset), so we can't compute text-accuracy
directly. Instead each engine is scored on two signals per image:

  1. mean self-reported OCR confidence
  2. a "known-token hit" heuristic: does the extracted text contain at least
     one token from the expected product name? (expected name is taken from
     the image's parent folder name if the dataset is organized by class,
     e.g. .../Biogesic/img001.jpg -> expects "biogesic")

Usage:
    python -m scripts.benchmark_ocr /path/to/kaggle_dataset_root

The dataset root is expected to contain one subfolder per product class,
matching how the Kaggle dataset ships. If your folder is flat, hit-rate
scoring is skipped and only confidence is compared.

Writes:
    config/ocr_benchmark_results.json  (full per-image results, for manual review)
    config/ocr_engine_choice.json      (winner, read by main.py)
"""
import json
import sys
import time
from pathlib import Path

from app import config
from scripts.ocr_engines import run_easyocr, run_paddleocr

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def iter_test_images(root: Path):
    """Yield (image_path, expected_label_or_None)."""
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() in IMAGE_EXTS:
            expected = path.parent.name if path.parent != root else None
            yield path, expected


def score_engine(engine_fn, image_path: Path, expected_label: str | None) -> dict:
    start = time.time()
    try:
        text, confidence = engine_fn(str(image_path))
        error = None
    except Exception as e:  # keep benchmarking even if one image fails
        text, confidence, error = "", 0.0, str(e)
    elapsed = time.time() - start

    hit = None
    if expected_label:
        norm_text = text.lower()
        norm_label_tokens = [t for t in expected_label.lower().replace("-", " ").split() if len(t) > 2]
        hit = any(tok in norm_text for tok in norm_label_tokens) if norm_label_tokens else None

    return {
        "confidence": confidence,
        "elapsed_sec": round(elapsed, 3),
        "text_len": len(text),
        "hit": hit,
        "error": error,
    }


def run_benchmark(dataset_root: Path) -> dict:
    images = list(iter_test_images(dataset_root))
    if not images:
        raise SystemExit(f"No images found under {dataset_root}")

    print(f"Found {len(images)} test images under {dataset_root}")
    per_image_results = []

    for i, (img_path, expected) in enumerate(images, 1):
        print(f"[{i}/{len(images)}] {img_path.name} (expected: {expected})")
        row = {"image": str(img_path), "expected_label": expected}
        row["paddleocr"] = score_engine(run_paddleocr, img_path, expected)
        row["easyocr"] = score_engine(run_easyocr, img_path, expected)
        per_image_results.append(row)

    summary = {}
    for engine in ("paddleocr", "easyocr"):
        confidences = [r[engine]["confidence"] for r in per_image_results]
        times = [r[engine]["elapsed_sec"] for r in per_image_results]
        hits = [r[engine]["hit"] for r in per_image_results if r[engine]["hit"] is not None]
        errors = sum(1 for r in per_image_results if r[engine]["error"])

        summary[engine] = {
            "avg_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
            "avg_time_sec": round(sum(times) / len(times), 3) if times else 0.0,
            "hit_rate": round(sum(hits) / len(hits), 4) if hits else None,
            "errors": errors,
            "n_images": len(per_image_results),
        }

    # Winner = higher hit_rate if available (it's the closer proxy for real
    # accuracy); otherwise fall back to avg_confidence.
    p, e = summary["paddleocr"], summary["easyocr"]
    if p["hit_rate"] is not None and e["hit_rate"] is not None:
        winner = "paddleocr" if p["hit_rate"] >= e["hit_rate"] else "easyocr"
        winner_reason = "hit_rate"
    else:
        winner = "paddleocr" if p["avg_confidence"] >= e["avg_confidence"] else "easyocr"
        winner_reason = "avg_confidence"

    result = {
        "summary": summary,
        "winner": winner,
        "winner_reason": winner_reason,
        "per_image_results": per_image_results,
    }
    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.benchmark_ocr /path/to/kaggle_dataset_root")
        sys.exit(1)

    dataset_root = Path(sys.argv[1]).expanduser().resolve()
    if not dataset_root.exists():
        raise SystemExit(f"Path does not exist: {dataset_root}")

    result = run_benchmark(dataset_root)

    config.BENCHMARK_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.BENCHMARK_RESULTS_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    config.OCR_ENGINE_CHOICE_PATH.write_text(
        json.dumps({"engine": result["winner"], "reason": result["winner_reason"]}, indent=2),
        encoding="utf-8",
    )

    print("\n=== Benchmark summary ===")
    print(json.dumps(result["summary"], indent=2))
    print(f"\nWinner: {result['winner']}  (decided by {result['winner_reason']})")
    print(f"Full results: {config.BENCHMARK_RESULTS_PATH}")
    print(f"Engine choice written to: {config.OCR_ENGINE_CHOICE_PATH}")
    print("\nNote: hit_rate is a heuristic proxy (no real OCR ground truth in this")
    print("dataset). Spot-check per_image_results manually before fully trusting it.")


if __name__ == "__main__":
    main()
