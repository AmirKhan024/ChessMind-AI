"""Facts for the coach, the grounding verifier and the deterministic fallback report.

Everything the LLM may say comes from `facts` (built from engine results and the peer
model). The verifier is plain code: every number and every chess move in a claim must
appear in the facts the claim cites. The LLM is told to copy numbers verbatim, and anything
it computes or invents fails verification."""

import json
import re

NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
TOKEN = re.compile(r"[A-Za-z0-9=+#-]+")
SAN = re.compile(r"(?:O-O(?:-O)?|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?)[+#]?")
MAX_CLAIMS = 8


def slice_label(s: dict) -> str:
    names = {
        "clock": "time left",
        "phase": "game phase",
        "piece": "piece moved",
        "speed": "time control",
        "opening": "opening",
        "clock_phase": "time left / phase",
    }
    return f"{names.get(s['family'], s['family'])}: {s['value']}"


def build_facts(
    games: int, moves: int, overall: dict, weaknesses: list[dict], details: dict[str, dict]
) -> dict:
    """details: evidence id -> {move_number, played, best, win_before, win_after, clock_s}."""
    out = []
    for i, w in enumerate(weaknesses, start=1):
        out.append(
            {
                "id": f"W{i}",
                "slice": slice_label(w["slice"]),
                "moves": w["moves"],
                "observed_blunders": w["observed_blunders"],
                "expected_blunders": round(w["expected_blunders"], 1),
                "ratio": round(w["ratio"], 1),
                "confidence_pct": round(w["confidence"] * 100),
                "observed_rate_pct": round(w["observed_rate"] * 100, 1),
                "expected_rate_pct": round(w["expected_rate"] * 100, 1),
                "cost_win_pct": round(w["cost_win_pct"], 1),
                "examples": [{"id": e, **details[e]} for e in w["evidence"] if e in details],
            }
        )
    return {
        "games": games,
        "moves": moves,
        "overall_observed_blunders": overall["observed_blunders"],
        "overall_expected_blunders": round(overall["expected_blunders"], 1),
        "weaknesses": out,
    }


def _numbers(obj) -> set[float]:
    return {round(float(n), 2) for n in NUMBER.findall(json.dumps(obj))}


def _sans(obj) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("played", "best") and isinstance(v, str):
                found.add(v)
            else:
                found |= _sans(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _sans(v)
    return found


def check_text(text: str, nums: set[float], sans: set[str]) -> list[str]:
    """Errors for numbers and chess moves in `text` that are not in the allowed sets."""
    errors = []
    for n in NUMBER.findall(text):
        if round(float(n), 2) not in nums:
            errors.append(f"number {n} is not in the facts")
    for tok in TOKEN.findall(text):
        tok = tok.rstrip("-=")
        if SAN.fullmatch(tok) and tok not in sans:
            errors.append(f"move {tok} is not an example move you were given")
    return errors


def validate_report(facts: dict, report) -> list[str]:
    """All problems with an LLM report; empty list means grounded."""
    if not isinstance(report, dict) or not isinstance(report.get("summary"), str):
        return ["report must be an object with a string `summary` and a list `claims`"]
    claims = report.get("claims")
    if not isinstance(claims, list) or not 0 < len(claims) <= MAX_CLAIMS:
        return [f"`claims` must be a list of 1-{MAX_CLAIMS} items"]
    by_id = {w["id"]: w for w in facts["weaknesses"]}
    top_level = {k: v for k, v in facts.items() if k != "weaknesses"}
    summary_nums = _numbers(top_level) | _numbers(
        [{k: v for k, v in w.items() if k != "examples"} for w in by_id.values()]
    )
    errors = [f"summary: {e}" for e in check_text(report["summary"], summary_nums, set())]
    covered = set()
    for i, c in enumerate(claims):
        where = f"claim {i + 1}"
        w = by_id.get(c.get("weakness")) if isinstance(c, dict) else None
        if w is None or not isinstance(c.get("text"), str):
            errors.append(f"{where}: needs a valid `weakness` id and `text`")
            continue
        covered.add(w["id"])
        cited = c.get("evidence")
        known = {e["id"]: e for e in w["examples"]}
        if not isinstance(cited, list) or not cited or any(e not in known for e in cited):
            errors.append(f"{where}: `evidence` must list ids from {w['id']}'s examples")
            continue
        own = {k: v for k, v in w.items() if k != "examples"}
        nums = _numbers(own) | _numbers(top_level) | _numbers([known[e] for e in cited])
        sans = _sans([known[e] for e in cited])
        errors += [f"{where}: {e}" for e in check_text(c["text"], nums, sans)]
    errors += [f"{w}: no claim covers this weakness" for w in by_id if w not in covered]
    return errors


def template_report(facts: dict) -> dict:
    """Deterministic report built straight from the facts. Always passes validation."""
    claims = []
    for w in facts["weaknesses"]:
        ex = w["examples"][:1]
        text = (
            f"In {w['slice']}, you blundered {w['observed_blunders']} times in {w['moves']} "
            f"moves ({w['observed_rate_pct']}%), while players at your rating would be "
            f"expected to blunder about {w['expected_blunders']} times "
            f"({w['expected_rate_pct']}%). That is {w['ratio']}x the expected rate, "
            f"costing roughly {w['cost_win_pct']} win-probability points."
        )
        claims.append({"weakness": w["id"], "text": text, "evidence": [e["id"] for e in ex]})
    if claims:
        summary = (
            f"Across {facts['games']} games and {facts['moves']} moves you blundered "
            f"{facts['overall_observed_blunders']} times; peers would be expected to "
            f"blunder about {facts['overall_expected_blunders']} times."
        )
    else:
        summary = (
            f"Across {facts['games']} games and {facts['moves']} moves you blundered "
            f"{facts['overall_observed_blunders']} times, about what peers at your rating "
            f"would be expected to ({facts['overall_expected_blunders']}). No recurring "
            f"weakness stood out with enough confidence."
        )
    return {"summary": summary, "claims": claims}
