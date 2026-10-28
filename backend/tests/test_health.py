from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


def test_health_ok(monkeypatch):
    monkeypatch.setattr(main, "_check_db", lambda: True)
    monkeypatch.setattr(main, "_check_redis", lambda: True)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": True, "redis": True}


def test_health_degraded_when_dependency_down(monkeypatch):
    monkeypatch.setattr(main, "_check_db", lambda: True)
    monkeypatch.setattr(main, "_check_redis", lambda: False)
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"
