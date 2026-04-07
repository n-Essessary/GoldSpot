from __future__ import annotations

import pytest

from parser import g2g_parser
from service import normalize_pipeline as np


def test_g2g_dedupe_same_source_offer_id_keeps_one(make_offer):
    a = make_offer(id="x1", source="g2g")
    b = make_offer(id="x1", source="g2g", seller="other")
    out = g2g_parser._dedupe([a, b])
    assert len(out) == 1


@pytest.mark.asyncio
async def test_normalize_dedup_key_is_source_plus_offer_id(make_offer):
    a = make_offer(id="dup", source="g2g")
    b = make_offer(id="dup", source="funpay")
    normalized, _ = await np.normalize_offer_batch([a, b], pool=None)
    assert len(normalized) == 2


@pytest.mark.asyncio
async def test_duplicate_update_does_not_duplicate(make_offer):
    old = make_offer(id="same", source="g2g", amount_gold=100)
    new = make_offer(id="same", source="g2g", amount_gold=999)
    normalized, _ = await np.normalize_offer_batch([old, new], pool=None)
    assert len(normalized) == 1
