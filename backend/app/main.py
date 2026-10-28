import psycopg
import redis
from fastapi import FastAPI, Response

from app.config import settings

app = FastAPI(title="ChessMind AI")


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
