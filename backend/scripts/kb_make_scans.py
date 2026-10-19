"""
Draft evaluation scans for the verified dataset: runs local OCR over a folder
of packaging images (one subfolder per product, as in sorted_output/packaging)
and writes one JSON line per image to kb/eval/scans_draft.jsonl.

The draft has "expected_product_id": null. A teammate fills it in with a
product_id from kb/products.csv, or "unsupported" for medicines outside the
10, then moves the reviewed lines into kb/eval/scans.jsonl.

Usage:
    python -m scripts.kb_make_scans sorted_output/packaging [--engine paddleocr]
"""
import argparse
import json
from pathlib import Path

from app import config
from scripts.ocr_engines import ENGINES, run_ocr

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
DRAFT_PATH = config.KB_DIR / "eval" / "scans_draft.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("images_root", type=Path)
    parser.add_argument("--engine", choices=sorted(ENGINES), default="paddleocr")
    args = parser.parse_args()

    images = sorted(p for p in args.images_root.rglob("*") if p.suffix.lower() in IMAGE_EXTS)
    if not images:
        raise SystemExit(f"No images found under {args.images_root}")

    DRAFT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DRAFT_PATH, "w", encoding="utf-8") as out:
        for i, image in enumerate(images, 1):
            print(f"[{i}/{len(images)}] {image}")
            try:
                text, confidence = run_ocr(args.engine, str(image))
            except Exception as e:  # keep going; a failed image is just skipped
                print(f"  ! OCR failed: {e}")
                continue
            out.write(json.dumps({
                "id": f"{image.parent.name}/{image.stem}",
                "source_image": image.as_posix(),
                "dataset_class": image.parent.name,
                "ocr_engine": args.engine,
                "ocr_text": text,
                "ocr_confidence": round(confidence, 4),
                "expected_product_id": None,
            }, ensure_ascii=False) + "\n")

    print(f"\nWrote {DRAFT_PATH}. Fill in expected_product_id for each line, "
          f"then move reviewed lines into kb/eval/scans.jsonl.")


if __name__ == "__main__":
    main()
