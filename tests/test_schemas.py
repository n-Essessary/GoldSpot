from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.schemas import Offer


def _base(**overrides):
    d = dict(
        id="1",
        source="g2g",
        server="(EU) Anniversary",
        display_server="(EU) Anniversary",
        server_name="Spineshatter",
        faction="Horde",
        amount_gold=1000,
        seller="seller",
        offer_url="https://x",
        updated_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc),
    )
    d.update(overrides)
    return d


def test_offer_per_lot_derives_price_per_1k():
    o = Offer(**_base(raw_price_unit="per_lot", lot_size=1000, raw_price=3.0))
    assert o.price_per_1k == 3.0


def test_offer_per_unit_derives_price_per_1k():
    o = Offer(**_base(raw_price_unit="per_unit", lot_size=1, raw_price=0.003))
    assert o.price_per_1k == 3.0


def test_offer_legacy_backfills_raw_price():
    o = Offer(**_base(raw_price=0.0, price_per_1k=3.0))
    assert o.raw_price == 0.003


def test_offer_price_per_1k_non_positive_raises():
    with pytest.raises(ValueError):
        Offer(**_base(raw_price=0.0, price_per_1k=0.0))


def test_offer_amount_gold_non_positive_raises():
    with pytest.raises(ValueError):
        Offer(**_base(raw_price=0.003, amount_gold=0))


def test_offer_naive_datetime_raises():
    with pytest.raises(ValueError):
        Offer(**_base(raw_price=0.003, updated_at=datetime.now(), fetched_at=datetime.now()))
