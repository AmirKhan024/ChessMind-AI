import uuid

import psycopg
import redis
from fastapi import FastAPI, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app import coach, tasks, web
from app.config import settings
from app.db import engine, jobs, players, reports
from app.games import USERNAME_RE
from app.weakness import find_weaknesses, player_moves

app = FastAPI(title="ChessMind AI")
app.include_router(web.router)
MAX_UPLOAD_BYTES = 5_000_000


def _check_db() -> bool:
    try:
        with psycopg.connect(settings.database_url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except psycopg.Error:
        return False


def _check_redis() -> bool:
    try:
        return bool(redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2).ping())
    except redis.RedisError:
        return False


@app.get("/health")
def health(response: Response) -> dict:
    checks = {"db": _check_db(), "redis": _check_redis()}
    ok = all(checks.values())
    if not ok:
        response.status_code = 503
    return {"status": "ok" if ok else "degraded", **checks}


def _new_job(username: str, status: str) -> tuple[str, int]:
    if not USERNAME_RE.match(username):
        raise HTTPException(422, "invalid username")
    job_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(insert(players).values(username=username.lower()).on_conflict_do_nothing())
        pid = conn.scalar(select(players.c.id).where(players.c.username == username.lower()))
        conn.execute(insert(jobs).values(id=job_id, player_id=pid, status=status))
    return job_id, pid


@app.post("/players/{username}/analyse", status_code=202)
def analyse_player(username: str) -> dict:
    job_id, pid = _new_job(username, "fetching")
    # Allows for a few 60 s rate-limit pauses plus the stream itself.
    tasks.enqueue(
        tasks.lichess_queue,
        tasks.fetch_player_games,
        job_id,
        job_id,
        pid,
        username.lower(),
        timeout=600,
    )
    return {"job_id": job_id}


@app.post("/upload", status_code=202)
async def upload_pgn(file: UploadFile, username: str = Form(...)) -> dict:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "PGN file too large")
    job_id, pid = _new_job(username, "analysing")
    text = data.decode("utf-8", "replace")
    added = tasks.save_games(username.lower(), [text], "upload")
    queued = tasks.enqueue_analysis(job_id, username.lower())
    return {"job_id": job_id, "games_added": added, "games_queued": queued}


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(select(jobs).where(jobs.c.id == job_id)).one_or_none()
    if row is None:
        raise HTTPException(404, "job not found")
    hit_rate = row.cache_hits / row.positions if row.positions else None
    return {
        "status": row.status,
        "games_done": row.done,
        "games_total": row.total,
        "positions": row.positions,
        "cache_hit_rate": hit_rate,
        "queue_position": tasks.queue_position(job_id, row.status),
        "error": row.error,
    }


@app.get("/players/{username}/weaknesses")
def weaknesses(username: str) -> dict:
    if not USERNAME_RE.match(username):
        raise HTTPException(422, "invalid username")
    mv = player_moves(username.lower())
    if mv.empty:
        raise HTTPException(404, "no analysed games for this player")
    return {
        "username": username.lower(),
        "games": int(mv.game_id.nunique()),
        "moves": len(mv),
        "overall": {
            "observed_blunders": int(mv.blunder.sum()),
            "expected_blunders": round(float(mv.expected.sum()), 2),
        },
        "weaknesses": find_weaknesses(mv),
    }


def _username(username: str) -> str:
    if not USERNAME_RE.match(username):
        raise HTTPException(422, "invalid username")
    return username.lower()


@app.post("/players/{username}/report")
def create_report(username: str) -> dict:
    row = coach.generate_report(_username(username))
    if row is None:
        raise HTTPException(404, "no analysed games for this player")
    return {k: row[k] for k in ("id", "method", "attempts", "report", "facts")}


@app.get("/reports/{report_id}")
def get_report(report_id: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(select(reports).where(reports.c.id == report_id)).one_or_none()
    if row is None:
        raise HTTPException(404, "report not found")
    return dict(row._mapping)


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=1000)


class ChatRequest(BaseModel):
    messages: list[Turn] = Field(min_length=1, max_length=12)


@app.post("/players/{username}/chat")
def chat(username: str, body: ChatRequest) -> dict:
    facts = coach.player_facts(_username(username))
    if facts is None:
        raise HTTPException(404, "no analysed games for this player")
    out = coach.answer(facts, [t.model_dump() for t in body.messages])
    return {"answer": out["answer"], "grounded": out["grounded"]}
