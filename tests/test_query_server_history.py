"""Tests for db.writer.query_server_history (hours window, columns)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from db import writer


@pytest.mark.asyncio
async def test_query_server_history_passes_hours_and_limit(monkeypatch):
    captured: dict = {}

    class FakePool:
        async def fetch(self, query, *params):
            captured["query"] = query
            captured["params"] = params
            return []

    async def fake_get_pool():
        return FakePool()

    monkeypatch.setattr(writer, "get_pool", fake_get_pool)
    await writer.query_server_history(
        "Firemaw", "EU", "Anniversary", "Horde", last=300, hours=48,
    )
    assert captured["params"] == ("Firemaw", "EU", "Anniversary", "Horde", 48, 300)
    assert "($5::integer * INTERVAL '1 hour')" in captured["query"]
    assert "LIMIT $6" in captured["query"]


@pytest.mark.asyncio
async def test_query_server_history_maps_null_best_ask_to_index(monkeypatch):
    """NULL best_ask (legacy rows) displays as index_price_per_1k."""
    ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)

    class Row:
        def __init__(self, d):
            self._d = d

        def __getitem__(self, k):
            return self._d[k]

    class FakePool:
        async def fetch(self, query, *params):
            return [
                Row(
                    {
                        "recorded_at": ts,
                        "index_price": 0.012,
                        "best_ask": None,
                        "sample_size": 3,
                    }
                )
            ]

    async def fake_get_pool():
        return FakePool()

    monkeypatch.setattr(writer, "get_pool", fake_get_pool)
    out = await writer.query_server_history("X", "EU", "Anniversary", "Horde")
    assert len(out) == 1
    assert out[0]["index_price_per_1k"] == pytest.approx(12.0)
    assert out[0]["best_ask"] == pytest.approx(12.0)
