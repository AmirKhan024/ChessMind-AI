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
