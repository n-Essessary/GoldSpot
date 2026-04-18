"""
tests/test_mop_expansion.py — Pytest suite covering MoP Classic expansion changes.

Blocks:
  1. canonical_servers.py — registry correctness
  2. schemas.py           — game_version field
  3. g2g_parser.py        — GameConfig + _build_offer_url + _to_offer
  4. funpay_parser.py     — ChipConfig + no legacy _URL
  5. server_resolver.py   — game_version routing (fully mocked, zero DB)

Run from backend/:
    python -m pytest tests/test_mop_expansion.py -v
"""

import sys
import os

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# NOTE: @pytest.mark.asyncio applied individually to async tests only.


# ═══════════════════════════════════════════════════════════════════════════════
# BLOCK 1 — canonical_servers.py
# ═══════════════════════════════════════════════════════════════════════════════

def test_mop_valid_version():
    from db.canonical_servers import VALID_VERSIONS
    assert "MoP Classic" in VALID_VERSIONS


def test_mop_server_count():
    from db.canonical_servers import CANONICAL_SERVERS
    mop = [s for s in CANONICAL_SERVERS if s.version == "MoP Classic"]
    # Actual count: 28 EU + 25 US + 3 OCE + 2 RU = 58
    assert len(mop) == 58


def test_mop_no_duplicate_name_region():
    from db.canonical_servers import CANONICAL_SERVERS
    mop = [s for s in CANONICAL_SERVERS if s.version == "MoP Classic"]
    seen: set = set()
    for s in mop:
        key = (s.name.lower(), s.region.upper())
        assert key not in seen, f"Duplicate MoP server: {s.name} [{s.region}]"
        seen.add(key)


def test_mop_valid_regions():
    from db.canonical_servers import CANONICAL_SERVERS, VALID_REGIONS
    mop = [s for s in CANONICAL_SERVERS if s.version == "MoP Classic"]
    for s in mop:
        assert s.region in VALID_REGIONS, f"Invalid region {s.region!r} for {s.name}"


def test_mop_collision_servers_exist_in_both_versions():
    from db.canonical_servers import CANONICAL_SERVERS
    collisions = ["Firemaw", "Gehennas", "Grobbulus", "Arugal", "Pagle",
                  "Chromie", "Flamegor"]
    for name in collisions:
        assert any(s.name == name for s in CANONICAL_SERVERS if s.version == "Classic Era"), \
            f"{name} missing from Classic Era"
        assert any(s.name == name for s in CANONICAL_SERVERS if s.version == "MoP Classic"), \
            f"{name} missing from MoP Classic"


def test_all_servers_valid_versions():
    from db.canonical_servers import CANONICAL_SERVERS, VALID_VERSIONS
    for s in CANONICAL_SERVERS:
        assert s.version in VALID_VERSIONS, \
            f"Invalid version {s.version!r} for server {s.name}"


# ═══════════════════════════════════════════════════════════════════════════════
# BLOCK 2 — schemas.py
# ═══════════════════════════════════════════════════════════════════════════════

def test_offer_has_game_version_field():
    from api.schemas import Offer
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    o = Offer(
        id="test_1", source="g2g", server="firemaw", faction="Horde",
        raw_price=0.05, raw_price_unit="per_unit", lot_size=1,
        amount_gold=1000, seller="test", updated_at=now, fetched_at=now,
        game_version="MoP Classic",
    )
    assert o.game_version == "MoP Classic"


def test_offer_game_version_defaults_empty():
    from api.schemas import Offer
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    o = Offer(
        id="test_2", source="g2g", server="firemaw", faction="Horde",
        raw_price=0.05, raw_price_unit="per_unit", lot_size=1,
        amount_gold=1000, seller="test", updated_at=now, fetched_at=now,
    )
    assert o.game_version == ""


