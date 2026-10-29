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


def test_list_markers_are_not_numbers():
    from app.grounding import check_text

    assert check_text("1. You blundered 31 times\n2) Kg3 lost", {31.0}, {"Kg3"}) == []


def test_template_says_fewer_or_more_than_peers():
    from app.grounding import PEER_DATA_NOTE

    better = {**FACTS, "weaknesses": [], "overall_expected_blunders": 241.0, "overall_ratio": 0.56}
    s = template_report(better)["summary"]
    assert "fewer than the 241.0" in s and "0.56x" in s
    worse = {**FACTS, "overall_ratio": 1.3}
    assert "more than the 118.2" in template_report(worse)["summary"]
    note = {**FACTS, "overall_ratio": 0.56, "peer_data_note": PEER_DATA_NOTE}
    assert validate_report(note, template_report(note)) == []


def test_relative_weak_spot_template_wording():
    rel = copy.deepcopy(FACTS)
    rel["weaknesses"][0]["compared_with"] = "own level"
    r = template_report(rel)
    assert "your usual rate" in r["claims"][0]["text"]
    assert validate_report(rel, r) == []


def test_summary_may_quote_breakdown_but_not_field_names():
    r = copy.deepcopy(GOOD)
    r["summary"] = "In endgames you blunder 10.0% of the time against 6.8% for peers."
    assert validate_report(FACTS, r) == []
    r["summary"] = "Your overall_ratio is 1.14."
    assert any("field name overall_ratio" in e for e in validate_report(FACTS, r))
