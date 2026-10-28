"""Server-rendered UI over the JSON API. Boards are SVG drawn by python-chess, so the page
needs no JavaScript build and no CDN."""

from pathlib import Path

import chess
import chess.svg
from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, select, tuple_

from app import puzzles, tasks
from app.db import engine, games, jobs, moves, players, reports
from app.grounding import parse_slice_label

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
PLAYED_COLOR = "#d9534f88"
BEST_COLOR = "#2e9d4f88"


def _page(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


async def http_error(request: Request, exc):
    """UI pages get an HTML error page; the JSON API keeps FastAPI's JSON errors."""
    if request.url.path == "/" or request.url.path.startswith("/ui/"):
        return _page(request, "error.html", exc.status_code, detail=exc.detail)
    return await http_exception_handler(request, exc)


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return _page(request, "index.html")


@router.post("/ui/analyse")
def ui_analyse(request: Request, username: str = Form(...)):
    from app import main

    try:
        job_id = main.analyse_player(username.strip())["job_id"]
    except HTTPException as e:
        return _page(request, "index.html", e.status_code, error=e.detail, username=username)
    return RedirectResponse(f"/ui/jobs/{job_id}", status_code=303)


@router.post("/ui/upload")
async def ui_upload(request: Request, file: UploadFile, username: str = Form(...)):
    from app import main

    try:
        out = await main.upload_pgn(file, username.strip())
    except HTTPException as e:
        return _page(request, "index.html", e.status_code, error=e.detail, username=username)
    # Re-uploading already analysed games is fine (the job is simply done); only reject a
    # file that gave this player no games at all.
    if out["games_queued"] == 0 and not _has_games(username.strip().lower()):
        return _page(
            request,
            "index.html",
            422,
            error=f"No new games by {username.strip()} found in that file.",
            username=username,
        )
    return RedirectResponse(f"/ui/jobs/{out['job_id']}", status_code=303)


def _has_games(username: str) -> bool:
    with engine.connect() as conn:
        found = conn.scalar(select(games.c.id).where(tasks.player_games(username)).limit(1))
    return found is not None


@router.get("/ui/jobs/{job_id}", response_class=HTMLResponse)
def ui_job(request: Request, job_id: str):
    with engine.connect() as conn:
        row = conn.execute(
            select(jobs, players.c.username)
            .join(players, players.c.id == jobs.c.player_id)
            .where(jobs.c.id == job_id)
        ).one_or_none()
    if row is None:
        raise HTTPException(404, "job not found")
    return _page(
        request,
        "job.html",
        job=row,
        queue_position=tasks.queue_position(job_id, row.status),
        running=row.status in ("fetching", "analysing"),
    )


@router.post("/ui/players/{username}/report")
def ui_create_report(request: Request, username: str):
    from app import main

    row = main.create_report(username)
    return RedirectResponse(f"/ui/reports/{row['id']}", status_code=303)


def _boards(evidence_ids: list[str]) -> dict[str, str]:
    """Evidence id -> SVG of the position before the move, played move red, engine best green,
    seen from the mover's side."""
    keys = [(g, int(p)) for g, p in (e.split(":") for e in evidence_ids)]
    if not keys:
        return {}
    with engine.connect() as conn:
        rows = conn.execute(select(moves).where(tuple_(moves.c.game_id, moves.c.ply).in_(keys)))
    out = {}
    for m in rows:
        arrows = [chess.svg.Arrow(*_squares(m.uci), color=PLAYED_COLOR)]
        if m.best_uci and m.best_uci != m.uci:
            arrows.append(chess.svg.Arrow(*_squares(m.best_uci), color=BEST_COLOR))
        out[f"{m.game_id}:{m.ply}"] = chess.svg.board(
            chess.Board(m.fen_before),
            arrows=arrows,
            orientation=chess.WHITE if m.color == "white" else chess.BLACK,
            size=300,
        )
    return out


def _squares(uci: str) -> tuple[int, int]:
    mv = chess.Move.from_uci(uci)
    return mv.from_square, mv.to_square


def _rating(username: str) -> int | None:
    """The player's average rating over their analysed games, for picking puzzle difficulty."""
    elo = case((games.c.white == username, games.c.white_elo), else_=games.c.black_elo)
    with engine.connect() as conn:
        avg = conn.scalar(
            select(func.avg(elo)).where(
                tasks.player_games(username), games.c.analysed_at.is_not(None)
            )
        )
    return round(avg) if avg is not None else None


def _puzzle_board(p: dict) -> str:
    """Puzzle position after the opponent's first move, from the solver's side."""
    board = chess.Board(p["fen"])
    first = chess.Move.from_uci(p["moves"][0])
    board.push(first)
    return chess.svg.board(board, lastmove=first, orientation=board.turn, size=200)


@router.get("/ui/reports/{report_id}", response_class=HTMLResponse)
def ui_report(request: Request, report_id: str):
    with engine.connect() as conn:
        row = conn.execute(select(reports).where(reports.c.id == report_id)).one_or_none()
    if row is None:
        raise HTTPException(404, "report not found")
    weaknesses = {w["id"]: w for w in row.facts["weaknesses"]}
    claims = []
    for c in row.report["claims"]:
        w = weaknesses.get(c["weakness"])
        if w is None:
            continue
        examples = {e["id"]: e for e in w["examples"]}
        cited = [examples[e] for e in c["evidence"] if e in examples]
        claims.append({"text": c["text"], "weakness": w, "examples": cited})
    boards = _boards([e["id"] for c in claims for e in c["examples"]])
    rating = _rating(row.username)
    shown: set[str] = set()
    for c in claims:
        sl = parse_slice_label(c["weakness"]["slice"])
        c["title"] = puzzles.display_label(sl["family"], sl["value"], c["weakness"]["slice"])
        c["drill"] = puzzles.drills(sl["family"], sl["value"], rating, exclude=shown)
        shown |= {p["id"] for p in c["drill"]["puzzles"]}
        for p in c["drill"]["puzzles"]:
            boards.setdefault("puzzle:" + p["id"], _puzzle_board(p))
    return _page(
        request,
        "report.html",
        report=row,
        summary=row.report["summary"],
        claims=claims,
        boards=boards,
        rating=rating,
    )
