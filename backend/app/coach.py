"""LLM coach. The LLM only rewrites stored facts into coaching language; every report and
chat answer is checked by `grounding` (plain code) before a user sees it."""

import json
import logging
import time
import uuid

import chess
from sqlalchemy import select, tuple_

from app import llm, metrics
from app.config import settings
from app.db import engine, games, moves, reports
from app.grounding import (
    _numbers,
    _sans,
    build_facts,
    check_text,
    template_report,
    validate_report,
    without_examples,
)
from app.weakness import breakdown, player_moves, weaknesses_with_relative

log = logging.getLogger(__name__)
PROMPT_VERSION = "coach-v4"
MAX_REGENERATIONS = 2

RULES = """You are a friendly chess coach writing for a club player. You receive FACTS
computed by a chess engine and a statistical model. Rules you must follow:
- Use ONLY the facts. Copy every number exactly as written in the facts (no rounding,
  no arithmetic, no new numbers, no spelled-out numbers).
- Mention a chess move only if it appears as `played` or `best` in an example you cite.
- Never judge a position or a move yourself; the engine already did.
- win before/win after are the player's winning chances in percent before and after the move.
- games lost equivalent is the excess winning chances lost, summed over games, in units of
  whole games (e.g. 2.5 = about two and a half games' worth of winning chances).
- overall ratio is the player's blunders divided by what peers at their rating would make
  (below 1 = fewer blunders than peers).
- A weakness with compared with "peers" is worse than players at the same rating. One with
  compared with "own level" is NOT worse than peers: its expected blunders is what the player
  would make there at their own overall level, so it is a relative weak spot. Say which.
- If peer data note is present, mention it.
- Write plain English for a person: never quote the facts' field names.
"""

REPORT_TASK = """Write a short report as JSON:
{"summary": "<2 sentences about the player overall>",
 "claims": [{"weakness": "W1", "text": "<1-3 sentences>", "evidence": ["<example id>"]}]}
Write exactly one claim per weakness, citing 1-2 of that weakness's example ids. Each claim
must state the observed and expected blunders and the ratio (copied from the facts), what
the slice means in plain words, and one practical habit to try. The summary must say whether
the player blunders more or fewer times than peers overall."""


def _details(mv, ids: list[str]) -> dict[str, dict]:
    """Evidence id -> what the coach may say about that position."""
    keys = sorted({(g, int(p)) for g, p in (e.split(":") for e in ids)})
    if not keys:
        return {}
    with engine.connect() as conn:
        rows = conn.execute(
            select(moves).where(tuple_(moves.c.game_id, moves.c.ply).in_(keys))
        ).all()
    ctx = mv.set_index(["game_id", "ply"])
    out = {}
    for m in rows:
        board = chess.Board(m.fen_before)
        best = board.san(chess.Move.from_uci(m.best_uci)) if m.best_uci else None
        c = ctx.loc[(m.game_id, m.ply)]
        out[f"{m.game_id}:{m.ply}"] = {
            "move_number": int(c.move_number),
            "played": m.san,
            "best": best,
            "win_before": round(50 + 50 * m.wc_before),
            "win_after": round(50 + 50 * m.wc_after),
            "clock_s": None if c.clock_before != c.clock_before else round(c.clock_before),
        }
    return out


def player_facts(username: str) -> dict | None:
    """Facts for the coach, built from the user's analysed games. None if nothing analysed."""
    mv = player_moves(username)
    if mv.empty:
        return None
    found = weaknesses_with_relative(mv)
    rows = breakdown(mv)
    details = _details(mv, [e for x in found + rows for e in x["evidence"]])
    overall = {
        "observed_blunders": int(mv.blunder.sum()),
        "expected_blunders": float(mv.expected.sum()),
    }
    rating = mv.elo.mean()
    return build_facts(
        int(mv.game_id.nunique()),
        len(mv),
        overall,
        found,
        details,
        rows,
        None if rating != rating else round(rating),
    )


def _for_llm(obj) -> str:
    """JSON for the prompt with readable keys ("observed blunders", not observed_blunders):
    in the live eval, 53 of 61 first-attempt grounding errors were field names copied into
    the text. Values are unchanged, so verification against the stored facts still holds."""

    def readable(o):
        if isinstance(o, dict):
            return {k.replace("_", " "): readable(v) for k, v in o.items()}
        if isinstance(o, list):
            return [readable(v) for v in o]
        return o

    return json.dumps(readable(obj))


