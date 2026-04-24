"""
tests/test_per1k_price_fix.py — Verify Retail/MoP 1000× price inflation fix.

Regression tests for: G2G Retail/MoP unit_price_in_usd is per-1K-gold denomination;
FunPay Retail/MoP .tc-price is also per-1K-gold. Both must use raw_price_unit='per_1k'
so price_per_1k = raw_price (no ×1000 multiplication).

Run from backend/:
    python -m pytest tests/test_per1k_price_fix.py -v
"""

import sys
import os
from datetime import datetime, timezone

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from api.schemas import Offer

_NOW = datetime.now(timezone.utc)


def _make_offer(**kwargs) -> Offer:
    defaults = dict(
        id="test_1",
        source="g2g",
        server="test-server",
        faction="Horde",
        amount_gold=1,
        seller="TestSeller",
        updated_at=_NOW,
        fetched_at=_NOW,
    )
    defaults.update(kwargs)
    return Offer(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# BLOCK 1 — schemas.py: per_1k branch in model_validator
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaPerOnekBranch:
    def test_per_1k_price_passthrough(self):
        """per_1k → price_per_1k == raw_price, no ×1000."""
        offer = _make_offer(raw_price=0.047, raw_price_unit="per_1k", lot_size=1)
        assert offer.price_per_1k == pytest.approx(0.047, rel=1e-6)

    def test_per_unit_still_multiplies(self):
        """per_unit unaffected — Classic price formula unchanged."""
        offer = _make_offer(raw_price=0.003, raw_price_unit="per_unit", lot_size=1)
        assert offer.price_per_1k == pytest.approx(3.0, rel=1e-6)

    def test_per_lot_still_divides(self):
        """per_lot unaffected — FunPay Classic formula unchanged."""
        offer = _make_offer(raw_price=3.0, raw_price_unit="per_lot", lot_size=1000)
        assert offer.price_per_1k == pytest.approx(3.0, rel=1e-6)

    def test_per_1k_retail_range(self):
        """Retail price range $0.03–$0.10 stays in range."""
        for raw in (0.030, 0.047, 0.056, 0.100):
            offer = _make_offer(raw_price=raw, raw_price_unit="per_1k", lot_size=1)
            assert 0.01 < offer.price_per_1k < 0.15, (
                f"raw={raw} → price_per_1k={offer.price_per_1k} out of expected range"
            )

    def test_per_1k_does_not_exceed_classic_range(self):
        """With per_1k, price_per_1k must NOT look like Classic ($1–$5)."""
        offer = _make_offer(raw_price=0.047, raw_price_unit="per_1k", lot_size=1)
        assert offer.price_per_1k < 1.0, "Retail price should be far below Classic range"


# ═══════════════════════════════════════════════════════════════════════════════
# BLOCK 2 — g2g_parser._to_offer(): correct raw_price_unit by game_version
# ═══════════════════════════════════════════════════════════════════════════════

class TestG2GToOffer:
    def _make_raw(self, game_version: str, price_usd: float = 0.047):
        from parser.g2g_parser import G2GOffer
        return G2GOffer(
            offer_id="OID1",
            title=f"Silvermoon [EU - {game_version}] - Horde",
            server_name="Silvermoon",
            region_id="EU",
            relation_id="rel1",
            price_usd=price_usd,
            min_qty=1,
            available_qty=100,
            seller="TestSeller",
            brand_id="lgc_game_2299",
            service_id="lgc_service_1",
            sort="lowest_price",
            game_version=game_version,
        )

    def test_retail_uses_per_1k(self):
        from parser.g2g_parser import _to_offer
        raw = self._make_raw("Retail", price_usd=0.047)
        offer = _to_offer(raw, _NOW, skip_qty_check=True)
        assert offer is not None
        assert offer.raw_price_unit == "per_1k"
        assert offer.price_per_1k == pytest.approx(0.047, rel=1e-6)

    def test_mop_uses_per_1k(self):
        from parser.g2g_parser import _to_offer
        raw = self._make_raw("MoP Classic", price_usd=0.052)
        offer = _to_offer(raw, _NOW, skip_qty_check=True)
        assert offer is not None
        assert offer.raw_price_unit == "per_1k"
        assert offer.price_per_1k == pytest.approx(0.052, rel=1e-6)

    def test_classic_era_uses_per_unit(self):
        from parser.g2g_parser import _to_offer
        raw = self._make_raw("Classic Era", price_usd=0.003)
        offer = _to_offer(raw, _NOW, skip_qty_check=True)
        assert offer is not None
        assert offer.raw_price_unit == "per_unit"
        assert offer.price_per_1k == pytest.approx(3.0, rel=1e-6)

    def test_anniversary_uses_per_unit(self):
        from parser.g2g_parser import _to_offer
        raw = self._make_raw("Anniversary", price_usd=0.002)
        offer = _to_offer(raw, _NOW, skip_qty_check=True)
        assert offer is not None
        assert offer.raw_price_unit == "per_unit"
        assert offer.price_per_1k == pytest.approx(2.0, rel=1e-6)

    def test_retail_not_inflated_1000x(self):
        """price_per_1k must NOT be raw_price * 1000 for Retail."""
        from parser.g2g_parser import _to_offer
        raw = self._make_raw("Retail", price_usd=0.047)
        offer = _to_offer(raw, _NOW, skip_qty_check=True)
        assert offer is not None
        assert offer.price_per_1k != pytest.approx(47.0, abs=0.1), (
            "price_per_1k=47 means 1000× inflation bug is still present"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# BLOCK 3 — funpay_parser denomination fix via raw_price_unit mutation
# ═══════════════════════════════════════════════════════════════════════════════

class TestFunPayPerOnekFix:
    """Test the in-place mutation logic that _fetch_chip applies post-parse."""

    def _make_funpay_offer(self, raw_price: float, amount_gold: int = 1000) -> Offer:
        """Simulates an offer as _parse_item() produces it (per_unit, lot_size=1)."""
        return Offer(
            id="fp_test_1",
            source="funpay",
            server="silvermoon",
            display_server="Silvermoon",
            faction="Horde",
            raw_price=raw_price,
            raw_price_unit="per_unit",  # initial value from _parse_item
            lot_size=1,
            amount_gold=amount_gold,
            seller="TestSeller",
            updated_at=_NOW,
            fetched_at=_NOW,
        )

    def _apply_retail_fix(self, offer: Offer) -> Offer:
        """Mirror the fix applied in _fetch_chip for Retail/MoP."""
        offer.raw_price_unit = "per_1k"
        offer.price_per_1k = round(offer.raw_price, 6)
        return offer

    def test_retail_fix_corrects_price_per_1k(self):
        """After fix: price_per_1k == raw_price, not raw_price * 1000."""
        offer = self._make_funpay_offer(raw_price=0.056)
        # Before fix: inflated
        assert offer.price_per_1k == pytest.approx(56.0, rel=1e-5)
        # Apply fix
        self._apply_retail_fix(offer)
        assert offer.price_per_1k == pytest.approx(0.056, rel=1e-6)

    def test_retail_fix_sets_correct_unit(self):
        offer = self._make_funpay_offer(raw_price=0.047)
        self._apply_retail_fix(offer)
        assert offer.raw_price_unit == "per_1k"

    def test_classic_offer_unchanged(self):
        """Classic offers must NOT be touched — per_unit formula stays."""
        raw_price = 0.003  # price per 1 gold (Classic)
        offer = self._make_funpay_offer(raw_price=raw_price)
        # Classic path: do NOT apply fix
        assert offer.raw_price_unit == "per_unit"
        assert offer.price_per_1k == pytest.approx(3.0, rel=1e-6)

    def test_retail_price_in_expected_range(self):
        """After fix, Retail price_per_1k should be $0.03–$0.10."""
        for raw_price in (0.035, 0.047, 0.056, 0.095):
            offer = self._make_funpay_offer(raw_price=raw_price)
            self._apply_retail_fix(offer)
            assert 0.01 < offer.price_per_1k < 0.15, (
                f"raw_price={raw_price} → price_per_1k={offer.price_per_1k} out of range"
            )

    def test_currency_converted_raw_price_preserved(self):
        """EUR→USD rate applied to raw_price before fix; fix must use converted value."""
        raw_eur = 0.051
        eur_to_usd = 1.08
        offer = self._make_funpay_offer(raw_price=raw_eur)
        # Simulate currency conversion (as _fetch_chip does)
        offer.raw_price = round(raw_eur * eur_to_usd, 8)
        offer.price_per_1k = round(offer.raw_price * 1000.0, 6)  # still wrong
        # Apply fix
        self._apply_retail_fix(offer)
        expected = round(raw_eur * eur_to_usd, 8)
        assert offer.price_per_1k == pytest.approx(expected, rel=1e-6)
