"""GET /price-history per-server mode: query params and response shape."""
from __future__ import annotations

from fastapi.testclient import TestClient

from main import app


def test_price_history_per_server_forwards_hours_and_last(monkeypatch):
    captured: dict = {}

    async def fake_query_server_history(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("db.writer.query_server_history", fake_query_server_history)

    client = TestClient(app)
    r = client.get(
        "/price-history",
        params={
            "server": "Firemaw",
            "region": "EU",
            "version": "Anniversary",
            "faction": "horde",
            "last": "400",
            "hours": "168",
        },
    )
    assert r.status_code == 200
    assert captured.get("hours") == 168
    assert captured.get("last") == 400
    assert captured.get("server_name") == "Firemaw"
    assert captured.get("region") == "EU"
    assert captured.get("version") == "Anniversary"


def test_price_history_per_server_returns_points_with_best_ask_vwap(monkeypatch):
    async def fake_query_server_history(**kwargs):
        return [
            {
                "recorded_at": "2024-01-15T10:00:00+00:00",
                "index_price": 0.01,
                "index_price_per_1k": 10.0,
                "best_ask": 9.5,
                "vwap": 9.7,
                "sample_size": 4,
            }
        ]

    monkeypatch.setattr("db.writer.query_server_history", fake_query_server_history)

    client = TestClient(app)
    r = client.get(
        "/price-history",
        params={
            "server": "Firemaw",
            "region": "EU",
            "version": "Anniversary",
            "faction": "all",
            "last": "50",
            "hours": "24",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    pt = body["points"][0]
    assert pt["best_ask"] == 9.5
    assert pt["vwap"] == 9.7
    assert pt["index_price_per_1k"] == 10.0


def test_price_history_uses_short_query_when_hours_lte_6(monkeypatch):
    called: list[str] = []

    async def fake_short(**kwargs):
        called.append("short")
        return [
            {
                "recorded_at": "2024-01-15T10:00:00+00:00",
                "index_price": 0.01,
                "index_price_per_1k": 10.0,
                "best_ask": 9.5,
                "vwap": 9.7,
                "sample_size": 4,
            }
        ]

    async def fake_long(**kwargs):
        called.append("long")
        return []

    monkeypatch.setattr("db.writer.query_server_history_short", fake_short)
    monkeypatch.setattr("db.writer.query_server_history", fake_long)

    client = TestClient(app)
    r = client.get(
        "/price-history",
        params={
            "server": "Firemaw",
            "region": "EU",
            "version": "Anniversary",
            "faction": "All",
            "last": "200",
            "hours": "1",
        },
    )
    assert r.status_code == 200
    assert called == ["short"]
    assert r.json()["count"] == 1


def test_price_history_hours_6_uses_short(monkeypatch):
    called: list[str] = []

    async def fake_short(**kwargs):
        called.append("short")
        return []

    async def fake_long(**kwargs):
        called.append("long")
        return []

    monkeypatch.setattr("db.writer.query_server_history_short", fake_short)
    monkeypatch.setattr("db.writer.query_server_history", fake_long)

    client = TestClient(app)
    r = client.get(
        "/price-history",
        params={
            "server": "Firemaw",
            "region": "EU",
            "version": "Anniversary",
            "hours": "6",
            "last": "300",
        },
    )
    assert r.status_code == 200
    assert called == ["short"]


def test_price_history_uses_long_query_when_hours_gt_6(monkeypatch):
    called: list[str] = []

    async def fake_short(**kwargs):
        called.append("short")
        return []

    async def fake_long(**kwargs):
        called.append("long")
        return [{"recorded_at": "2024-01-15T10:00:00+00:00", "index_price": 0.01,
                 "index_price_per_1k": 10.0, "best_ask": 9.5, "vwap": 9.7, "sample_size": 2}]

    monkeypatch.setattr("db.writer.query_server_history_short", fake_short)
    monkeypatch.setattr("db.writer.query_server_history", fake_long)

    client = TestClient(app)
    r = client.get(
        "/price-history",
        params={
            "server": "Firemaw",
            "region": "EU",
            "version": "Anniversary",
            "hours": "24",
            "last": "50",
        },
    )
    assert r.status_code == 200
    assert called == ["long"]
    assert r.json()["count"] == 1


def test_price_history_hours_validation_422(monkeypatch):
    monkeypatch.setattr(
        "db.writer.query_server_history",
        lambda **kwargs: [],
    )
    client = TestClient(app)
    r = client.get(
        "/price-history",
        params={
            "server": "Firemaw",
            "region": "EU",
            "version": "Anniversary",
            "hours": "0",
        },
    )
    assert r.status_code == 422
