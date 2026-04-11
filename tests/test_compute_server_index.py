"""Tests for per-server index: top-2 per platform, VWAP, best ask."""
from __future__ import annotations

from service import offers_service as osvc


def test_compute_server_index_insufficient_offers_returns_none(make_offer):
    """Fewer than _MIN_OFFERS matching rows → None."""
    offers = [
        make_offer(id="a", server_id=1, source="g2g", raw_price=0.01, amount_gold=1000),
    ]
    assert osvc.compute_server_index(1, "Horde", offers) is None


def test_compute_server_index_single_platform_two_cheapest(make_offer):
    """Only g2g: take two cheapest by price_per_1k."""
    offers = [
        make_offer(id="a", server_id=1, source="g2g", raw_price=0.02, amount_gold=1000),  # 20 per 1k
        make_offer(id="b", server_id=1, source="g2g", raw_price=0.015, amount_gold=1000),  # 15
        make_offer(id="c", server_id=1, source="g2g", raw_price=0.01, amount_gold=1000),  # 10
    ]
    r = osvc.compute_server_index(1, "Horde", offers)
    assert r is not None
    assert r["sample_size"] == 2
    # top-2: 10, 15
    assert abs(r["index_price"] * 1000 - 12.5) < 1e-3  # mean 12.5 per 1k → per unit 0.0125
    assert abs(r["best_ask"] * 1000 - 10.0) < 1e-6
    assert abs(r["min_price"] * 1000 - 10.0) < 1e-6
    assert abs(r["max_price"] * 1000 - 15.0) < 1e-6


def test_compute_server_index_two_platforms_top2_each(make_offer):
    """Two sources × top-2; index is mean of the four cheapest-by-platform picks."""
    offers = [
        make_offer(id="g1", server_id=1, source="g2g", raw_price=0.01, amount_gold=1000),   # 10 / 1k
        make_offer(id="g2", server_id=1, source="g2g", raw_price=0.015, amount_gold=1000),  # 15
        make_offer(id="f1", server_id=1, source="funpay", raw_price=0.011, amount_gold=500),  # 11 / 1k
        make_offer(id="f2", server_id=1, source="funpay", raw_price=0.02, amount_gold=500),   # 20
    ]
    r = osvc.compute_server_index(1, "Horde", offers)
    assert r is not None
    assert r["sample_size"] == 4
    # Sorted prices: 10, 11, 15, 20
    assert abs(r["best_ask"] * 1000 - 10.0) < 1e-5
    assert abs(r["index_price"] * 1000 - 14.0) < 1e-2  # mean(10,11,15,20)


def test_compute_server_index_wrong_server_id_none(make_offer):
    offers = [
        make_offer(id="a", server_id=2, source="g2g", raw_price=0.01, amount_gold=1000),
        make_offer(id="b", server_id=2, source="g2g", raw_price=0.012, amount_gold=1000),
    ]
    assert osvc.compute_server_index(1, "Horde", offers) is None


def test_compute_server_index_faction_all_includes_both(make_offer):
    """Faction 'All' keeps Horde + Alliance offers for same server."""
    offers = [
        make_offer(id="a", server_id=1, faction="Horde", source="g2g", raw_price=0.01, amount_gold=1000),
        make_offer(id="b", server_id=1, faction="Alliance", source="g2g", raw_price=0.012, amount_gold=1000),
    ]
    r = osvc.compute_server_index(1, "All", offers)
    assert r is not None and r["sample_size"] == 2


def test_compute_server_index_faction_horde_excludes_alliance(make_offer):
    offers = [
        make_offer(id="a", server_id=1, faction="Horde", source="g2g", raw_price=0.01, amount_gold=1000),
        make_offer(id="b", server_id=1, faction="Alliance", source="g2g", raw_price=0.009, amount_gold=1000),
    ]
    assert osvc.compute_server_index(1, "Horde", offers) is None  # only one Horde offer


def test_compute_server_index_two_sources_one_offer_each_ok(make_offer):
    """One listing per platform → two rows in top, enough for _MIN_OFFERS."""
    offers = [
        make_offer(id="a", server_id=1, source="g2g", raw_price=0.01, amount_gold=1000),
        make_offer(id="b", server_id=1, source="funpay", raw_price=0.012, amount_gold=1000),
    ]
    assert osvc.compute_server_index(1, "Horde", offers) is not None


def test_compute_server_index_single_listing_returns_none(make_offer):
    """Only one matching offer worldwide → cannot form top-2 sample."""
    offers_one = [make_offer(id="a", server_id=1, source="g2g", raw_price=0.01, amount_gold=1000)]
    assert osvc.compute_server_index(1, "Horde", offers_one) is None
