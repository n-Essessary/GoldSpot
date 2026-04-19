from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from db import server_resolver as sr
from service import normalize_pipeline as np
from service.price_profiles import PriceProfile


@pytest.mark.asyncio
async def test_offer_normalized_with_high_price(make_offer, monkeypatch):
    offer = make_offer(server_id=101, price_per_1k=9999)
    monkeypatch.setattr(
        sr,
        "get_server_data",
        lambda _sid: {
            "id": 101,
            "name": "Firemaw",
            "region": "EU",
            "version": "Classic Era",
        },
    )
    normalized, quarantined = await np.normalize_offer_batch([offer], pool=object())
    assert len(normalized) == 1 and quarantined == []


@pytest.mark.asyncio
async def test_no_price_reroute_keeps_original_server_id(make_offer, monkeypatch):
    """Pipeline no longer reroutes by price; resolved server_id stays put."""
    offer = make_offer(
        server_id=1,
        server_name="Firemaw",
        display_server="(EU) Classic",
        server="(EU) Classic",
        raw_price=0.1,
    )
    monkeypatch.setattr(
        sr,
        "get_server_data",
        lambda sid: {
            1: {"id": 1, "name": "Firemaw", "region": "EU", "version": "Classic"},
            2: {"id": 2, "name": "Firemaw", "region": "EU", "version": "Anniversary"},
        }[sid],
    )
    monkeypatch.setattr(
        sr,
        "find_server_versions",
        AsyncMock(
            return_value=[
                {"id": 1, "name": "Firemaw", "region": "EU", "version": "Classic"},
                {"id": 2, "name": "Firemaw", "region": "EU", "version": "Anniversary"},
            ]
        ),
    )
    monkeypatch.setattr(
        "service.price_profiles.get_profile",
        lambda sid, _f="All": PriceProfile(sid, "All", 90, 100, 110, 5, 1.0)
        if sid == 2
        else PriceProfile(sid, "All", 900, 1000, 1100, 5, 1.0),
    )
    normalized, _ = await np.normalize_offer_batch([offer], pool=object())
    assert normalized[0].server_id == 1


@pytest.mark.asyncio
async def test_extreme_price_normalized_without_error(make_offer, monkeypatch):
    offer = make_offer(
        server_id=1,
        server_name="Firemaw",
        display_server="(EU) Classic",
        server="(EU) Classic",
        price_per_1k=5000.0,
    )
    monkeypatch.setattr(
        sr,
        "get_server_data",
        lambda _sid: {"id": 1, "name": "Firemaw", "region": "EU", "version": "Classic"},
    )
    monkeypatch.setattr(
        sr,
        "find_server_versions",
        AsyncMock(
            return_value=[
                {"id": 1, "name": "Firemaw", "region": "EU", "version": "Classic"},
            ]
        ),
    )
    monkeypatch.setattr(
        "service.price_profiles.get_profile",
        lambda _sid, _f="All": PriceProfile(1, "All", 90, 100, 110, 5, 1.0),
    )
    normalized, quarantined = await np.normalize_offer_batch([offer], pool=object())
    assert len(normalized) == 1 and quarantined == []
