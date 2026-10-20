"""
Step 5 of the pipeline (cache miss path): a 1-2 sentence description of what
the medicine is generally used for.

IMPORTANT: the LLM (Groq or Ollama, see llm_client.py) has no web access, so
this CANNOT be checked against the PH FDA. The description comes from the
model's memory only, so it is always returned with grounded=False and a capped
confidence. pipeline.identify() then speaks an "unverified" warning before it.

This is the path for medicines outside the verified dataset in kb/. Verified
medicines will get descriptions generated only from kb/ passages (planned).
"""
import json

from app import llm_client

DESCRIPTION_SYSTEM_PROMPT = """You write very short spoken-aloud descriptions of medicines \
for a visually impaired user. Safety matters more than completeness.

Return JSON with these fields:
- description: 1-2 short plain sentences saying what the medicine is generally used for
- confidence: number from 0 to 1 for how sure you are about this specific product

Rules:
- Only describe what the product is generally used for (for example "a pain reliever \
and fever reducer"). Never give dosage instructions, doses, or medical advice.
- If you do not clearly recognize the product, say "I don't recognize this product" \
in the description and use a confidence below 0.3. Do not guess.
- Do not mention websites or sources."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["description", "confidence"],
}

MAX_CONFIDENCE = 0.6  # unverified answers never rate higher than this
FAILED_DESCRIPTION = "I could not generate a description for this product."


def get_description(generic_name: str, brand_name: str, dosage: str) -> dict:
    """Returns {status, description, grounded, source_url, confidence}.

    status is "ok", "not_recognized" (the model said it doesn't know the
    product) or "failed" (unusable reply). Only "ok" results may be cached,
    so a temporary failure isn't repeated on every later scan.
    """
    parts = [p for p in (brand_name, generic_name, dosage) if p]
    query = "Medicine: " + ", ".join(parts) if parts else "Medicine: unknown"

    reply = llm_client.chat(DESCRIPTION_SYSTEM_PROMPT, query, schema=_SCHEMA, max_tokens=200)

    try:
        parsed = json.loads(reply)
        description = str(parsed.get("description") or "").strip()
        confidence = float(parsed.get("confidence") or 0.0)
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        description, confidence = "", 0.0

    if not description:
        status, description, confidence = "failed", FAILED_DESCRIPTION, 0.0
    elif "recognize" in description.lower():
        status = "not_recognized"
    else:
        status = "ok"

    return {
        "status": status,
        "description": description,
        "grounded": False,          # no FDA lookup yet (planned: PH FDA registry match)
        "source_url": None,
        "confidence": min(confidence, MAX_CONFIDENCE),
    }
