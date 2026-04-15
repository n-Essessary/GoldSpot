"""
tests/test_g2g_parser.py — Unit tests for the two-system G2G parser.

Run from backend/:
    python -m pytest tests/test_g2g_parser.py -v

Uses respx to mock httpx calls; pytest-asyncio for async tests.
"""
import sys
import os
import time
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import httpx
import respx

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# ── Import parser components ──────────────────────────────────────────────────
from parser.g2g_parser import (
    G2GClient,
    G2GOffer,
    TrackedOffer,
    _build_offer_url,
    _pool,
    _MAX_POOL,
    fetch_g2g_game,
    BASE,
    GAME_CONFIG,
)

# ── pytest-asyncio config ─────────────────────────────────────────────────────
pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

GAME_KEY = "wow_classic_era_seasonal_anniversary"
SERVER_TITLE = "Spineshatter [EU - Anniversary] - Horde"

def _make_tracked(
    offer_id: str = "OID1",
    seller: str = "SellerA",
    price_usd: float = 0.01,
    available_qty: int = 1000,
    added_at: float | None = None,
) -> TrackedOffer:
    cfg = GAME_CONFIG[GAME_KEY]
    return TrackedOffer(
        offer_id=offer_id,
        server_title=SERVER_TITLE,
        seller=seller,
        region_id="abc",
        price_usd=price_usd,
        available_qty=available_qty,
        added_at=added_at if added_at is not None else time.monotonic(),
        brand_id=cfg["brand_id"],
        service_id=cfg["service_id"],
    )


def _live_payload(
    offer_id: str = "OID1",
    price: float = 0.01,
    qty: int = 1000,
    is_online: bool = True,
    status: str = "live",
) -> dict:
    return {
        "offer_id": offer_id,
        "unit_price_in_usd": str(price),
        "available_qty": qty,
        "is_online": is_online,
        "status": status,
        "region_id": "abc",
        "username": "SellerA",
    }


# Helper: build a minimal grouped-search JSON response
def _grouped_resp(offers: list[dict]) -> dict:
    return {"payload": {"results": offers}}


def _region_resp() -> dict:
    return {
        "payload": {
            "results": [
                {"region_id": "r1", "relation_id": "rel1"},
            ]
        }
    }


