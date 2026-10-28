"""Groq chat client (OpenAI-compatible endpoint): timeout, bounded retries, backoff.
The key is read from settings and never logged."""

import logging
import time

import httpx

from app.config import settings

log = logging.getLogger(__name__)
URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_ATTEMPTS = 3
MAX_WAIT = 20  # never sleep longer than this for a 429; give up and let the caller fall back


class LLMError(Exception):
    pass


def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    json_mode: bool = False,
    client: httpx.Client | None = None,
) -> tuple[dict, dict]:
    """Returns (assistant message, usage). Raises LLMError after bounded retries."""
    if not settings.groq_api_key:
        raise LLMError("GROQ_API_KEY is not set")
    body = {
        "model": settings.groq_model,
        "messages": messages,
        "temperature": 0.2,
        "max_completion_tokens": 1500,
        "reasoning_effort": "low",
    }
    if tools:
        body["tools"] = tools
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
    last = "unknown"
    for attempt in range(MAX_ATTEMPTS):
        try:
            r = (client or httpx).post(
                URL, json=body, headers=headers, timeout=settings.llm_timeout
            )
        except httpx.HTTPError as e:
            last = type(e).__name__
        else:
            if r.status_code == 200:
                data = r.json()
                return data["choices"][0]["message"], data.get("usage", {})
            last = f"HTTP {r.status_code}"
            if r.status_code not in (429, 500, 502, 503, 504):
                raise LLMError(last)
            wait = min(float(r.headers.get("retry-after", 2 ** (attempt + 1))), MAX_WAIT)
            if attempt < MAX_ATTEMPTS - 1:
                log.warning("groq %s, retrying in %.0fs", last, wait)
                time.sleep(wait)
    raise LLMError(f"Groq unavailable after {MAX_ATTEMPTS} attempts ({last})")
