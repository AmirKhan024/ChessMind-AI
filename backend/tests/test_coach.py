import json
from pathlib import Path

from app import coach, llm

FACTS = json.loads((Path(__file__).parent / "fixtures" / "coach" / "facts.json").read_text())
GOOD = json.dumps(
    {
        "summary": "Over 50 games you blundered 135 times.",
        "claims": [
            {
                "weakness": "W1",
                "evidence": ["abcd1234:61"],
                "text": "Under 10s you blundered 31 times; Qxb7 dropped you from 62 to 18.",
            },
            {
                "weakness": "W2",
                "evidence": ["ijkl9012:80"],
                "text": "In endgames your rate is 10.0% against 6.8%.",
            },
        ],
    }
)
FABRICATED = GOOD.replace("31 times", "37 times")


def scripted(*contents):
    """Fake Groq: returns the given contents in order (an Exception is raised instead)."""
    calls = []

    def chat(messages, **kw):
        calls.append(messages)
        c = contents[len(calls) - 1]
        if isinstance(c, Exception):
            raise c
        return (c if isinstance(c, dict) else {"content": c}), {"total_tokens": 10}

    return chat, calls


def test_grounded_first_try():
    chat, calls = scripted(GOOD)
    r = coach.write_report(FACTS, chat)
    assert (r["method"], r["attempts"], r["verification"]) == ("llm", 1, [[]])


def test_regenerates_with_feedback_then_passes():
    chat, calls = scripted(FABRICATED, GOOD)
    r = coach.write_report(FACTS, chat)
    assert (r["method"], r["attempts"]) == ("llm", 2)
    assert "number 37" in calls[1][-1]["content"]  # the error was fed back


def test_falls_back_to_template_after_repeated_failures():
    chat, calls = scripted(FABRICATED, "not json", FABRICATED)
    r = coach.write_report(FACTS, chat)
    assert (r["method"], r["attempts"]) == ("template", 3)
    assert "31 times" in r["report"]["claims"][0]["text"]


def test_falls_back_when_groq_is_down():
    chat, _ = scripted(llm.LLMError("timeout"))
    r = coach.write_report(FACTS, chat)
    assert r["method"] == "template" and r["reason"].startswith("llm error")


def tool_call(name, args="{}"):
    return {
        "content": "",
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": name, "arguments": args}}
        ],
    }


def test_chat_answer_grounded_in_tool_output():
    chat, calls = scripted(
        tool_call("get_examples", '{"weakness": "W1"}'),
        "On move 31 you played Qxb7; the engine preferred Nf3.",
    )
    out = coach.answer(FACTS, [{"role": "user", "content": "Show me a time-trouble example"}], chat)
    assert out["grounded"] and "Qxb7" in out["answer"]
    assert calls[1][-1]["role"] == "tool"


def test_chat_blocks_numbers_not_from_tools():
    chat, _ = scripted("You blunder 42% of the time; play Bc4 instead.")
    out = coach.answer(FACTS, [{"role": "user", "content": "how bad am I"}], chat)
    assert not out["grounded"] and out["answer"] == coach.FALLBACK_ANSWER
