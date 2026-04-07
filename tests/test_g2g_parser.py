from __future__ import annotations

import pytest

from parser.g2g_parser import G2GOffer, _build_offer_url, _dedupe, _parse_title, _to_offer
from service.offers_service import _normalize_g2g_offer


@pytest.mark.parametrize(
    "title,expected",
    [
        (
            "Spineshatter [EU - Anniversary] - Alliance",
            ("Spineshatter", "EU", "Anniversary", "Alliance"),
        ),
        (
            "Lava Lash [EU - Seasonal] - Horde",
            ("Lava Lash", "EU", "Seasonal", "Horde"),
        ),
        (
            "Firemaw [EU] - Alliance",
            ("Firemaw", "EU", "Classic", "Alliance"),
        ),
        (
            "Classic Era Gold EU",
            ("Classic Era Gold EU", "EU", "Classic Era", "Horde"),
        ),
        (
            "",
            ("", "", "", "Horde"),
        ),
    ],
)
def test_parse_title_cases(title, expected):
    assert _parse_title(title) == expected


def test_parse_title_none_safe():
    out = _parse_title(None)  # type: ignore[arg-type]
    assert isinstance(out, tuple) and len(out) == 4


def test_parse_title_seasonal_normalizes_after_service_step(make_offer):
    raw = G2GOffer(
        offer_id="a",
        title="Lava Lash [EU - Seasonal] - Horde",
        server_name="Lava Lash",
        region_id="eu",
        relation_id="r",
        price_usd=0.003,
        min_qty=1,
        available_qty=10,
        seller="s",
        brand_id="b",
        service_id="svc",
    )
    offer = _to_offer(raw, make_offer().updated_at)
    assert offer is not None
    normalized = _normalize_g2g_offer(offer)
    assert normalized.display_server == "(EU) Season of Discovery"


def test_to_offer_price_zero_returns_none(make_offer):
    raw = G2GOffer("a", "x [EU - Classic] - Horde", "x", "eu", "r", 0.0, 1, 10, "s", "b", "svc")
    assert _to_offer(raw, make_offer().updated_at) is None


def test_to_offer_available_qty_zero_returns_none(make_offer):
    raw = G2GOffer("a", "x [EU - Classic] - Horde", "x", "eu", "r", 0.003, 1, 0, "s", "b", "svc")
    assert _to_offer(raw, make_offer().updated_at) is None


def test_to_offer_unrecognized_title_returns_none(make_offer):
    raw = G2GOffer("a", "Unrecognized title", "x", "eu", "r", 0.003, 1, 10, "s", "b", "svc")
    assert _to_offer(raw, make_offer().updated_at) is None


def test_to_offer_seasonal_version_canonicalized(make_offer):
    raw = G2GOffer("a", "Lava Lash [EU - Seasonal] - Horde", "Lava Lash", "eu", "r", 0.003, 1, 10, "s", "b", "svc")
    offer = _to_offer(raw, make_offer().updated_at)
    assert offer is not None and offer.display_server == "(EU) Season of Discovery"


def test_to_offer_skip_qty_check(make_offer):
    raw = G2GOffer("a", "Firemaw [EU - Classic] - Horde", "Firemaw", "eu", "r", 0.003, 1, 0, "s", "b", "svc")
    assert _to_offer(raw, make_offer().updated_at) is None
    assert _to_offer(raw, make_offer().updated_at, skip_qty_check=True) is not None


def test_normalize_g2g_offer_hardcore(make_offer):
    offer = make_offer(source="g2g", display_server="(EU) Hardcore", server="(eu) hardcore")
    normalized = _normalize_g2g_offer(offer)
    assert normalized.display_server == "(EU) Hardcore"


def test_dedupe_removes_exact_duplicate_offer_ids(make_offer):
    a = make_offer(id="dup", source="g2g")
    b = make_offer(id="dup", source="g2g", seller="x")
    assert len(_dedupe([a, b])) == 1


# ── Bug 1: sort=lowest_price in buy URLs ──────────────────────────────────────

def test_build_offer_url_group_ends_with_sort_param():
    """Group buy URL must end with &sort=lowest_price."""
    attrs = [{"collection_id": "lgc_col", "dataset_id": "lgc_dat"}]
    url = _build_offer_url("offer123", attrs, "region-abc")
    assert url.endswith("&sort=lowest_price"), f"URL did not end with sort param: {url}"
    assert "fa=" in url
    assert "region_id=region-abc" in url


def test_build_offer_url_fallback_uses_offer_id():
    """Fallback URL (no offer_attributes) returns /offer/{offer_id}."""
    url = _build_offer_url("offer123", [], "region-abc")
    assert "/offer/offer123" in url


# ── Bug 2A: Hardcore in _VERSION_PATTERNS ────────────────────────────────────

def test_parse_title_hardcore():
    """Soulseeker [EU - Hardcore] must parse version='Hardcore'."""
    server, region, version, faction = _parse_title("Soulseeker [EU - Hardcore] - Alliance")
    assert server  == "Soulseeker"
    assert region  == "EU"
    assert version == "Hardcore"
    assert faction == "Alliance"


def test_parse_title_hardcore_horde():
    server, region, version, faction = _parse_title("Soulseeker [EU - Hardcore] - Horde")
    assert version == "Hardcore"
    assert faction == "Horde"


# ── Bug 2B: AU realm region override ─────────────────────────────────────────

def test_parse_title_penance_overrides_to_au():
    """Penance must always return region=AU regardless of bracket region."""
    server, region, version, faction = _parse_title("Penance [US - Seasonal] - Horde")
    assert server  == "Penance"
    assert region  == "AU"
    assert version == "Season of Discovery"


def test_parse_title_penance_ru_overrides_to_au():
    """Penance with RU bracket must still return AU."""
    server, region, version, faction = _parse_title("Penance [RU - Seasonal] - Alliance")
    assert region  == "AU"
    assert version == "Season of Discovery"
    assert faction == "Alliance"


def test_parse_title_shadowstrike_overrides_to_au():
    """Shadowstrike must always return region=AU."""
    server, region, version, faction = _parse_title("Shadowstrike [RU - Seasonal] - Alliance")
    assert server  == "Shadowstrike"
    assert region  == "AU"
    assert version == "Season of Discovery"


def test_parse_title_shadowstrike_us_overrides_to_au():
    server, region, version, faction = _parse_title("Shadowstrike [US - Seasonal] - Horde")
    assert region  == "AU"
    assert version == "Season of Discovery"