def test_offer_row_has_game_version():
    from api.schemas import Offer, OfferRow
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    o = Offer(
        id="test_3", source="funpay", server="firemaw", faction="Alliance",
        raw_price=0.04, raw_price_unit="per_unit", lot_size=1,
        amount_gold=500, seller="test", updated_at=now, fetched_at=now,
        game_version="Classic Era",
    )
    row = OfferRow.from_offer(o)
    assert row.game_version == "Classic Era"


# ═══════════════════════════════════════════════════════════════════════════════
# BLOCK 3 — g2g_parser.py
# ═══════════════════════════════════════════════════════════════════════════════

def test_g2g_game_configs_exist():
    from parser.g2g_parser import GAME_CONFIGS
    keys = [c.key for c in GAME_CONFIGS]
    assert "wow_classic_era" in keys
    assert "wow_mop_classic" in keys


def test_g2g_classic_config():
    from parser.g2g_parser import GAME_CONFIGS
    c = next(x for x in GAME_CONFIGS if x.key == "wow_classic_era")
    assert c.brand_id == "lgc_game_27816"
    assert c.seo_term == "wow-classic-era-vanilla-gold"
    assert c.game_version == "Classic Era"


def test_g2g_mop_config():
    from parser.g2g_parser import GAME_CONFIGS
    c = next(x for x in GAME_CONFIGS if x.key == "wow_mop_classic")
    assert c.brand_id == "lgc_game_29076"
    assert c.seo_term == "wow-classic-gold"
    assert c.game_version == "MoP Classic"


def test_g2g_no_legacy_constants():
    import parser.g2g_parser as m
    assert not hasattr(m, "_SEO_TERM"), "_SEO_TERM must be removed"
    assert not hasattr(m, "_BRAND_ID"), "_BRAND_ID must be removed"
    assert not hasattr(m, "_SERVICE_ID"), "_SERVICE_ID must be removed"


def test_g2g_build_offer_url_mop():
    from parser.g2g_parser import _build_offer_url
    url = _build_offer_url(
        offer_group="/lgc_29076_platform_59293",
        region_id="dfced32f-2f0a-4df5-a218-1e068cfadffa",
        sort="lowest_price",
        seo_term="wow-classic-gold",
    )
    assert "wow-classic-gold" in url
    assert "lgc_29076_platform_59293" in url
    assert "wow-classic-era-vanilla-gold" not in url


def test_g2g_build_offer_url_classic():
    from parser.g2g_parser import _build_offer_url
    url = _build_offer_url(
        offer_group="/lgc_27816_dropdown_18_41007_alliance",
        region_id="some-region-id",
        sort="lowest_price",
        seo_term="wow-classic-era-vanilla-gold",
    )
    assert "wow-classic-era-vanilla-gold" in url


def test_g2g_to_offer_carries_game_version():
    from parser.g2g_parser import G2GOffer, _to_offer
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    raw = G2GOffer(
        offer_id="abc123",
        title="Galakras [US] - Horde",
        server_name="Galakras",
        region_id="some-region",
        relation_id="rel_1",
        price_usd=0.018,
        min_qty=1,
        available_qty=5000,
        seller="TestSeller",
        brand_id="lgc_game_29076",
        service_id="lgc_service_1",
        sort="lowest_price",
        game_version="MoP Classic",
    )
    offer = _to_offer(raw, now)
    assert offer is not None
    assert offer.game_version == "MoP Classic"


# ═══════════════════════════════════════════════════════════════════════════════
# BLOCK 4 — funpay_parser.py
# ═══════════════════════════════════════════════════════════════════════════════

def test_funpay_chip_configs_exist():
    from parser.funpay_parser import CHIP_CONFIGS
    chip_ids = [c.chip_id for c in CHIP_CONFIGS]
    assert 114 in chip_ids
    assert 145 in chip_ids
    assert 146 in chip_ids
    assert 147 in chip_ids


