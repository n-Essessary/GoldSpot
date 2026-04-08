from __future__ import annotations

import pytest

from db import server_resolver as sr
from service import normalize_pipeline as np


@pytest.mark.asyncio
async def test_faction_defaults_to_horde(make_offer):
    offer = make_offer(faction="")
    normalized, quarantined = await np.normalize_offer_batch([offer], None)
    assert normalized[0].faction == "Horde"


@pytest.mark.asyncio
async def test_broken_title_goes_to_quarantine(make_offer):
    offer = make_offer(server_name="", display_server="", server="", server_id=None)
    normalized, quarantined = await np.normalize_offer_batch([offer], None)
    assert normalized == [] and quarantined and quarantined[0].reason == "empty_server_title"


@pytest.mark.asyncio
async def test_canonical_version_overrides_raw_title(make_offer, monkeypatch):
    offer = make_offer(display_server="(EU) Seasonal", server="(EU) Seasonal", server_id=10)
    monkeypatch.setattr(
        sr,
        "get_server_data",
        lambda _sid: {"id": 10, "name": "Lava Lash", "region": "EU", "version": "Season of Discovery"},
    )
    normalized, quarantined = await np.normalize_offer_batch([offer], None)
    assert not quarantined and normalized[0].display_server == "(EU) Season of Discovery"


@pytest.mark.asyncio
async def test_inactive_server_goes_to_quarantine(make_offer, monkeypatch):
    offer = make_offer(server_id=22, display_server="(EU) Anniversary", server="(eu) anniversary")
    monkeypatch.setattr(
        sr,
        "get_server_data",
        lambda _sid: {
            "id": 22,
            "name": "Jom Gabbar",
            "region": "US",
            "version": "Season of Mastery",
            "realm_type": "Normal",
            "is_active": False,
        },
    )
    normalized, quarantined = await np.normalize_offer_batch([offer], None)
    assert normalized == [] and quarantined and quarantined[0].reason == "deprecated_version"
