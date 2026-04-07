from __future__ import annotations

import re

import pytest
from httpx import ASGITransport, AsyncClient

from main import app


@pytest.mark.asyncio
async def test_meta_contract():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/meta")
    assert r.status_code == 200 and ("last_update" in r.json())


@pytest.mark.asyncio
async def test_servers_count_matches_len():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/servers")
    body = r.json()
    assert r.status_code == 200 and body["count"] == len(body["servers"])


@pytest.mark.asyncio
async def test_offers_all_rows_have_positive_price():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/offers")
    assert r.status_code == 200 and all(o["price_per_1k"] > 0 for o in r.json()["offers"])


@pytest.mark.asyncio
async def test_offers_horde_filter():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/offers", params={"faction": "Horde"})
    assert r.status_code == 200 and all(o["faction"] == "Horde" for o in r.json()["offers"])


@pytest.mark.asyncio
async def test_offers_invalid_sort_returns_422():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/offers", params={"sort_by": "invalid"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_parser_status_has_funpay_and_g2g():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/parser-status")
    body = r.json()
    assert r.status_code == 200 and "funpay" in body and "g2g" in body
