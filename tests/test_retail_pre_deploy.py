"""
tests/test_retail_pre_deploy.py — Pre-deploy static checks for WoW Retail support.

Covers 6 files modified during the Retail implementation:
  canonical_servers.py  · version_utils.py · g2g_parser.py
  funpay_parser.py      · offers_service.py · normalize_pipeline.py

No HTTP requests, no DB connections.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure backend on path (conftest.py already does this, but be explicit)
ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 1 — canonical_servers.py
# ═══════════════════════════════════════════════════════════════════════════════

from db.canonical_servers import CANONICAL_SERVERS, VALID_VERSIONS


def test_retail_in_valid_versions():
    assert "Retail" in VALID_VERSIONS


def test_retail_server_counts():
    retail = [s for s in CANONICAL_SERVERS if s.version == "Retail"]
    by_region: dict[str, int] = {}
    for s in retail:
        by_region[s.region] = by_region.get(s.region, 0) + 1
    assert len(retail) == 514, f"Expected 514, got {len(retail)}"
    assert by_region.get("EU") == 248, f"EU: {by_region.get('EU')}"
    assert by_region.get("US") == 234, f"US: {by_region.get('US')}"
    assert by_region.get("OCE") == 12, f"OCE: {by_region.get('OCE')}"
    assert by_region.get("RU") == 20, f"RU: {by_region.get('RU')}"


def test_no_duplicate_retail_servers():
    retail = [s for s in CANONICAL_SERVERS if s.version == "Retail"]
    seen: set[tuple[str, str]] = set()
    for s in retail:
        key = (s.name.lower(), s.region.upper())
        assert key not in seen, f"Duplicate Retail server: {s.name} [{s.region}]"
        seen.add(key)


def test_retail_valid_regions():
    from db.canonical_servers import VALID_REGIONS
    retail = [s for s in CANONICAL_SERVERS if s.version == "Retail"]
    for s in retail:
        assert s.region in VALID_REGIONS, \
            f"Invalid region {s.region!r} for {s.name}"


def test_all_servers_valid_versions():
    for s in CANONICAL_SERVERS:
        assert s.version in VALID_VERSIONS, \
            f"Invalid version {s.version!r} for {s.name}"


# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 2 — version_utils.py
# ═══════════════════════════════════════════════════════════════════════════════

from utils.version_utils import _VERSION_ALIASES, _canonicalize_version


def test_retail_version_aliases():
    assert _canonicalize_version("retail") == "Retail"
    assert _canonicalize_version("Retail") == "Retail"
    assert _canonicalize_version("midnight") == "Retail"
    assert _canonicalize_version("the war within") == "Retail"
    assert _canonicalize_version("tww") == "Retail"


def test_existing_aliases_unchanged():
    # Existing aliases must still work
    assert _canonicalize_version("seasonal") == "Season of Discovery"
    assert _canonicalize_version("anniversary") == "Anniversary"
    assert _canonicalize_version("mop classic") == "MoP Classic"
    assert _canonicalize_version("classic era") == "Classic Era"


# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 3 — g2g_parser.py
# ═══════════════════════════════════════════════════════════════════════════════

from parser.g2g_parser import (
    GAME_CONFIGS, RETAIL_CONFIG, _RETAIL_MAX_PAGES,
    fetch_retail_offers, _fetch_sort_retail, _parse_title,
)


def test_retail_config_fields():
    assert RETAIL_CONFIG.key == "wow_retail"
    assert RETAIL_CONFIG.seo_term == "wow-gold"
    assert RETAIL_CONFIG.brand_id == "lgc_game_2299"
    assert RETAIL_CONFIG.game_version == "Retail"
    assert RETAIL_CONFIG.service_id == "lgc_service_1"


def test_retail_max_pages():
    assert _RETAIL_MAX_PAGES == 25
    assert _RETAIL_MAX_PAGES > 10  # must be > Classic's _MAX_PAGES


def test_existing_game_configs_unchanged():
    keys = [c.key for c in GAME_CONFIGS]
    assert "wow_classic_era" in keys
    assert "wow_mop_classic" in keys
    # RETAIL_CONFIG is separate — not in GAME_CONFIGS
    assert "wow_retail" not in keys


def test_parse_title_retail_bracket_format():
    # Standard Retail G2G format — same as Classic
    server, region, version, faction = _parse_title("Bonechewer [US] - Horde")
    assert server == "Bonechewer"
    assert region == "US"
    assert faction == "Horde"


def test_parse_title_retail_eu_subregion():
    server, region, version, faction = _parse_title("Archimonde [FR] - Horde")
    assert server == "Archimonde"
    assert region == "FR"
    assert faction == "Horde"


def test_parse_title_retail_ru_format():
    # RU Retail: Cyrillic with English in parens — Level 2 fallback
    server, region, version, faction = _parse_title("Гордунни (Gordunni) - Horde")
    # Level 2 fallback: server_name extracted from part before "("
    # Result: server may be Cyrillic part or full string — key is faction correct
    assert faction == "Horde"
    # Server name must be non-empty
    assert server != ""


def test_fetch_retail_offers_is_async():
    import asyncio
    assert asyncio.iscoroutinefunction(fetch_retail_offers)


def test_fetch_retail_offers_signature():
    import inspect
    sig = inspect.signature(fetch_retail_offers)
    params = sig.parameters
    assert "sort" in params
    assert "semaphore_limit" in params
    assert params["sort"].default == "lowest_price"
    assert params["semaphore_limit"].default == 30


# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 4 — funpay_parser.py
# ═══════════════════════════════════════════════════════════════════════════════

from parser.funpay_parser import CHIP_CONFIGS


def test_retail_chip_configs():
    retail = [c for c in CHIP_CONFIGS if c.game_version == "Retail"]
    assert len(retail) == 2, f"Expected 2 Retail configs, got {len(retail)}"
    chip_ids = {c.chip_id for c in retail}
    assert chip_ids == {2, 25}, f"Expected {{2, 25}}, got {chip_ids}"


def test_retail_chip_regions():
    eu = next(c for c in CHIP_CONFIGS if c.chip_id == 2)
    us = next(c for c in CHIP_CONFIGS if c.chip_id == 25)
    assert eu.region == "EU"
    assert us.region == "US"
    assert eu.game_version == "Retail"
    assert us.game_version == "Retail"


def test_existing_chips_unchanged():
    classic = next(c for c in CHIP_CONFIGS if c.chip_id == 114)
    assert classic.game_version == "Classic Era"
    mop_eu = next(c for c in CHIP_CONFIGS if c.chip_id == 146)
    assert mop_eu.game_version == "MoP Classic"


# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 5 — offers_service.py
# ═══════════════════════════════════════════════════════════════════════════════

from service.offers_service import (
    _cache, _last_update, _running, _cache_version,
    _last_error, _cache_initialized,
    get_all_offers, get_parser_status,
    _game_version_from_display, _VERSION_ORDER,
    start_background_parsers,
)


def test_g2g_retail_in_all_state_dicts():
    assert "g2g_retail" in _cache
    assert "g2g_retail" in _last_update
    assert "g2g_retail" in _running
    assert "g2g_retail" in _cache_version
    assert "g2g_retail" in _last_error
    assert "g2g_retail" in _cache_initialized


def test_existing_keys_unchanged():
    for key in ("funpay", "g2g"):
        assert key in _cache
        assert key in _last_update
        assert key in _running


def test_version_order_has_retail():
    assert "Retail" in _VERSION_ORDER
    # Retail should sort before Anniversary
    assert _VERSION_ORDER["Retail"] < _VERSION_ORDER["Anniversary"]
    # MoP Classic should sort before Retail
    assert _VERSION_ORDER["MoP Classic"] < _VERSION_ORDER["Retail"]


def test_game_version_from_display_retail():
    assert _game_version_from_display("(EU) Retail") == "Retail"
    assert _game_version_from_display("(US) Retail") == "Retail"


def test_game_version_from_display_existing():
    assert _game_version_from_display("(EU) MoP Classic") == "MoP Classic"
    assert _game_version_from_display("(EU) Anniversary") == "Classic Era"


def test_get_parser_status_includes_retail():
    status = get_parser_status()
    assert "g2g_retail" in status
    assert "funpay" in status
    assert "g2g" in status


def test_start_background_parsers_defined():
    import asyncio
    assert asyncio.iscoroutinefunction(start_background_parsers)


# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 6 — normalize_pipeline.py
# ═══════════════════════════════════════════════════════════════════════════════

from service.normalize_pipeline import normalize_offer_batch, _build_alias_key
import inspect


def test_normalize_pipeline_imports_cleanly():
    # Should not raise
    from service.normalize_pipeline import (
        normalize_offer_batch, QuarantinedOffer,
        _build_alias_key, _collect_resolve_keys,
        _apply_canonical, _validate_and_default,
    )


def test_versioned_games_includes_retail():
    """_versioned_games local set in normalize_offer_batch must include Retail."""
    import ast
    pipeline_path = ROOT / "backend" / "service" / "normalize_pipeline.py"
    src = pipeline_path.read_text()

    # Quick textual check first
    assert "_versioned_games" in src, "_versioned_games not found in normalize_pipeline.py"

    # AST walk: find a set node that contains the string "Retail"
    tree = ast.parse(src)
    retail_in_set = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Set):
            for elt in node.elts:
                if isinstance(elt, ast.Constant) and elt.value == "Retail":
                    retail_in_set = True
                    break
        if retail_in_set:
            break

    assert retail_in_set, (
        '"Retail" not found in any set literal in normalize_pipeline.py. '
        "_versioned_games must include Retail for correct game-version-scoped resolution."
    )
