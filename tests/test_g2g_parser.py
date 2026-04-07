from __future__ import annotations

import pytest

from parser.g2g_parser import G2GOffer, _dedupe, _parse_title, _to_offer
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


def test_dedupe_removes_exact_duplicate_offer_ids(make_offer):
    a = make_offer(id="dup", source="g2g")
    b = make_offer(id="dup", source="g2g", seller="x")
    assert len(_dedupe([a, b])) == 1
