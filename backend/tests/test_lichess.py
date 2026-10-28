import httpx
import pytest

from app import lichess


def client(responses):
    seen = []

    def handler(request):
        seen.append(request)
        return responses.pop(0)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_429_pauses_a_full_minute_then_streams(monkeypatch):
    sleeps = []
    monkeypatch.setattr(lichess.time, "sleep", sleeps.append)
    c, seen = client([httpx.Response(429), httpx.Response(200, text='{"id":"a"}\n\n{"id":"b"}\n')])
    games = list(lichess.stream_games("someone", 123, 10, client=c))
    assert [g["id"] for g in games] == ["a", "b"]
    assert sleeps == [60]
    assert seen[1].url.params["since"] == "123" and seen[1].url.params["max"] == "10"
    assert "ChessMind-AI" in seen[1].headers["user-agent"]


def test_unknown_user_raises():
    c, _ = client([httpx.Response(404)])
    with pytest.raises(lichess.UserNotFound):
        list(lichess.stream_games("nobody", None, 10, client=c))
