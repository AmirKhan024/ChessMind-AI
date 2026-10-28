import copy
import json
from pathlib import Path

import pytest

from app.grounding import template_report, validate_report

FACTS = json.loads((Path(__file__).parent / "fixtures" / "coach" / "facts.json").read_text())

GOOD = {
    "summary": "Over 50 games you blundered 135 times, versus about 118.2 expected.",
    "claims": [
        {
            "weakness": "W1",
            "evidence": ["abcd1234:61"],
            "text": "With under 10s left you blundered 31 times against 14.6 expected (2.1x). "
            "On move 31 you played Qxb7 and your winning chances fell from 62 to 18.",
        },
        {
            "weakness": "W2",
            "evidence": ["ijkl9012:80"],
            "text": "In endgames the rate is 10.0% against 6.8% expected; on move 40, Kg3 was the "
            "mistake and the engine preferred Kf2.",
        },
    ],
}


def bad(mutate):
    r = copy.deepcopy(GOOD)
    mutate(r)
    return validate_report(FACTS, r)


def test_grounded_report_passes():
    assert validate_report(FACTS, GOOD) == []


def test_template_report_always_passes():
    assert validate_report(FACTS, template_report(FACTS)) == []
    empty = {**FACTS, "weaknesses": []}
    assert "No recurring weakness" in template_report(empty)["summary"]


@pytest.mark.parametrize(
    "mutate, expect",
    [
        (
            lambda r: r["claims"][0].update(text="You blundered 33 times with under 10s left."),
            "number 33",
        ),
        (lambda r: r["claims"][0].update(text="You played Qxb7 but Bc4 was best."), "move Bc4"),
        (lambda r: r["claims"][0].update(evidence=["nope:1"]), "evidence"),
        (lambda r: r["claims"][0].update(evidence=["ijkl9012:80"]), "evidence"),
        (lambda r: r["claims"][0].update(evidence=[]), "evidence"),
        (lambda r: r["claims"][0].update(text="After Kg3 you lost."), "move Kg3"),
        (lambda r: r["claims"].pop(), "W2: no claim"),
        (lambda r: r.update(summary="You blundered 140 times."), "summary: number 140"),
        (lambda r: r["claims"][1].update(text="That is about 3.4% worse."), "number 3.4"),
    ],
)
def test_bad_reports_are_caught(mutate, expect):
    errors = bad(mutate)
    assert any(expect in e for e in errors), errors


def test_numbers_inside_move_names_are_not_numbers():
    r = copy.deepcopy(GOOD)
    r["claims"][0]["text"] = "You played Qxb7 and Nf3 was best, with under 10s left."
    assert validate_report(FACTS, r) == []


def test_malformed_output_is_rejected():
    assert validate_report(FACTS, "not json")
    assert validate_report(FACTS, {"summary": "x", "claims": []})
