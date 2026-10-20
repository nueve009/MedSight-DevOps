"""
Filters the dataset down to packaging-only images, since this phase is scoped
to reading text on packaging (not shape/color ID of loose pills).

The dataset has no packaging-vs-loose-medicine label, so this uses a heuristic:
run OCR on every image and bucket it by how many text regions were found.
Packaging shots have several (brand, dosage, warnings...); loose pills have
few or none.

This is a heuristic, not ground truth. Spot-check both output folders and
drag any obvious mis-sorts across manually before benchmarking.

Usage:
    python -m scripts.sort_packaging_images DATASET_ROOT OUTPUT_DIR [--min-text-regions 3]

Output:
    OUTPUT_DIR/packaging/<ClassName>/...
    OUTPUT_DIR/loose_medicine/<ClassName>/...
    OUTPUT_DIR/sort_log.json
"""
import argparse
import json
import shutil
from pathlib import Path

from paddleocr import PaddleOCR

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

_ocr = None


def _get_ocr():
    global _ocr
    if _ocr is None:
        _ocr = PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=False,
        )
    return _ocr


def count_text_regions(image_path: Path) -> int:
    try:
        results = _get_ocr().predict(str(image_path))
        if not results:
            return 0
        res_dict = results[0].json
        res_dict = res_dict.get("res", res_dict)
        return len(res_dict.get("rec_texts", []) or [])
    except Exception as e:
        print(f"  ! OCR failed on {image_path.name}: {e}")
        return 0


def iter_images(root: Path):
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() in IMAGE_EXTS:
            # class name = the folder directly containing the image
            yield path, path.parent.name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--min-text-regions", type=int, default=3,
                        help="Images with at least this many detected text "
                             "regions count as packaging (default: 3)")
    args = parser.parse_args()

    dataset_root = args.dataset_root.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    packaging_dir = output_dir / "packaging"
    loose_dir = output_dir / "loose_medicine"
    packaging_dir.mkdir(parents=True, exist_ok=True)
    loose_dir.mkdir(parents=True, exist_ok=True)

    images = list(iter_images(dataset_root))
    if not images:
        raise SystemExit(f"No images found under {dataset_root}")

    print(f"Found {len(images)} images. "
          f">= {args.min_text_regions} text regions = packaging.")

    log = []
    packaging_count = 0

    for i, (img_path, class_name) in enumerate(images, 1):
        n_regions = count_text_regions(img_path)
        is_packaging = n_regions >= args.min_text_regions

        dest_dir = (packaging_dir if is_packaging else loose_dir) / class_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(img_path, dest_dir / img_path.name)

        if is_packaging:
            packaging_count += 1

        log.append({
            "image": str(img_path),
            "class": class_name,
            "text_regions": n_regions,
            "classified_as": "packaging" if is_packaging else "loose_medicine",
        })

        if i % 10 == 0 or i == len(images):
            print(f"  [{i}/{len(images)}] done ({packaging_count} packaging so far)")

    (output_dir / "sort_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")

    print(f"\nDone. {packaging_count}/{len(images)} classified as packaging.")
    print(f"Packaging:      {packaging_dir}")
    print(f"Loose medicine: {loose_dir}")
    print(f"Log:            {output_dir / 'sort_log.json'}")


if __name__ == "__main__":
    main()
