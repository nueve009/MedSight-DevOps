"""
Confidence handling (handoff Section 6, open item #3):
When OCR/parse/description confidence is below threshold, surface a clear
warning (console + spoken) and log it to warnings_log.jsonl for later review.
This is intentionally "log, don't block" for the prototype phase.
"""
from app import config
from app.utils import append_jsonl


def check_confidence(stage: str, confidence: float, context: dict) -> bool:
    """
    stage: 'ocr' | 'parse' | 'description'
    Returns True if this stage was low-confidence (caller can use this to
    decide whether to prepend a spoken caveat).
    """
    if confidence is None:
        confidence = 0.0

    is_low = confidence < config.LOW_CONFIDENCE_THRESHOLD
    if is_low:
        # Plain ASCII: on the server a non-UTF-8 stdout must not crash a request.
        print(f"\n[!] LOW CONFIDENCE at [{stage}] stage: {confidence:.2f} "
              f"(threshold {config.LOW_CONFIDENCE_THRESHOLD})")
        append_jsonl(config.WARNINGS_LOG_PATH, {
            "stage": stage,
            "confidence": confidence,
            "threshold": config.LOW_CONFIDENCE_THRESHOLD,
            **context,
        })
    return is_low


def build_spoken_caveat(low_stages: list) -> str:
    if not low_stages:
        return ""
    return ("Note: I'm not fully confident in this reading — " +
            ", ".join(low_stages) + " confidence was low. Please double-check the label. ")
