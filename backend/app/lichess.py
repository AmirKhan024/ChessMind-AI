"""Lichess API client. Only called from the single `lichess` queue worker, so requests
are serialized. On 429 we sleep a full minute in that worker, which pauses the queue."""

import json
import logging
import time
from collections.abc import Iterator

import httpx

from app.config import settings

log = logging.getLogger(__name__)
RATE_LIMIT_WAIT = 60
MAX_429_RETRIES = 3


class UserNotFound(Exception):
    pass


def stream_games(
    username: str, since_ms: int | None, max_games: int, client: httpx.Client | None = None
) -> Iterator[dict]:
    params = {
        "max": max_games,
        "rated": "true",
        "perfType": "bullet,blitz,rapid,classical",
        "clocks": "true",
        "opening": "true",
        "pgnInJson": "true",
    }
    if since_ms is not None:
        params["since"] = since_ms
    headers = {"Accept": "application/x-ndjson", "User-Agent": settings.user_agent}
    url = f"{settings.lichess_url}/api/games/user/{username}"
    for attempt in range(MAX_429_RETRIES + 1):
        with (client or httpx).stream("GET", url, params=params, headers=headers, timeout=30) as r:
            if r.status_code == 429 and attempt < MAX_429_RETRIES:
                log.warning("lichess 429, pausing queue for %ss", RATE_LIMIT_WAIT)
                time.sleep(RATE_LIMIT_WAIT)
                continue
            if r.status_code == 404:
                raise UserNotFound(username)
            r.raise_for_status()
            for line in r.iter_lines():
                if line.strip():
                    yield json.loads(line)
            return