def test_funpay_chip_game_versions():
    from parser.funpay_parser import CHIP_CONFIGS
    classic = next(c for c in CHIP_CONFIGS if c.chip_id == 114)
    assert classic.game_version == "Classic Era"
    for chip_id in (145, 146, 147):
        c = next(x for x in CHIP_CONFIGS if x.chip_id == chip_id)
        assert c.game_version == "MoP Classic", \
            f"chip {chip_id} should be MoP Classic, got {c.game_version!r}"


def test_funpay_no_legacy_url_constant():
    import parser.funpay_parser as m
    assert not hasattr(m, "_URL"), "_URL must be removed"


# ═══════════════════════════════════════════════════════════════════════════════
# BLOCK 5 — server_resolver.py  (fully mocked, zero DB connections)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_resolve_with_game_version_uses_version():
    """When game_version is provided, _lookup_server must be called with it,
    not with the version parsed from the title bracket."""
    from db import server_resolver
    from unittest.mock import AsyncMock, patch, MagicMock

    mock_pool = MagicMock()
    with patch.object(server_resolver, "_alias_cache", {}), \
         patch.object(server_resolver, "_ensure_cache", AsyncMock()), \
         patch.object(server_resolver, "_lookup_server", AsyncMock(return_value=42)) as mock_lookup:
        result = await server_resolver.resolve_server(
            raw_title="Firemaw [EU - Classic Era] - Horde",
            source="g2g",
            pool=mock_pool,
            game_version="MoP Classic",
        )

    # _lookup_server must have been called with "MoP Classic", not "Classic Era"
    mock_lookup.assert_called_once()
    call_args = mock_lookup.call_args
    version_used = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs.get("version")
    assert version_used == "MoP Classic", \
        f"Expected version='MoP Classic', got: {call_args}"
    assert result == 42


@pytest.mark.asyncio
async def test_resolve_without_game_version_uses_title_version():
    """When game_version is omitted, the version from the title bracket is used."""
    from db import server_resolver
    from unittest.mock import AsyncMock, patch, MagicMock

    mock_pool = MagicMock()
    with patch.object(server_resolver, "_alias_cache", {}), \
         patch.object(server_resolver, "_ensure_cache", AsyncMock()), \
         patch.object(server_resolver, "_lookup_server", AsyncMock(return_value=99)) as mock_lookup:
        result = await server_resolver.resolve_server(
            raw_title="Firemaw [EU - Classic Era] - Horde",
            source="g2g",
            pool=mock_pool,
        )

    mock_lookup.assert_called_once()
    call_args = mock_lookup.call_args
    version_used = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs.get("version")
    assert version_used == "Classic Era", \
        f"Expected 'Classic Era' from title bracket, got: {version_used!r}"
    assert result == 99


@pytest.mark.asyncio
async def test_resolve_alias_hit_wrong_version_falls_through():
    """If alias cache returns a Classic Era server but game_version='MoP Classic',
    resolver must skip that alias hit and fall through to fuzzy resolve."""
    from db import server_resolver
    from unittest.mock import AsyncMock, patch, MagicMock

    mock_pool = MagicMock()
    classic_era_id = 10
    mop_id = 20
    fake_server_data = {
        classic_era_id: {
            "id":      classic_era_id,
            "version": "Classic Era",
            "name":    "Firemaw",
            "region":  "EU",
        },
    }

    with patch.object(server_resolver, "_alias_cache",
                      {"firemaw [eu - classic era] - horde": classic_era_id}), \
         patch.object(server_resolver, "_server_data_cache", fake_server_data), \
         patch.object(server_resolver, "_ensure_cache", AsyncMock()), \
         patch.object(server_resolver, "_lookup_server", AsyncMock(return_value=mop_id)):
        result = await server_resolver.resolve_server(
            raw_title="Firemaw [EU - Classic Era] - Horde",
            source="g2g",
            pool=mock_pool,
            game_version="MoP Classic",
        )

    assert result == mop_id, \
        f"Expected MoP server_id={mop_id} after alias version mismatch, got {result}"
