"""
The "general" tier: a 1-2 sentence description of what a medicine is
generally used for, from the model's own knowledge. It is the fallback for a
medicine that isn't in the reviewed dataset (kb/) and has no web answer.

IMPORTANT: the LLM (Groq or Ollama, see llm_client.py) has no web access, so
nothing here is checked against a source. speech.answer() always speaks the
"isn't in our reviewed list" warning before it.

Code gates (not only the prompt): a reply that gives a dose or schedule, or
is too long, is "failed" and never spoken or cached.
"""
import json

from app import llm_client
from app.llm_parse import ParsedProduct
from app.utils import contains_dosing

DESCRIPTION_SYSTEM_PROMPT = """You write very short spoken-aloud descriptions of medicines \
for a visually impaired user. Safety matters more than completeness.

Return JSON with these fields:
- description: 1-2 short plain sentences saying what the medicine is generally used for
- confidence: number from 0 to 1 for how sure you are about this specific product

Rules:
- Only describe what the product is generally used for (for example "a pain reliever \
and fever reducer"). Never give dosage instructions, doses, or medical advice.
- If the product has several active ingredients, cover what each one is for.
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

MAX_CONFIDENCE = 0.6  # unreviewed answers never rate higher than this
MAX_DESCRIPTION_CHARS = 300
FAILED_DESCRIPTION = "I could not generate a description for this product."


def describe_query(product: ParsedProduct) -> str:
    ingredients = ", ".join(f"{g} {s}".strip() for g, s in zip(product.generics, product.strengths, strict=False))
    parts = [f"Brand: {product.brand}" if product.brand else "",
             f"Active ingredients: {ingredients}" if ingredients else ""]
    return "Medicine. " + "; ".join(p for p in parts if p)


def get_description(product: ParsedProduct) -> dict:
    """Returns {status, description, confidence}.

    status is "ok", "not_recognized" (the model said it doesn't know the
    product) or "failed" (unusable reply, or it broke a gate). Only "ok"
    results may be cached, so a temporary failure isn't repeated on every
    later scan.
    Raises llm_client.LLMUnavailableError if the LLM can't be reached.
    """
    reply = llm_client.chat(DESCRIPTION_SYSTEM_PROMPT, describe_query(product), schema=_SCHEMA, max_tokens=200)

    try:
        parsed = json.loads(reply)
        description = " ".join(str(parsed.get("description") or "").split())
        confidence = float(parsed.get("confidence") or 0.0)
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        description, confidence = "", 0.0

    if not description or len(description) > MAX_DESCRIPTION_CHARS or contains_dosing(description):
        status, description, confidence = "failed", FAILED_DESCRIPTION, 0.0
    elif "recognize" in description.lower():
        status = "not_recognized"
    else:
        status = "ok"

    return {"status": status, "description": description, "confidence": min(confidence, MAX_CONFIDENCE)}