def _parse(content: str | None):
    try:
        return json.loads(content or "")
    except json.JSONDecodeError:
        return None


REPORT_EXAMPLES = 2  # claims cite 1-2 examples


def _report_input(facts: dict) -> dict:
    """What the report prompt needs, trimmed to fit Groq's 8k tokens/minute with a
    regeneration: no breakdown examples (the report does not cite them), 2 per weakness."""
    return {
        **facts,
        "weaknesses": [
            {**w, "examples": w["examples"][:REPORT_EXAMPLES]} for w in facts["weaknesses"]
        ],
        "breakdown": without_examples(facts.get("breakdown", [])),
    }


def write_report(facts: dict, chat=llm.chat) -> dict:
    """Generate, verify, regenerate with feedback, else fall back to the template."""
    if not facts["weaknesses"]:
        return {
            "report": template_report(facts),
            "method": "template",
            "attempts": 0,
            "verification": [],
            "tokens": 0,
            "reason": "no weaknesses",
        }
    messages = [
        {"role": "system", "content": RULES},
        {"role": "user", "content": REPORT_TASK + "\nFACTS:\n" + _for_llm(_report_input(facts))},
    ]
    log_, tokens, reason = [], 0, "grounding failed"
    for attempt in range(1 + MAX_REGENERATIONS):
        try:
            msg, usage = chat(messages, json_mode=True, max_wait=llm.REPORT_MAX_WAIT)
        except llm.LLMError as e:
            reason = f"llm error: {e}"
            break
        tokens += usage.get("total_tokens", 0)
        report = _parse(msg.get("content"))
        errors = validate_report(facts, report) if report is not None else ["not valid JSON"]
        log_.append(errors)
        metrics.inc("grounding_checks", kind="report", result="fail" if errors else "pass")
        log.info("report attempt %d grounding errors=%d", attempt + 1, len(errors))
        if not errors:
            return {
                "report": report,
                "method": "llm",
                "attempts": attempt + 1,
                "verification": log_,
                "tokens": tokens,
            }
        messages += [
            {"role": "assistant", "content": msg.get("content") or ""},
            {
                "role": "user",
                "content": "Your JSON broke the rules:\n- "
                + "\n- ".join(errors[:10])
                + "\nReturn the corrected JSON only.",
            },
        ]
    return {
        "report": template_report(facts),
        "method": "template",
        "attempts": len(log_),
        "verification": log_,
        "tokens": tokens,
        "reason": reason,
    }


def generate_report(username: str, report_id: str | None = None) -> dict | None:
    t = time.perf_counter()
    facts = player_facts(username)
    if facts is None:
        return None
    result = write_report(facts)
    with engine.connect() as conn:
        g = conn.execute(
            select(games.c.engine, games.c.engine_nodes)
            .where(games.c.analysed_at.is_not(None))
            .limit(1)
        ).first()
    row = {
        "id": report_id or str(uuid.uuid4()),
        "username": username,
        "method": result["method"],
        "attempts": result["attempts"],
        "llm_model": settings.groq_model if result["attempts"] else None,
        "prompt_version": PROMPT_VERSION,
        "engine": g.engine if g else None,
        "engine_nodes": g.engine_nodes if g else None,
        "verification": result["verification"],
        "facts": facts,
        "report": result["report"],
        "total_tokens": result["tokens"],
    }
    with engine.begin() as conn:
        conn.execute(reports.insert().values(row))
    metrics.inc("reports", method=result["method"])
    metrics.observe("report_seconds", time.perf_counter() - t)
    log.info(
        "report written",
        extra={
            "method": result["method"],
            "attempts": result["attempts"],
            "tokens": result["tokens"],
        },
    )
    return row