def _make_grouped_offer_dict(
    offer_id: str = "OID_NEW",
    title: str = SERVER_TITLE,
    seller: str = "SellerNew",
    price: float = 0.008,
) -> dict:
    return {
        "offer_id": offer_id,
        "title": title,
        "region_id": "abc",
        "relation_id": "rel1",
        "converted_unit_price": str(price),
        "available_qty": 500,
        "min_qty": 1,
        "username": seller,
        "brand_id": GAME_CONFIG[GAME_KEY]["brand_id"],
        "service_id": GAME_CONFIG[GAME_KEY]["service_id"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. test_fetch_offer_status_live
# ─────────────────────────────────────────────────────────────────────────────
async def test_fetch_offer_status_live():
    """fetch_offer_status returns payload dict for a live offer."""
    payload = _live_payload("OID1")
    response_body = {"payload": payload}

    with respx.mock(base_url=BASE) as mock:
        mock.get("/offer/OID1").mock(
            return_value=httpx.Response(200, json=response_body)
        )
        async with G2GClient() as client:
            result = await client.fetch_offer_status("OID1")

    assert result is not None
    assert result["offer_id"] == "OID1"


# ─────────────────────────────────────────────────────────────────────────────
# 2. test_fetch_offer_status_deleted
# ─────────────────────────────────────────────────────────────────────────────
async def test_fetch_offer_status_deleted():
    """fetch_offer_status returns None when API returns code 4041."""
    with respx.mock(base_url=BASE) as mock:
        mock.get("/offer/OID_GONE").mock(
            return_value=httpx.Response(200, json={"code": 4041, "payload": {}})
        )
        async with G2GClient() as client:
            result = await client.fetch_offer_status("OID_GONE")

    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. test_fetch_offer_status_offline
# ─────────────────────────────────────────────────────────────────────────────
async def test_fetch_offer_status_offline():
    """fetch_offer_status returns payload even when is_online=False (caller decides)."""
    payload = _live_payload("OID2", is_online=False)
    with respx.mock(base_url=BASE) as mock:
        mock.get("/offer/OID2").mock(
            return_value=httpx.Response(200, json={"payload": payload})
        )
        async with G2GClient() as client:
            result = await client.fetch_offer_status("OID2")

    assert result is not None
    assert result["is_online"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. test_pool_eviction
# ─────────────────────────────────────────────────────────────────────────────
async def test_pool_eviction():
    """Oldest entry is evicted when pool slot is full and a new offer_id arrives."""
    _pool.clear()
    cfg = GAME_CONFIG[GAME_KEY]

    # Pre-fill slot with 5 tracked offers — oldest first
    oldest_time = time.monotonic() - 1000
    slot_entries = [
        _make_tracked(offer_id=f"OLD{i}", added_at=oldest_time + i)
        for i in range(5)
    ]
    _pool[GAME_KEY] = {SERVER_TITLE: list(slot_entries)}

    new_offer_dict = _make_grouped_offer_dict(offer_id="BRAND_NEW")

    # System 2 needs offer_status for each of the 5 existing + newly added offer
    live_old = {f"OLD{i}": _live_payload(f"OLD{i}") for i in range(5)}
    brand_new_status = _live_payload("BRAND_NEW", price=0.008)

    def status_side_effect(offer_id):
        if offer_id == "BRAND_NEW":
            return brand_new_status
        return live_old.get(offer_id)

    with (
        patch.object(G2GClient, "fetch_regions", new_callable=AsyncMock) as mock_regions,
        patch.object(G2GClient, "fetch_offers", new_callable=AsyncMock) as mock_offers,
        patch.object(G2GClient, "fetch_offer_status", new_callable=AsyncMock) as mock_status,
    ):
        mock_regions.return_value = [
            MagicMock(region_id="r1", relation_id="rel1")
        ]
        mock_offers.return_value = [
            G2GOffer(
                offer_id="BRAND_NEW",
                title=SERVER_TITLE,
                server_name=SERVER_TITLE,
                region_id="abc",
                relation_id="rel1",
                price_usd=0.008,
                min_qty=1,
                available_qty=500,
                seller="SellerNew",
                brand_id=cfg["brand_id"],
                service_id=cfg["service_id"],
            )
        ]
        mock_status.side_effect = status_side_effect

        await fetch_g2g_game(GAME_KEY, max_regions=1)

    slot = _pool[GAME_KEY][SERVER_TITLE]
    ids = {t.offer_id for t in slot}

    assert len(slot) == _MAX_POOL, f"Expected pool size {_MAX_POOL}, got {len(slot)}"
    assert "BRAND_NEW" in ids, "New offer should be in pool"
    assert "OLD0" not in ids, "Oldest offer (OLD0) should have been evicted"


# ─────────────────────────────────────────────────────────────────────────────
# 5. test_pool_prune_offline
# ─────────────────────────────────────────────────────────────────────────────
async def test_pool_prune_offline():
    """Offline offer is removed from pool during System 2 pass."""
    _pool.clear()
    cfg = GAME_CONFIG[GAME_KEY]

    tracked_online = _make_tracked("OID_ON", seller="SellerOnline", price_usd=0.01)
    tracked_offline = _make_tracked("OID_OFF", seller="SellerOffline", price_usd=0.012)
    _pool[GAME_KEY] = {SERVER_TITLE: [tracked_online, tracked_offline]}

    def status_side_effect(offer_id):
        if offer_id == "OID_ON":
            return _live_payload("OID_ON")
        if offer_id == "OID_OFF":
            return _live_payload("OID_OFF", is_online=False)
        return None

    with (
        patch.object(G2GClient, "fetch_regions", new_callable=AsyncMock) as mock_regions,
        patch.object(G2GClient, "fetch_offers", new_callable=AsyncMock) as mock_offers,
        patch.object(G2GClient, "fetch_offer_status", new_callable=AsyncMock) as mock_status,
    ):
        mock_regions.return_value = [MagicMock(region_id="r1", relation_id="rel1")]
        mock_offers.return_value = []
        mock_status.side_effect = status_side_effect

        await fetch_g2g_game(GAME_KEY, max_regions=1)

    slot = _pool[GAME_KEY].get(SERVER_TITLE, [])
    ids = {t.offer_id for t in slot}
    assert "OID_OFF" not in ids, "Offline offer should be pruned"
    assert "OID_ON" in ids, "Online offer should remain"


# ─────────────────────────────────────────────────────────────────────────────
# 6. test_pool_prune_deleted
# ─────────────────────────────────────────────────────────────────────────────
async def test_pool_prune_deleted():
    """Deleted offer (None from fetch_offer_status) is removed from pool."""
    _pool.clear()
    cfg = GAME_CONFIG[GAME_KEY]

    tracked_alive = _make_tracked("OID_ALIVE", seller="SellerAlive")
    tracked_dead = _make_tracked("OID_DEAD", seller="SellerDead")
    _pool[GAME_KEY] = {SERVER_TITLE: [tracked_alive, tracked_dead]}

    def status_side_effect(offer_id):
        if offer_id == "OID_ALIVE":
            return _live_payload("OID_ALIVE")
        return None  # deleted

    with (
        patch.object(G2GClient, "fetch_regions", new_callable=AsyncMock) as mock_regions,
        patch.object(G2GClient, "fetch_offers", new_callable=AsyncMock) as mock_offers,
        patch.object(G2GClient, "fetch_offer_status", new_callable=AsyncMock) as mock_status,
    ):
        mock_regions.return_value = [MagicMock(region_id="r1", relation_id="rel1")]
        mock_offers.return_value = []
        mock_status.side_effect = status_side_effect

        await fetch_g2g_game(GAME_KEY, max_regions=1)

    slot = _pool[GAME_KEY].get(SERVER_TITLE, [])
    ids = {t.offer_id for t in slot}
    assert "OID_DEAD" not in ids, "Deleted offer should be removed from pool"
    assert "OID_ALIVE" in ids, "Live offer should remain"


# ─────────────────────────────────────────────────────────────────────────────
# 7. test_price_update
# ─────────────────────────────────────────────────────────────────────────────
async def test_price_update():
    """Price is set by System 1 (grouped converted_unit_price) and NOT overwritten by System 2.

    System 2 (/offer/{id}) returns a different price context — only available_qty is
    taken from it. The price set at pool-add time (from grouped search) must be preserved.
    """
    _pool.clear()
    cfg = GAME_CONFIG[GAME_KEY]

    # System 1 grouped offer carries the correct price via converted_unit_price
    grouped_offer = G2GOffer(
        offer_id="OID_P",
        title=SERVER_TITLE,
        server_name=SERVER_TITLE,
        region_id="abc",
        relation_id="rel1",
        price_usd=0.018699,   # correct grouped converted_unit_price
        min_qty=1,
        available_qty=5000,
        seller="SellerP",
        brand_id=cfg["brand_id"],
        service_id=cfg["service_id"],
    )

    # System 2 returns a DIFFERENT (wrong) price — must NOT be applied to price_usd
    status_payload = {
        "offer_id": "OID_P",
        "converted_unit_price": "0.040087",   # wrong context — ignored for price
        "unit_price_in_usd": "0.040087",
        "available_qty": 4800,                # qty update IS applied
        "is_online": True,
        "status": "live",
        "region_id": "abc",
        "username": "SellerP",
    }

    with (
        patch.object(G2GClient, "fetch_regions", new_callable=AsyncMock) as mock_regions,
        patch.object(G2GClient, "fetch_offers", new_callable=AsyncMock) as mock_offers,
        patch.object(G2GClient, "fetch_offer_status", new_callable=AsyncMock) as mock_status,
    ):
        mock_regions.return_value = [MagicMock(region_id="r1", relation_id="rel1")]
        mock_offers.return_value = [grouped_offer]
        mock_status.return_value = status_payload

        await fetch_g2g_game(GAME_KEY, max_regions=1)

    slot = _pool[GAME_KEY].get(SERVER_TITLE, [])
    assert slot, "Slot should not be empty"
    t = next(x for x in slot if x.offer_id == "OID_P")
    assert t.price_usd == pytest.approx(0.018699), (
        f"Price must stay at grouped value 0.018699, got {t.price_usd}"
    )
    assert t.available_qty == 4800, (
        f"available_qty should be updated from System 2 to 4800, got {t.available_qty}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 8. test_output_offer_url_format
# ─────────────────────────────────────────────────────────────────────────────
async def test_output_offer_url_format():
    """Output G2GOffer has correctly formatted offer_url."""
    _pool.clear()
    tracked = TrackedOffer(
        offer_id="G123",
        server_title=SERVER_TITLE,
        seller="TestSeller",
        region_id="abc",
        price_usd=0.01,
        available_qty=1000,
        added_at=time.monotonic(),
        brand_id=GAME_CONFIG[GAME_KEY]["brand_id"],
        service_id=GAME_CONFIG[GAME_KEY]["service_id"],
    )
    _pool[GAME_KEY] = {SERVER_TITLE: [tracked]}

    with (
        patch.object(G2GClient, "fetch_regions", new_callable=AsyncMock) as mock_regions,
        patch.object(G2GClient, "fetch_offers", new_callable=AsyncMock) as mock_offers,
        patch.object(G2GClient, "fetch_offer_status", new_callable=AsyncMock) as mock_status,
    ):
        mock_regions.return_value = [MagicMock(region_id="r1", relation_id="rel1")]
        mock_offers.return_value = []
        mock_status.return_value = _live_payload("G123")

        result = await fetch_g2g_game(GAME_KEY, max_regions=1)

    expected_url = (
        "https://www.g2g.com/categories/wow-classic-era-vanilla-gold"
        "/offer/G123?region_id=abc&seller=TestSeller"
    )
    pool_offers = [o for o in result if o.offer_id == "G123"]
    assert pool_offers, "G123 offer should appear in output"
    assert pool_offers[0].offer_url == expected_url, (
        f"Wrong offer_url: {pool_offers[0].offer_url!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 9. test_grouped_fallback
# ─────────────────────────────────────────────────────────────────────────────
async def test_grouped_fallback():
    """On cold start (empty pool) all 3 grouped offers appear in combined output."""
    _pool.clear()
    cfg = GAME_CONFIG[GAME_KEY]

    grouped = [
        G2GOffer(
            offer_id=f"GRP{i}",
            title=f"Server{i} [EU - Anniversary] - Horde",
            server_name=f"Server{i}",
            region_id="r1",
            relation_id="rel1",
            price_usd=0.01 + i * 0.001,
            min_qty=1,
            available_qty=500,
            seller=f"Seller{i}",
            brand_id=cfg["brand_id"],
            service_id=cfg["service_id"],
        )
        for i in range(3)
    ]

    with (
        patch.object(G2GClient, "fetch_regions", new_callable=AsyncMock) as mock_regions,
        patch.object(G2GClient, "fetch_offers", new_callable=AsyncMock) as mock_offers,
        patch.object(G2GClient, "fetch_offer_status", new_callable=AsyncMock) as mock_status,
    ):
        mock_regions.return_value = [MagicMock(region_id="r1", relation_id="rel1")]
        mock_offers.return_value = grouped
        # System 2: newly added offers will have status fetched; return live for all
        mock_status.side_effect = lambda oid: _live_payload(oid)

        result = await fetch_g2g_game(GAME_KEY, max_regions=1)

    result_ids = {o.offer_id for o in result}
    for offer in grouped:
        assert offer.offer_id in result_ids, (
            f"{offer.offer_id} missing from combined output"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 10. test_pool_sort_by_price
# ─────────────────────────────────────────────────────────────────────────────
async def test_pool_sort_by_price():
    """Pool slot is sorted by price ASC after each cycle."""
    _pool.clear()
    prices = [0.02, 0.01, 0.015]
    entries = [
        _make_tracked(offer_id=f"S{i}", seller=f"Seller{i}", price_usd=p)
        for i, p in enumerate(prices)
    ]
    _pool[GAME_KEY] = {SERVER_TITLE: entries}

    # Return slightly different prices from tracker so sorting is exercised
    updated_prices = {"S0": 0.02, "S1": 0.01, "S2": 0.015}

    def status_side_effect(offer_id):
        p = updated_prices.get(offer_id, 0.01)
        return _live_payload(offer_id, price=p)

    with (
        patch.object(G2GClient, "fetch_regions", new_callable=AsyncMock) as mock_regions,
        patch.object(G2GClient, "fetch_offers", new_callable=AsyncMock) as mock_offers,
        patch.object(G2GClient, "fetch_offer_status", new_callable=AsyncMock) as mock_status,
    ):
        mock_regions.return_value = [MagicMock(region_id="r1", relation_id="rel1")]
        mock_offers.return_value = []
        mock_status.side_effect = status_side_effect

        await fetch_g2g_game(GAME_KEY, max_regions=1)

    slot = _pool[GAME_KEY].get(SERVER_TITLE, [])
    slot_prices = [t.price_usd for t in slot]
    assert slot_prices == sorted(slot_prices), (
        f"Slot not sorted ASC: {slot_prices}"
    )
    assert slot_prices == pytest.approx([0.01, 0.015, 0.02])
