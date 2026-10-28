"""Live coach evaluation against Groq (manual, costs free-tier quota).

For each analysed player: generate N reports and ask fixed chat questions. Measures how often
the LLM output passes the grounding verifier, and records every raw LLM response with its
facts to tests/fixtures/coach/recorded.jsonl so CI can replay the verifier on real outputs.
Usage: python -m ml.eval_coach USER [USER ...] [--reports 4] [--pause 20]"""

import argparse
import json
import time
from pathlib import Path

from app import coach, llm
from app.grounding import validate_report

OUT = Path(__file__).parent.parent / "tests" / "fixtures" / "coach" / "recorded.jsonl"
QUESTIONS = [
    ("in_scope", "What is my biggest weakness and can you show me an example?"),
    ("in_scope", "How often do I blunder overall compared to my peers?"),
    (
        "out_of_scope",
        "What is the best move in this position: "
        "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
    ),
    ("out_of_scope", "Which opening should I learn to gain 200 rating points?"),
]


def recording_chat(record: list):
    def chat(messages, **kw):
        msg, usage = llm.chat(messages, **kw)
        record.append(msg)
        return msg, usage

    return chat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("users", nargs="+")
    ap.add_argument("--reports", type=int, default=4)
    ap.add_argument("--pause", type=float, default=20)
    args = ap.parse_args()
    stats = {
        "reports": 0,
        "first_try_grounded": 0,
        "final_llm": 0,
        "final_template": 0,
        "llm_errors": 0,
        "attempts": 0,
        "tokens": 0,
        "latency_s": [],
    }
    chat_stats = []
    with OUT.open("w") as out:
        for user in args.users:
            facts = coach.player_facts(user)
            print(user, "weaknesses:", len(facts["weaknesses"]))
            for _ in range(args.reports):
                msgs: list = []
                t = time.perf_counter()
                r = coach.write_report(facts, recording_chat(msgs))
                stats["latency_s"].append(round(time.perf_counter() - t, 1))
                stats["reports"] += 1
                stats["attempts"] += r["attempts"]
                stats["tokens"] += r["tokens"]
                stats["first_try_grounded"] += r["verification"][:1] == [[]]
                stats["final_" + r["method"]] += 1
                stats["llm_errors"] += r.get("reason", "").startswith("llm error")
                for m in msgs:
                    try:
                        parsed = json.loads(m.get("content") or "")
                    except json.JSONDecodeError:
                        parsed = m.get("content")
                    out.write(
                        json.dumps(
                            {
                                "kind": "report",
                                "facts": facts,
                                "response": parsed,
                                "errors": validate_report(facts, parsed),
                            }
                        )
                        + "\n"
                    )
                print(
                    " report",
                    r["method"],
                    "attempts",
                    r["attempts"],
                    [len(e) for e in r["verification"]],
                    r.get("reason", ""),
                )
                time.sleep(args.pause)
            for kind, q in QUESTIONS:
                a = coach.answer(facts, [{"role": "user", "content": q}])
                chat_stats.append(
                    {
                        "kind": kind,
                        "grounded": a["grounded"],
                        "answer": a["answer"],
                        "errors": a["errors"],
                    }
                )
                out.write(
                    json.dumps({"kind": "chat_" + kind, "facts": facts, "question": q, **a}) + "\n"
                )
                print(" chat", kind, a["grounded"], a["answer"][:160].replace("\n", " "))
                time.sleep(args.pause / 2)
    n = stats["reports"]
    lat = sorted(stats.pop("latency_s"))
    summary = {
        **stats,
        "first_try_pass_rate": stats["first_try_grounded"] / n,
        "final_llm_rate": stats["final_llm"] / n,
        "latency_p50_s": lat[n // 2],
        "latency_max_s": lat[-1],
        "tokens_per_report": stats["tokens"] / n,
        "chat": {
            k: {
                "n": sum(c["kind"] == k for c in chat_stats),
                "grounded": sum(c["grounded"] for c in chat_stats if c["kind"] == k),
            }
            for k in ("in_scope", "out_of_scope")
        },
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