# ---- chat ----

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_overview",
            "description": "Games, moves and overall blunder counts.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weaknesses",
            "description": "The player's ranked weaknesses (no examples).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_breakdown_examples",
            "description": "The player's worst blunders in one breakdown area (e.g. "
            "'game phase: endgame'): move played, engine's best move, winning chances, clock.",
            "parameters": {
                "type": "object",
                "properties": {"slice": {"type": "string"}},
                "required": ["slice"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_examples",
            "description": "Example positions for one weakness: move played, engine's best move, "
            "winning chances before/after, clock.",
            "parameters": {
                "type": "object",
                "properties": {"weakness": {"type": "string"}},
                "required": ["weakness"],
            },
        },
    },
]

CHAT_RULES = (
    RULES
    + """
Answer questions about this player's analysed games from FACTS below. `breakdown` gives
blunder rates by game phase, time left and time control for every player, so use it for
questions about an area that is not a weakness. Call get_examples or get_breakdown_examples
(at most once or twice) only when you need example positions. If asked for analysis the
tools cannot provide (for example "what is the best move here?" for a position not in the
examples, or general chess theory), say you
can only show the engine's stored results for their games. Keep answers under 120 words."""
)

MAX_TOOL_ROUNDS = 3
CHAT_REGENERATIONS = 1
FALLBACK_ANSWER = (
    "I could not produce an answer I can verify against your analysed games. "
    "Try asking about one of your listed weaknesses."
)


def _run_tool(facts: dict, name: str, args: dict):
    if name == "get_overview":
        return {k: v for k, v in facts.items() if k not in ("weaknesses", "breakdown")}
    if name == "get_weaknesses":
        return without_examples(facts["weaknesses"])
    if name == "get_breakdown":
        return without_examples(facts.get("breakdown", []))
    if name == "get_breakdown_examples":
        b = next((b for b in facts.get("breakdown", []) if b["slice"] == args.get("slice")), None)
        return b["examples"] if b else {"error": "unknown breakdown slice"}
    if name == "get_examples":
        w = next((w for w in facts["weaknesses"] if w["id"] == args.get("weakness")), None)
        return w["examples"] if w else {"error": "unknown weakness id"}
    return {"error": f"unknown tool {name}"}


def answer(facts: dict, history: list[dict], chat=llm.chat) -> dict:
    """history: [{"role": "user"|"assistant", "content": str}], last item is the question."""
    # Overview, weakness list and breakdown are small, so give them up front; the model then
    # only needs a tool call for example positions. They count as allowed facts for
    # verification.
    given = {
        "overview": _run_tool(facts, "get_overview", {}),
        "weaknesses": _run_tool(facts, "get_weaknesses", {}),
        "breakdown": _run_tool(facts, "get_breakdown", {}),
    }
    system = CHAT_RULES + "\nFACTS:\n" + _for_llm(given)
    messages = [{"role": "system", "content": system}] + history
    outputs, tokens, errors = [given], 0, []
    try:
        for attempt in range(1 + CHAT_REGENERATIONS):
            for rnd in range(MAX_TOOL_ROUNDS + 1):
                # Last round forbids tool calls, which forces a written answer.
                choice = "auto" if rnd < MAX_TOOL_ROUNDS else "none"
                msg, usage = chat(messages, tools=TOOLS, tool_choice=choice)
                tokens += usage.get("total_tokens", 0)
                calls = msg.get("tool_calls") or []
                if not calls:
                    break
                messages.append(
                    {"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls}
                )
                for c in calls:
                    out = _run_tool(
                        facts, c["function"]["name"], _parse(c["function"].get("arguments")) or {}
                    )
                    outputs.append(out)
                    messages.append(
                        {"role": "tool", "tool_call_id": c["id"], "content": _for_llm(out)}
                    )
            text = msg.get("content") or ""
            errors = check_text(text, _numbers(outputs), _sans(outputs)) if text else ["empty"]
            metrics.inc("grounding_checks", kind="chat", result="fail" if errors else "pass")
            if not errors:
                return {"answer": text, "grounded": True, "errors": [], "tokens": tokens}
            log.info("chat attempt %d failed grounding: %s", attempt + 1, errors)
            messages += [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": "That answer broke the rules: "
                    + "; ".join(errors)
                    + ". Answer again using only numbers and moves copied from tool results.",
                },
            ]
    except llm.LLMError as e:
        errors = [str(e)]
    return {"answer": FALLBACK_ANSWER, "grounded": False, "errors": errors, "tokens": tokens}
