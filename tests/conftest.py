from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture
def make_offer():
    def _factory(**overrides):
        from api.schemas import Offer

        base = dict(
            id="o1",
            source="g2g",
            server="(EU) Anniversary",
            display_server="(EU) Anniversary",
            server_name="Spineshatter",
            server_id=1,
            faction="Horde",
            raw_price=0.003,
            raw_price_unit="per_unit",
            lot_size=1,
            amount_gold=1000,
            seller="seller",
            offer_url="https://example.com/offer/1",
            updated_at=datetime.now(timezone.utc),
            fetched_at=datetime.now(timezone.utc),
        )
        base.update(overrides)
        return Offer(**base)

    return _factory
