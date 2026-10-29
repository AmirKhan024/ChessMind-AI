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
LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.M)  # "1. " / "2) " at line start
FIELD_NAME = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")  # e.g. observed_blunders
MAX_CLAIMS = 8


SLICE_NAMES = {
    "clock": "time left",
    "phase": "game phase",
    "piece": "piece moved",
    "speed": "time control",
    "opening": "opening",
    "clock_phase": "time left / phase",
}


def slice_label(s: dict) -> str:
    return f"{SLICE_NAMES.get(s['family'], s['family'])}: {s['value']}"


def parse_slice_label(label: str) -> dict:
    """Inverse of slice_label (stored facts keep only the label)."""
    name, value = label.split(": ", 1)
    family = next((f for f, n in SLICE_NAMES.items() if n == name), name)
    return {"family": family, "value": value}


# 3,829 of the 1.07M training moves are by players rated 2700+ (366 at 2900+), so above
# this the peer expectation is mostly extrapolation.
THIN_PEER_RATING = 2700
PEER_DATA_NOTE = (
    "Very few players at your rating are in the peer model's training data, so comparisons "
    "with peers are less certain than for most players."
)


def _example(details: dict, e: str) -> dict:
    return {"id": e, **details[e]}


def build_facts(
    games: int,
    moves: int,
    overall: dict,
    weaknesses: list[dict],
    details: dict[str, dict],
    breakdown: list[dict] = (),
    rating: int | None = None,
) -> dict:
    """details: evidence id -> {move_number, played, best, win_before, win_after, clock_s}."""
    out = []
    for i, w in enumerate(weaknesses, start=1):
        out.append(
            {
                "id": f"W{i}",
                "slice": slice_label(w["slice"]),
                "compared_with": w.get("compared_with", "peers"),
                "moves": w["moves"],
                "observed_blunders": w["observed_blunders"],
                "expected_blunders": round(w["expected_blunders"], 1),
                "ratio": round(w["ratio"], 1),
                "confidence_pct": round(w["confidence"] * 100),
                "observed_rate_pct": round(w["observed_rate"] * 100, 1),
                "expected_rate_pct": round(w["expected_rate"] * 100, 1),
                "games_lost_equivalent": round(w["cost_win_pct"] / 100, 1),
                "examples": [_example(details, e) for e in w["evidence"] if e in details],
            }
        )
    o, e = overall["observed_blunders"], overall["expected_blunders"]
    facts = {
        "games": games,
        "moves": moves,
        "overall_observed_blunders": o,
        "overall_expected_blunders": round(e, 1),
        "overall_ratio": round(o / e, 2) if e else None,
        "weaknesses": out,
        "breakdown": [
            {
                "slice": slice_label(b["slice"]),
                "moves": b["moves"],
                "blunders": b["observed_blunders"],
                "rate_pct": round(b["observed_rate"] * 100, 1),
                "peer_rate_pct": round(b["expected_rate"] * 100, 1),
                "ratio_vs_peers": None if b["ratio"] is None else round(b["ratio"], 2),
                "examples": [_example(details, x) for x in b["evidence"] if x in details],
            }
            for b in breakdown
        ],
    }
    if rating is not None and rating >= THIN_PEER_RATING:
        facts["peer_data_note"] = PEER_DATA_NOTE
    return facts


def without_examples(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k != "examples"} for r in rows]


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
    text = LIST_MARKER.sub("", text)
    for n in NUMBER.findall(text):
        if round(float(n), 2) not in nums:
            errors.append(f"number {n} is not in the facts")
    for tok in TOKEN.findall(text):
        tok = tok.rstrip("-=")
        if SAN.fullmatch(tok) and tok not in sans:
            errors.append(f"move {tok} is not an example move you were given")
    for name in sorted(set(FIELD_NAME.findall(text))):
        errors.append(f"write plain English instead of the field name {name}")
    return errors


def validate_report(facts: dict, report) -> list[str]:
    """All problems with an LLM report; empty list means grounded."""
    if not isinstance(report, dict) or not isinstance(report.get("summary"), str):
        return ["report must be an object with a string `summary` and a list `claims`"]
    claims = report.get("claims")
    if not isinstance(claims, list) or not 0 < len(claims) <= MAX_CLAIMS:
        return [f"`claims` must be a list of 1-{MAX_CLAIMS} items"]
    by_id = {w["id"]: w for w in facts["weaknesses"]}
    top_level = {k: v for k, v in facts.items() if k not in ("weaknesses", "breakdown")}
    summary_nums = (
        _numbers(top_level)
        | _numbers(without_examples(facts["weaknesses"]))
        | _numbers(without_examples(facts.get("breakdown", [])))
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
        if not _numbers(LIST_MARKER.sub("", c["text"])) & _numbers(own):
            errors.append(f"{where}: must quote numbers from {w['id']} (observed, expected, ratio)")
    errors += [f"{w}: no claim covers this weakness" for w in by_id if w not in covered]
    return errors


def template_report(facts: dict) -> dict:
    """Deterministic report built straight from the facts. Always passes validation."""
    claims = []
    for w in facts["weaknesses"]:
        ex = w["examples"][:1]
        if w.get("compared_with") == "own level":
            text = (
                f"In {w['slice']}, you blundered {w['observed_blunders']} times in {w['moves']} "
                f"moves ({w['observed_rate_pct']}%). Given how you play elsewhere, about "
                f"{w['expected_blunders']} would be expected ({w['expected_rate_pct']}%), so "
                f"this is {w['ratio']}x your usual rate: one of your relatively weakest areas, "
                f"costing about {w['games_lost_equivalent']} games' worth of winning chances."
            )
        else:
            text = (
                f"In {w['slice']}, you blundered {w['observed_blunders']} times in {w['moves']} "
                f"moves ({w['observed_rate_pct']}%), while players at your rating would be "
                f"expected to blunder about {w['expected_blunders']} times "
                f"({w['expected_rate_pct']}%). That is {w['ratio']}x the expected rate, "
                f"costing about {w['games_lost_equivalent']} games' worth of winning chances."
            )
        claims.append({"weakness": w["id"], "text": text, "evidence": [e["id"] for e in ex]})
    o, e = facts["overall_observed_blunders"], facts["overall_expected_blunders"]
    ratio = facts.get("overall_ratio")
    summary = f"Across {facts['games']} games and {facts['moves']} moves you blundered {o} times"
    if ratio is not None and ratio < 0.9:
        summary += (
            f", fewer than the {e} that players at your rating would be expected to make "
            f"({ratio}x the peer rate)."
        )
    elif ratio is not None and ratio > 1.1:
        summary += (
            f", more than the {e} that players at your rating would be expected to make "
            f"({ratio}x the peer rate)."
        )
    else:
        summary += f", about the {e} expected for players at your rating."
    if not any(w.get("compared_with", "peers") == "peers" for w in facts["weaknesses"]):
        summary += (
            " No area stood out as worse than your peers; the areas below are where you lose "
            "most of your usual edge."
            if claims
            else " No recurring weakness stood out with enough confidence; the table below "
            "shows where your blunders happen."
        )
    if "peer_data_note" in facts:
        summary += " " + facts["peer_data_note"]
    return {"summary": summary, "claims": claims}
