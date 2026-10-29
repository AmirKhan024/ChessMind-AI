"""Groq chat client (OpenAI-compatible endpoint): timeout, bounded retries, backoff.
The key is read from settings and never logged."""

import logging
import time

import httpx

from app import metrics
from app.config import settings

log = logging.getLogger(__name__)
URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_ATTEMPTS = 3
# Longest sleep for one 429 before giving up (the caller falls back). Chat is interactive;
# reports run in a background job and can wait for Groq's per-minute token budget to reset.
MAX_WAIT = 20
REPORT_MAX_WAIT = 60


class LLMError(Exception):
    pass


def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    json_mode: bool = False,
    tool_choice: str | None = None,
    client: httpx.Client | None = None,
    max_wait: float = MAX_WAIT,
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
        if tool_choice:
            body["tool_choice"] = tool_choice
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
    last = "unknown"
    for attempt in range(MAX_ATTEMPTS):
        t = time.perf_counter()
        try:
            r = (client or httpx).post(
                URL, json=body, headers=headers, timeout=settings.llm_timeout
            )
        except httpx.HTTPError as e:
            last = type(e).__name__
            metrics.inc("llm_requests", outcome="error")
            log.warning("groq %s (attempt %d of %d)", last, attempt + 1, MAX_ATTEMPTS)
        else:
            metrics.observe("llm_latency_seconds", time.perf_counter() - t)
            if r.status_code == 200:
                data = r.json()
                usage = data.get("usage", {})
                metrics.inc("llm_requests", outcome="ok")
                metrics.inc("llm_tokens", usage.get("total_tokens", 0))
                return data["choices"][0]["message"], usage
            last = f"HTTP {r.status_code}"
            metrics.inc("llm_requests", outcome=f"http_{r.status_code}")
            if r.status_code not in (429, 500, 502, 503, 504):
                raise LLMError(f"{last}: {r.text[:200]}")
            # The free tier also caps tokens per day (200k for gpt-oss-120b); a retry within
            # seconds cannot succeed, so fall back at once.
            if r.status_code == 429 and "per day" in r.text:
                metrics.inc("llm_requests", outcome="daily_limit")
                raise LLMError("Groq daily token limit reached")
            wait = min(float(r.headers.get("retry-after", 2 ** (attempt + 1))), max_wait)
            if attempt < MAX_ATTEMPTS - 1:
                log.warning("groq %s, retrying in %.0fs", last, wait)
                time.sleep(wait)
    raise LLMError(f"Groq unavailable after {MAX_ATTEMPTS} attempts ({last})")
