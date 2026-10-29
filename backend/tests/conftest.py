import os

# Never touch the dev database from tests.
os.environ.setdefault(
    "DATABASE_URL", "postgresql://chessmind:chessmind@localhost:5433/chessmind_test"
)


def pytest_sessionstart(session):
    import psycopg
    from alembic import command
    from alembic.config import Config

    url = os.environ["DATABASE_URL"]
    admin, name = url.rsplit("/", 1)
    try:
        with psycopg.connect(f"{admin}/postgres", autocommit=True, connect_timeout=2) as c:
            if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
                c.execute(f'CREATE DATABASE "{name}"')
    except psycopg.Error:
        return  # DB-backed tests skip themselves
    command.upgrade(Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini")), "head")


class FakeRedis:
    """In-memory stand-in for the few Redis calls metrics and the rate limit make, so tests
    need no Redis server (CI has none)."""

    def __init__(self):
        self.data: dict = {}

    def hincrbyfloat(self, key, field, value):
        h = self.data.setdefault(key, {})
        h[field.encode()] = h.get(field.encode(), 0.0) + value
        return h[field.encode()]

    def hgetall(self, key):
        return {k: str(v).encode() for k, v in self.data.get(key, {}).items()}

    def incr(self, key):
        self.data[key] = self.data.get(key, 0) + 1
        return self.data[key]

    def expire(self, key, seconds):
        return True

    def delete(self, key):
        self.data.pop(key, None)

    def rpush(self, key, *values):
        self.data.setdefault(key, []).extend(v.encode() for v in values)

    def lpop(self, key):
        items = self.data.get(key)
        return items.pop(0) if items else None

    def pipeline(self):
        return _Pipeline(self)


class _Pipeline:
    def __init__(self, r):
        self.r, self.calls = r, []

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name, a))

    def execute(self):
        return [getattr(self.r, n)(*a) for n, a in self.calls]


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    from app import metrics, tasks

    r = FakeRedis()
    monkeypatch.setattr(tasks, "redis", r)
    monkeypatch.setattr(metrics, "_down_until", 0.0)
    return r
