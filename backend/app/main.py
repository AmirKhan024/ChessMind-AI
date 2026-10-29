import logging
import time
import uuid

import psycopg
import redis
from fastapi import FastAPI, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import coach, logs, metrics, tasks, web
from app.config import settings
from app.db import engine, games, jobs, players, reports
from app.games import USERNAME_RE
from app.weakness import breakdown, player_moves, weaknesses_with_relative

logs.setup()
log = logging.getLogger("app.http")
app = FastAPI(title="ChessMind AI")
app.include_router(web.router)
app.add_exception_handler(StarletteHTTPException, web.http_error)
REGISTRY.register(metrics.RedisCollector())
MAX_UPLOAD_BYTES = 5_000_000
# Per client IP per minute. Analysis costs minutes of CPU; reports and chat cost LLM quota.
RATE_LIMITS = {"analyse": 6, "report": 6, "chat": 20, "delete": 6}


def _rate_group(method: str, path: str) -> str | None:
    if method == "DELETE":
        return "delete"
    if method != "POST":
        return None
    if path.endswith("/chat"):
        return "chat"
    if path.endswith("/report"):
        return "report"
    if path.endswith(("/analyse", "/upload")):
        return "analyse"
    return None


def _over_limit(group: str, ip: str) -> bool:
    # Fail open: Redis trouble should not lock everyone out (or slow every request).
    if not metrics.redis_up():
        return False
    key = f"ratelimit:{group}:{ip}:{int(time.time() // 60)}"
    try:
        p = tasks.redis.pipeline()
        p.incr(key)
        p.expire(key, 60)
        n, _ = p.execute()
    except redis.RedisError as e:
        metrics.redis_failed(e)
        return False
    return n > RATE_LIMITS[group]


async def queue_unavailable(request: Request, exc: redis.RedisError):
    """Redis down: enqueueing fails. Say so (503) instead of an unhandled 500."""
    log.error("redis unavailable: %s", exc)
    return await web.http_error(
        request,
        StarletteHTTPException(503, "The job queue is unavailable. Please try again shortly."),
    )


app.add_exception_handler(redis.RedisError, queue_unavailable)


@app.middleware("http")
async def observe(request: Request, call_next):
    """Rate limit, then one JSON log line and one latency sample per request."""
    t = time.perf_counter()
    ip = request.client.host if request.client else "unknown"
    group = settings.rate_limit and _rate_group(request.method, request.url.path)
    if group and _over_limit(group, ip):
        metrics.inc("rate_limited", group=group)
        response = await web.http_error(
            request, StarletteHTTPException(429, "Too many requests. Please wait a minute.")
        )
    else:
        response = await call_next(request)
    route = getattr(request.scope.get("route"), "path", "unmatched")
    seconds = time.perf_counter() - t
    if route != "/metrics":
        metrics.observe("http_request_seconds", seconds, route=route, method=request.method)
        log.info(
            "request",
            extra={
                "method": request.method,
                "route": route,
                "status": response.status_code,
                "ms": round(seconds * 1000, 1),
            },
        )
    return response


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics() -> Response:
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


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
        "games_skipped": row.skipped,
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
        "weaknesses": weaknesses_with_relative(mv),
        "breakdown": breakdown(mv),
    }


def _username(username: str) -> str:
    if not USERNAME_RE.match(username):
        raise HTTPException(422, "invalid username")
    return username.lower()


@app.post("/players/{username}/report", status_code=202)
def create_report(username: str) -> dict:
    """Queues the report (building facts takes ~20 s for 300 games, plus LLM calls);
    poll GET /reports/{id}."""
    username = _username(username)
    with engine.connect() as conn:
        analysed = conn.scalar(
            select(games.c.id)
            .where(tasks.player_games(username), games.c.analysed_at.is_not(None))
            .limit(1)
        )
    if analysed is None:
        raise HTTPException(404, "no analysed games for this player")
    report_id = str(uuid.uuid4())
    tasks.enqueue_report(username, report_id)
    return {"report_id": report_id, "status": "queued"}


@app.get("/reports/{report_id}")
def get_report(report_id: str):
    with engine.connect() as conn:
        row = conn.execute(select(reports).where(reports.c.id == report_id)).one_or_none()
    if row is not None:
        return dict(row._mapping)
    status = tasks.report_status(report_id)
    if status is None:
        raise HTTPException(404, "report not found")
    if status == "failed":
        raise HTTPException(500, "writing the report failed")
    return JSONResponse({"status": status}, status_code=202)


@app.delete("/players/{username}")
def delete_player(username: str) -> dict:
    """Delete everything stored about a player: games they played (with those games' moves),
    jobs and reports. Cached engine evaluations of positions are not personal and stay."""
    username = _username(username)
    with engine.begin() as conn:
        n_games = conn.execute(delete(games).where(tasks.player_games(username))).rowcount
        n_reports = conn.execute(delete(reports).where(reports.c.username == username)).rowcount
        conn.execute(delete(players).where(players.c.username == username))  # jobs cascade
    return {"username": username, "games_deleted": n_games, "reports_deleted": n_reports}


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=1000)


class ChatRequest(BaseModel):
    messages: list[Turn] = Field(min_length=1, max_length=12)
    report_id: uuid.UUID | None = None


def _report_facts(username: str, report_id: uuid.UUID | None) -> dict | None:
    """Facts stored with the report the user is reading, so chat answers match that page
    and skip re-parsing every game (~20 s for 300 games). Reports from before the
    breakdown existed are rebuilt."""
    if report_id is None:
        return None
    with engine.connect() as conn:
        facts = conn.scalar(
            select(reports.c.facts).where(
                reports.c.id == str(report_id), reports.c.username == username
            )
        )
    return facts if facts and "breakdown" in facts else None


@app.post("/players/{username}/chat")
def chat(username: str, body: ChatRequest) -> dict:
    username = _username(username)
    facts = _report_facts(username, body.report_id) or coach.player_facts(username)
    if facts is None:
        raise HTTPException(404, "no analysed games for this player")
    out = coach.answer(facts, [t.model_dump() for t in body.messages])
    return {"answer": out["answer"], "grounded": out["grounded"]}
