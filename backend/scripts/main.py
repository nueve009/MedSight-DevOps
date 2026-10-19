"""
Local prototype pipeline (handoff Section 5): laptop webcam + local OCR, then
the same post-OCR pipeline the backend runs (pipeline.py). Useful for testing
the backend logic without the phone.

Run:
    python -m scripts.main

Controls:
    SPACE  - capture frame and run the pipeline on it
    q      - quit

Requires: run benchmark_ocr.py at least once first, so config/ocr_engine_choice.json
exists (falls back to 'paddleocr' if missing, with a warning).
"""
import json
import sys
import time
from pathlib import Path

import cv2

from app import config
from app.cache_store import CacheStore
from app.pipeline import identify
from scripts.ocr_engines import run_ocr
from scripts.tts_output import speak


def load_ocr_engine_choice() -> str:
    if config.OCR_ENGINE_CHOICE_PATH.exists():
        data = json.loads(config.OCR_ENGINE_CHOICE_PATH.read_text(encoding="utf-8"))
        return data.get("engine", "paddleocr")
    print("⚠️  No benchmark result found (run benchmark_ocr.py first). Defaulting to paddleocr.")
    return "paddleocr"

def process_frame(image_path: str, engine_name: str, cache: CacheStore) -> None:
    print("Running OCR...")
    raw_text, ocr_confidence = run_ocr(engine_name, image_path)

    print("Identifying...")
    result = identify(raw_text, ocr_confidence, cache,
                      meta={"image_path": image_path, "ocr_engine": engine_name, "client": "webcam"})

    if result["status"] != "rescan":
        print(f"\n--- Result ({result['status']}, cache {result['cache']}) ---")
        print(f"Brand: {result['brand_name']}\nGeneric: {result['generic_name']}\nDosage: {result['dosage']}")
        print(f"Description: {result['description']}")
        print(f"Grounded in PH FDA source: {result['grounded']}")

    speak(result["spoken_text"])


def main():
    engine_name = load_ocr_engine_choice()
    print(f"Using OCR engine: {engine_name}")
    cache = CacheStore()

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Could not open webcam. Exiting.")
        sys.exit(1)

    print("Press SPACE to capture and identify, or 'q' to quit.")
    capture_dir = Path("captures")
    capture_dir.mkdir(exist_ok=True)

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Failed to read frame from webcam.")
                break

            cv2.imshow("Medicine Scanner (SPACE=capture, q=quit)", frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break
            elif key == ord(" "):
                image_path = str(capture_dir / f"capture_{int(time.time())}.jpg")
                cv2.imwrite(image_path, frame)
                print(f"\nCaptured {image_path}")
                try:
                    process_frame(image_path, engine_name, cache)
                except Exception as e:
                    print(f"Pipeline error: {e}")
                print("\nPress SPACE to capture again, or 'q' to quit.")
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
