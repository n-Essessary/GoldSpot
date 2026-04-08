from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from main import app
from service import offers_service as osvc


@pytest.mark.asyncio
async def test_index_cache_per_server_key_populated(make_offer, monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("db.writer.upsert_server_index", _noop)
    monkeypatch.setattr("db.writer.write_index_snapshot", _noop)
    monkeypatch.setattr("db.writer.write_price_snapshot", _noop)
    monkeypatch.setattr("service.price_profiles.update_profiles", lambda *_args, **_kwargs: None)

    osvc._cache["g2g"] = [
        make_offer(
            id="k1",
            source="g2g",
            server_name="Firemaw",
            display_server="(EU) Anniversary",
            server="(eu) anniversary",
            faction="Horde",
            amount_gold=2000,
            raw_price=0.012,
        ),
        make_offer(
            id="k2",
            source="g2g",
            server_name="Firemaw",
            display_server="(EU) Anniversary",
            server="(eu) anniversary",
            faction="Horde",
            amount_gold=1800,
            raw_price=0.013,
        ),
    ]
    osvc._cache["funpay"] = []
    await osvc._do_snapshot_all_servers()
    key = "Firemaw::EU::Anniversary::Horde"
    assert key in osvc._index_cache and osvc._index_cache[key].index_price > 0


@pytest.mark.asyncio
async def test_index_endpoint_per_server_key():
    osvc._index_cache["Firemaw::EU::Anniversary::Horde"] = osvc.IndexPrice(
        index_price=12.3, vwap=12.1, best_ask=12.0, price_min=11.5, price_max=13.0,
        offer_count=10, total_volume=10000, sources=["g2g"],
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/index/Firemaw", params={"region": "EU", "version": "Anniversary", "faction": "Horde"})
    assert r.status_code == 200 and r.json()["index_price"] > 0


@pytest.mark.asyncio
async def test_index_endpoint_fallback_to_group():
    osvc._index_cache["(EU) Anniversary::Horde"] = osvc.IndexPrice(
        index_price=10.0, vwap=9.9, best_ask=9.8, price_min=9.0, price_max=11.0,
        offer_count=10, total_volume=10000, sources=["funpay"],
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/index/(EU) Anniversary", params={"faction": "Horde"})
    assert r.status_code == 200 and r.json()["index_price"] > 0
