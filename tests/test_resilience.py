from __future__ import annotations

import httpx
import pytest
from unittest.mock import AsyncMock

from parser import g2g_parser


@pytest.mark.asyncio
async def test_fetch_all_sellers_survives_region_error(monkeypatch):
    client = g2g_parser.G2GClient()
    client._client = object()  # only for attribute presence

    calls = {"n": 0}

    async def fake_get_retry(_client, _url, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.HTTPStatusError("boom", request=httpx.Request("GET", "https://x"), response=httpx.Response(500))
        return httpx.Response(200, json={"payload": {"results": [{"username": "ok"}]}})

    monkeypatch.setattr(g2g_parser, "_http_get_retry", fake_get_retry)
    regions = [g2g_parser.G2GRegion(region_id="r1", relation_id="rel1"), g2g_parser.G2GRegion(region_id="r2", relation_id="rel2")]
    sellers = await client.fetch_all_sellers("b", "s", regions)
    assert sellers == ["ok"]


@pytest.mark.asyncio
async def test_fetch_g2g_game_handles_partial_seller_failures(monkeypatch):
    async def fake_regions(self, _b, _s):
        return [g2g_parser.G2GRegion(region_id="r", relation_id="rel")]

    async def fake_all_sellers(self, _b, _s, _r):
        return ["good", "bad"]

    async def fake_fetch(self, _b, _s, seller):
        if seller == "bad":
            raise RuntimeError("timeout")
        return [g2g_parser.G2GOffer(
            offer_id="1", title="X [EU - Anniversary] - Horde", server_name="X", region_id="r", relation_id="rel",
            price_usd=0.01, min_qty=1, available_qty=1, seller="good", brand_id="b", service_id="s"
        )]

    monkeypatch.setattr(g2g_parser.G2GClient, "fetch_regions", fake_regions)
    monkeypatch.setattr(g2g_parser.G2GClient, "fetch_all_sellers", fake_all_sellers)
    monkeypatch.setattr(g2g_parser.G2GClient, "fetch_seller_offers", fake_fetch)
    out = await g2g_parser.fetch_g2g_game("wow_classic_era_seasonal_anniversary")
    assert len(out) == 1


@pytest.mark.asyncio
async def test_retry_respects_429_backoff(monkeypatch):
    sleeps = []

    async def fake_sleep(sec):
        sleeps.append(sec)

    seq = [
        httpx.Response(429, headers={"Retry-After": "3"}, request=httpx.Request("GET", "https://x")),
        httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", "https://x")),
    ]

    class C:
        async def get(self, _url, **_kwargs):
            return seq.pop(0)

    monkeypatch.setattr(g2g_parser.asyncio, "sleep", fake_sleep)
    resp = await g2g_parser._http_get_retry(C(), "https://x")
    assert resp.status_code == 200 and sleeps == [3]
