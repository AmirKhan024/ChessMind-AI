"""Grounding eval gate on REAL recorded Groq outputs (recorded by ml/eval_coach.py).
Deterministic: no network. Fails if the verifier's verdict on a recorded output changes, or
if anything that would have been shown to a user is not grounded in the facts."""

import json
from pathlib import Path

import pytest

from app.grounding import SAN, TOKEN, _numbers, _sans, check_text, validate_report

RECORDED = Path(__file__).parent / "fixtures" / "coach" / "recorded.jsonl"
records = (
    [json.loads(x) for x in RECORDED.read_text(encoding="utf-8").splitlines()]
    if (RECORDED.exists())
    else []
)
pytestmark = pytest.mark.skipif(not records, reason="no recorded coach outputs")


def test_verifier_verdicts_are_stable():
    for r in (r for r in records if r["kind"] == "report"):
        assert validate_report(r["facts"], r["response"]) == r["errors"]


def test_grounded_chat_answers_only_use_fact_numbers_and_moves():
    for r in (r for r in records if r["kind"].startswith("chat") and r["grounded"]):
        assert check_text(r["answer"], _numbers(r["facts"]), _sans(r["facts"])) == []


def test_out_of_scope_answers_name_no_moves():
    for r in (r for r in records if r["kind"] == "chat_out_of_scope"):
        moves = [t for t in TOKEN.findall(r["answer"]) if SAN.fullmatch(t.rstrip("-="))]
        assert moves == [], (r["question"], r["answer"])
