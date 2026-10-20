"""
Provider-agnostic chat helper for the parse and description steps.

LLM_PROVIDER picks the backend:
  - "groq":   Groq's OpenAI-compatible API (cloud backend default)
  - "ollama": a local Ollama server (offline work)

Both return the raw JSON text of the reply. Callers parse it themselves and
must treat a bad reply as "no result", never as a guess.
"""
import json

import requests

from app import config


class LLMUnavailableError(RuntimeError):
    """The LLM could not be reached or refused the request (rate limit, bad key, down)."""


def chat(system: str, user: str, schema: dict | None = None, max_tokens: int = 300) -> str:
    """Send one chat turn and return the raw text reply."""
    if config.LLM_PROVIDER == "groq":
        return _chat_groq(system, user, schema, max_tokens)
    if config.LLM_PROVIDER == "ollama":
        return _chat_ollama(system, user, schema, max_tokens)
    raise LLMUnavailableError(f"Unknown LLM_PROVIDER '{config.LLM_PROVIDER}'. Use 'groq' or 'ollama'.")


def _chat_groq(system: str, user: str, schema: dict | None, max_tokens: int) -> str:
    if not config.GROQ_API_KEY:
        raise LLMUnavailableError("GROQ_API_KEY is not set.")

    # JSON mode guarantees valid JSON but not the field layout, so the schema
    # is spelled out in the system prompt as well.
    if schema:
        system = f"{system}\n\nRespond with a JSON object matching this schema:\n{json.dumps(schema)}"

    payload = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    try:
        resp = requests.post(
            f"{config.GROQ_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            json=payload,
            timeout=60,
        )
    except requests.RequestException as e:
        raise LLMUnavailableError(f"Can't reach Groq: {e}") from e

    if resp.status_code == 429:
        raise LLMUnavailableError("Groq rate limit reached. Try again shortly.")
    if resp.status_code >= 400:
        # Groq error bodies don't echo the API key, so they're safe to surface.
        raise LLMUnavailableError(f"Groq returned HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]


def _chat_ollama(system: str, user: str, schema: dict | None, max_tokens: int) -> str:
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        # Ollama constrains output to the schema itself (much more reliable for
        # small models); otherwise it just forces valid JSON.
        "format": schema if schema else "json",
        "options": {"temperature": 0, "num_predict": max_tokens},
    }
    try:
        resp = requests.post(f"{config.OLLAMA_HOST}/api/chat", json=payload, timeout=180)
        resp.raise_for_status()
    except requests.ConnectionError as e:
        raise LLMUnavailableError(
            f"Can't reach Ollama at {config.OLLAMA_HOST}. Is it running? "
            f"Try `ollama list` in a terminal, or open the Ollama app."
        ) from e
    except requests.HTTPError as e:
        raise LLMUnavailableError(
            f"Ollama returned an error: {e}. Check that the model "
            f"'{config.OLLAMA_MODEL}' appears in `ollama list`."
        ) from e
    return resp.json()["message"]["content"]
