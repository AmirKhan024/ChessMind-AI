"""Prometheus metrics shared by the API and every RQ worker.

Workers are separate short-lived processes, so in-process prometheus_client counters would
be lost or split per process. Instead each process adds to Redis hashes, and the API's
/metrics endpoint turns them into Prometheus counters and histograms at scrape time. Queue
depth is read straight from RQ. Metric updates never raise: losing a sample is better than
failing a user's job. After a Redis error, metrics (and the API rate limit) skip Redis for
BREAKER_SECONDS: in the Redis-outage test every page otherwise took ~8 s waiting on it."""

import logging
import time

from prometheus_client.core import (
    CounterMetricFamily,
    GaugeMetricFamily,
    HistogramMetricFamily,
)
from prometheus_client.registry import Collector
from redis import Redis, RedisError

log = logging.getLogger(__name__)
COUNTERS = "metrics:counters"
HISTOGRAMS = "metrics:histograms"
SEP = "|"

DESCRIPTIONS = {
    "positions_analysed": "Positions evaluated by Stockfish (cache misses)",
    "positions_cached": "Positions answered from the position cache",
    "games_analysed": "Games fully analysed",
    "games_skipped": "Games given up on after repeated failures (job continues)",
    "lichess_429": "HTTP 429 responses from Lichess",
    "lichess_games_fetched": "Games downloaded from Lichess",
    "llm_requests": "Groq chat completion calls, by outcome",
    "llm_tokens": "Groq tokens used",
    "grounding_checks": "Coach outputs checked by the grounding verifier, by kind and result",
    "reports": "Reports written, by method (llm or template)",
    "jobs_finished": "Analysis jobs finished, by status",
    "rate_limited": "API requests rejected by the per-IP rate limit",
}
BUCKETS = {
    "llm_latency_seconds": (0.5, 1, 2, 4, 8, 15, 30, 60),
    "report_seconds": (1, 2, 5, 10, 20, 40, 80, 160),
    "job_seconds_per_100_games": (30, 60, 120, 240, 480, 900, 1800, 3600),
    "http_request_seconds": (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
}
HELP = {
    "llm_latency_seconds": "Groq call latency",
    "report_seconds": "Time to build and write a report",
    "job_seconds_per_100_games": "Analysis job wall time, scaled to 100 games",
    "http_request_seconds": "API request latency by route",
}


def _labels(labels: dict) -> str:
    return SEP.join(f"{k}={v}" for k, v in sorted(labels.items()))


def _parse(field: str) -> tuple[str, dict]:
    name, *pairs = field.split(SEP)
    return name, dict(p.split("=", 1) for p in pairs)


BREAKER_SECONDS = 30
_down_until = 0.0


def _redis() -> Redis:
    from app.tasks import redis

    return redis


def redis_up() -> bool:
    return time.monotonic() >= _down_until


def redis_failed(e: Exception) -> None:
    global _down_until
    _down_until = time.monotonic() + BREAKER_SECONDS
    log.warning("redis unavailable, skipping metrics for %ss: %s", BREAKER_SECONDS, e)


def inc(name: str, value: float = 1, **labels) -> None:
    if not redis_up():
        return
    try:
        _redis().hincrbyfloat(COUNTERS, SEP.join(filter(None, [name, _labels(labels)])), value)
    except RedisError as e:
        redis_failed(e)


def observe(name: str, value: float, **labels) -> None:
    """Histogram sample: bumps the first bucket >= value (cumulated at scrape), sum, count."""
    base = SEP.join(filter(None, [name, _labels(labels)]))
    bucket = next((b for b in BUCKETS[name] if value <= b), "+Inf")
    if not redis_up():
        return
    try:
        p = _redis().pipeline()
        p.hincrbyfloat(HISTOGRAMS, f"{base}{SEP}le={bucket}", 1)
        p.hincrbyfloat(HISTOGRAMS, f"{base}{SEP}_sum", value)
        p.hincrbyfloat(HISTOGRAMS, f"{base}{SEP}_count", 1)
        p.execute()
    except RedisError as e:
        redis_failed(e)


class RedisCollector(Collector):
    def collect(self):
        if not redis_up():
            return
        r = _redis()
        try:
            counters = r.hgetall(COUNTERS)
            hists = r.hgetall(HISTOGRAMS)
            depth = _queue_depths()
        except RedisError as e:
            redis_failed(e)
            return
        yield from _counters(counters)
        yield from _histograms(hists)
        g = GaugeMetricFamily(
            "chessmind_queue_depth", "Jobs waiting per RQ queue", labels=["queue"]
        )
        for q, n in depth.items():
            g.add_metric([q], n)
        yield g


def _queue_depths() -> dict[str, int]:
    from app.tasks import analysis_queue, lichess_queue, report_queue

    return {q.name: q.count for q in (lichess_queue, analysis_queue, report_queue)}


def _counters(raw: dict) -> list:
    families: dict[str, CounterMetricFamily] = {}
    for field, value in sorted(raw.items()):
        name, labels = _parse(field.decode())
        if name not in DESCRIPTIONS:
            continue
        key = (name, tuple(sorted(labels)))
        fam = families.get(key)
        if fam is None:
            fam = families[key] = CounterMetricFamily(
                f"chessmind_{name}", DESCRIPTIONS[name], labels=sorted(labels)
            )
        fam.add_metric([labels[k] for k in sorted(labels)], float(value))
    return list(families.values())


def _histograms(raw: dict) -> list:
    series: dict[tuple, dict] = {}
    for field, value in raw.items():
        name, *pairs = field.decode().split(SEP)
        if name not in BUCKETS:
            continue
        labels = dict(p.split("=", 1) for p in pairs if "=" in p and not p.startswith("le="))
        s = series.setdefault((name, tuple(sorted(labels.items()))), {"buckets": {}})
        last = pairs[-1]
        if last.startswith("le="):
            s["buckets"][last[3:]] = float(value)
        else:
            s[last] = float(value)
    families: dict[tuple, HistogramMetricFamily] = {}
    for (name, labels), s in sorted(series.items()):
        keys = [k for k, _ in labels]
        fam = families.get((name, tuple(keys)))
        if fam is None:
            fam = families[(name, tuple(keys))] = HistogramMetricFamily(
                f"chessmind_{name}", HELP[name], labels=keys
            )
        total, buckets = 0.0, []
        for b in [*map(str, BUCKETS[name]), "+Inf"]:
            total += s["buckets"].get(b, 0.0)
            buckets.append((b, total))
        fam.add_metric([v for _, v in labels], buckets, s.get("_sum", 0.0))
    return list(families.values())
