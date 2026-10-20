"""
Unified wrapper around PaddleOCR and EasyOCR so the rest of the pipeline
doesn't care which engine won the benchmark (see benchmark_ocr.py).

Both functions return: (raw_text: str, confidence: float in [0,1])
"""
from typing import Tuple

_paddle_instance = None
_easyocr_instance = None


def _get_paddle():
    global _paddle_instance
    if _paddle_instance is None:
        from paddleocr import PaddleOCR
        # PaddleOCR 3.x API: use_angle_cls was renamed use_textline_orientation,
        # and show_log was removed. Doc orientation/unwarping are off since
        # packaging photos are handheld, not scanned documents.
        _paddle_instance = PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            enable_mkldnn=False,
        )
    return _paddle_instance


def _get_easyocr():
    global _easyocr_instance
    if _easyocr_instance is None:
        import easyocr
        _easyocr_instance = easyocr.Reader(["en"], gpu=False)
    return _easyocr_instance


def run_paddleocr(image_path: str) -> Tuple[str, float]:
    # PaddleOCR 3.x API: .ocr() -> .predict(), returns a list of result
    # objects (one per input image) with a .json attribute instead of
    # nested [[bbox, (text, score)], ...] tuples.
    ocr = _get_paddle()
    results = ocr.predict(image_path)
    if not results:
        return "", 0.0

    res_dict = results[0].json
    res_dict = res_dict.get("res", res_dict)  # unwrap the {'res': {...}} envelope

    texts = res_dict.get("rec_texts", []) or []
    scores = res_dict.get("rec_scores", []) or []

    raw_text = "\n".join(texts)
    confidence = float(sum(scores) / len(scores)) if len(scores) else 0.0
    return raw_text, confidence


def run_easyocr(image_path: str) -> Tuple[str, float]:
    reader = _get_easyocr()
    result = reader.readtext(image_path)  # [(bbox, text, conf), ...]
    if not result:
        return "", 0.0
    lines = [r[1] for r in result]
    scores = [r[2] for r in result]
    raw_text = "\n".join(lines)
    confidence = sum(scores) / len(scores) if scores else 0.0
    return raw_text, confidence


ENGINES = {
    "paddleocr": run_paddleocr,
    "easyocr": run_easyocr,
}


def run_ocr(engine_name: str, image_path: str) -> Tuple[str, float]:
    if engine_name not in ENGINES:
        raise ValueError(f"Unknown OCR engine '{engine_name}'. Choose from {list(ENGINES)}")
    return ENGINES[engine_name](image_path)
