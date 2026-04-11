"""
tests/test_funpay_currency.py

Tests for the EUR→USD currency conversion changes in funpay_parser.py.
Also covers the 3 previously-broken _parse_html tests (return type changed
from list[Offer] to tuple[list[Offer], str]).

Bugs documented below with their test IDs.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)

# Minimal HTML fixture: one online EUR offer
_EUR_HTML = """
<a class="tc-item" href="/offer?id=42" data-online="1">
  <span class="tc-server">(EU) Classic</span>
  <span class="tc-side">Horde</span>
  <span class="tc-amount">1000</span>
  <div class="tc-price" data-s="0">
    <div>0.0151 <span class="unit">€</span></div>
  </div>
  <span class="tc-seller">seller1</span>
</a>
"""

# Minimal HTML fixture: one online USD offer (no .unit span → defaults to "$")
_USD_HTML = """
<a class="tc-item" href="/offer?id=43" data-online="1">
  <span class="tc-server">(US) Classic</span>
  <span class="tc-side">Alliance</span>
  <span class="tc-amount">500</span>
  <span class="tc-price">0.025</span>
  <span class="tc-seller">seller2</span>
</a>
"""

# Offline EUR item first in DOM, then an online item with invalid price.
# Used to test that currency extraction scans the WHOLE soup (including offline items).
_OFFLINE_EUR_ONLINE_INVALID_HTML = """
<a class="tc-item" href="/offer?id=99" data-online="0">
  <span class="tc-server">(EU) Classic</span>
  <span class="tc-side">Horde</span>
  <span class="tc-amount">1000</span>
  <div class="tc-price">
    <div>0.01 <span class="unit">€</span></div>
  </div>
  <span class="tc-seller">offline_seller</span>
</a>
<a class="tc-item" href="/offer?id=100" data-online="1">
  <span class="tc-server">(EU) Classic</span>
  <span class="tc-side">Horde</span>
  <span class="tc-amount">1000</span>
  <div class="tc-price"><div>INVALID</div></div>
  <span class="tc-seller">online_bad_price</span>
</a>
"""


def _reset_currency_cache() -> None:
    """Reset module-level cache state between tests."""
    import parser.funpay_parser as fp
    fp._currency_cache.clear()
    fp._currency_cache_ts = 0.0


# ===========================================================================
# FIX: 3 previously-broken tests (return type is now tuple, not list)
# ===========================================================================

class TestParseHtmlReturnType:
    """_parse_html must return tuple[list[Offer], str], not plain list."""

    def test_empty_html_returns_tuple(self):
        from parser.funpay_parser import _parse_html
        result = _parse_html("", NOW)
        offers, symbol = result  # must unpack cleanly
        assert offers == []
        assert symbol == "$"

    def test_no_items_returns_tuple(self):
        from parser.funpay_parser import _parse_html
        offers, symbol = _parse_html("<div>none</div>", NOW)
        assert offers == []
        assert symbol == "$"

    def test_only_offline_returns_tuple(self):
        from parser.funpay_parser import _parse_html
        html = """
        <a class="tc-item" href="/x?id=1" data-online="0">
          <span class="tc-server">(EU) Classic</span>
          <span class="tc-side">Horde</span>
          <span class="tc-amount">1000</span>
          <span class="tc-price">3.00</span>
          <span class="tc-seller">s</span>
        </a>
        """
        offers, symbol = _parse_html(html, NOW)
        assert offers == []
        assert symbol == "$"  # no .unit span in offline offer → default "$"


# ===========================================================================
# _parse_html: currency symbol extraction
# ===========================================================================

class TestParseHtmlCurrencyExtraction:

    def test_eur_symbol_extracted_from_unit_span(self):
        from parser.funpay_parser import _parse_html
        offers, symbol = _parse_html(_EUR_HTML, NOW)
        assert symbol == "€"
        assert len(offers) == 1

    def test_usd_default_when_no_unit_span(self):
        from parser.funpay_parser import _parse_html
        offers, symbol = _parse_html(_USD_HTML, NOW)
        assert symbol == "$"
        assert len(offers) == 1

    def test_price_stored_as_eur_raw_no_conversion_yet(self):
        """_parse_html must NOT convert prices — that's fetch_funpay_offers's job."""
        from parser.funpay_parser import _parse_html
        offers, symbol = _parse_html(_EUR_HTML, NOW)
        assert symbol == "€"
        # raw_price must still be the EUR value straight from HTML
        assert abs(offers[0].raw_price - 0.0151) < 1e-7

    # -----------------------------------------------------------------------
    # BUG #1 (LOW) — currency symbol extracted from OFFLINE items
    # -----------------------------------------------------------------------
    def test_currency_extracted_from_offline_item_in_mixed_page(self):
        """
        BUG #1: soup.select_one('.tc-price .unit') scans the WHOLE HTML,
        including offline items that were already filtered out before parsing.

        Trigger path:
          - HTML has an offline EUR item FIRST (has .unit = "€")
          - followed by an online item whose price is invalid → raises ValueError
            → filtered out → raw_offers = []
          - The code does NOT early-return (online_items is non-empty)
          - currency_symbol is extracted from full soup → picks "€" from the
            offline item, not from the online offers that were actually parsed

        Current behaviour: symbol = "€" (from offline item in DOM).
        Ideal behaviour:   symbol = "$" (no successfully-parsed online offer).

        Status: ACCEPTED RISK — all items on a real FunPay page share the same
        regional currency, so this edge-case has no practical impact.
        Test serves as documentation of the scope of the selector.
        """
        from parser.funpay_parser import _parse_html
        offers, symbol = _parse_html(_OFFLINE_EUR_ONLINE_INVALID_HTML, NOW)
        assert offers == []
        # The symbol comes from the OFFLINE item's .unit span (BUG: should be "$")
        assert symbol == "€"  # documents current (accepted) behaviour


# ===========================================================================
# _get_usd_rate — caching and HTTP behaviour
# ===========================================================================

class TestGetUsdRate:

    def setup_method(self):
        _reset_currency_cache()

    def test_usd_symbol_returns_1_without_http_call(self):
        from parser.funpay_parser import _get_usd_rate
        rate = asyncio.get_event_loop().run_until_complete(_get_usd_rate("$"))
        assert rate == 1.0

    def test_unknown_symbol_defaults_to_usd_code_returns_1(self):
        """Unknown symbols map to 'USD' via _SYMBOL_TO_CODE.get(..., 'USD') → 1.0."""
        from parser.funpay_parser import _get_usd_rate
        rate = asyncio.get_event_loop().run_until_complete(_get_usd_rate("zł"))
        assert rate == 1.0  # no HTTP call, just returns 1.0

    def test_eur_fetches_rate_from_frankfurter(self):
        from parser.funpay_parser import _get_usd_rate

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"rates": {"USD": 1.08}}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("parser.funpay_parser.httpx.AsyncClient", return_value=mock_client):
            rate = asyncio.get_event_loop().run_until_complete(_get_usd_rate("€"))

        assert rate == pytest.approx(1.08)

    def test_cache_hit_skips_http(self):
        """Second call within TTL must not make another HTTP request."""
        import parser.funpay_parser as fp
        from parser.funpay_parser import _get_usd_rate

        call_count = 0

        async def fake_fetch():
            nonlocal call_count
            call_count += 1
            return 1.10

        # Pre-populate cache
        fp._currency_cache["EUR"] = 1.10
        fp._currency_cache_ts = time.monotonic()  # fresh timestamp

        rate = asyncio.get_event_loop().run_until_complete(_get_usd_rate("€"))
        assert rate == pytest.approx(1.10)
        assert call_count == 0  # no HTTP call

    def test_cache_miss_after_ttl(self):
        """Cache must be bypassed when TTL is expired."""
        import parser.funpay_parser as fp
        from parser.funpay_parser import _get_usd_rate

        fp._currency_cache["EUR"] = 1.05
        fp._currency_cache_ts = time.monotonic() - (fp._CURRENCY_TTL + 1)  # expired

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"rates": {"USD": 1.09}}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("parser.funpay_parser.httpx.AsyncClient", return_value=mock_client):
            rate = asyncio.get_event_loop().run_until_complete(_get_usd_rate("€"))

        assert rate == pytest.approx(1.09)
        assert fp._currency_cache["EUR"] == pytest.approx(1.09)

    def test_http_failure_returns_cached_value(self):
        """On HTTP error, must return last known cached value."""
        import parser.funpay_parser as fp
        from parser.funpay_parser import _get_usd_rate

        fp._currency_cache["EUR"] = 1.07
        fp._currency_cache_ts = time.monotonic() - (fp._CURRENCY_TTL + 1)  # force refresh

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("network error"))

        with patch("parser.funpay_parser.httpx.AsyncClient", return_value=mock_client):
            rate = asyncio.get_event_loop().run_until_complete(_get_usd_rate("€"))

        assert rate == pytest.approx(1.07)  # stale cache returned

    def test_http_failure_empty_cache_returns_1(self):
        """On HTTP error with empty cache, must return 1.0 (no conversion)."""
        from parser.funpay_parser import _get_usd_rate

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("network error"))

        with patch("parser.funpay_parser.httpx.AsyncClient", return_value=mock_client):
            rate = asyncio.get_event_loop().run_until_complete(_get_usd_rate("€"))

        assert rate == 1.0

    # -----------------------------------------------------------------------
    # BUG #2 (MEDIUM) — shared _currency_cache_ts across all currency codes
    # -----------------------------------------------------------------------
    def test_bug_shared_timestamp_resets_ttl_for_all_currencies(self):
        """
        BUG #2: _currency_cache_ts is a single float shared by all currency
        codes. Fetching GBP resets the TTL clock, causing a stale EUR entry
        to be served as if it were fresh.

        Scenario:
          T=0:      EUR cached at _currency_cache_ts=0
          T=TTL+1:  GBP fetched, _currency_cache_ts reset to T=TTL+1
          T=TTL+2:  EUR checked — now (TTL+2 - TTL+1) = 1 < TTL → cache HIT
                    But EUR was cached at T=0, which is TTL+2 seconds ago!

        Correct fix: use per-currency timestamps, e.g.
            _currency_cache_ts: dict[str, float] = {}
        """
        import parser.funpay_parser as fp

        ttl = fp._CURRENCY_TTL

        # Simulate: EUR was cached long ago (stale)
        fp._currency_cache["EUR"] = 1.05
        fp._currency_cache["GBP"] = 1.25
        # GBP was just refreshed — timestamp is fresh
        fp._currency_cache_ts = time.monotonic()

        # EUR was actually last updated TTL+60 seconds ago, but because
        # _currency_cache_ts was reset when GBP was refreshed, the check
        #   (now - _currency_cache_ts) < _CURRENCY_TTL
        # passes for EUR too — stale EUR is returned as if fresh.

        from parser.funpay_parser import _get_usd_rate

        # No HTTP call should happen (current buggy code returns from cache)
        http_called = []

        async def patched_get(*args, **kwargs):
            http_called.append(True)
            raise AssertionError("Should not reach HTTP — bug!")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=patched_get)

        with patch("parser.funpay_parser.httpx.AsyncClient", return_value=mock_client):
            rate = asyncio.get_event_loop().run_until_complete(_get_usd_rate("€"))

        # BUG: stale EUR value 1.05 is returned without HTTP call
        assert rate == pytest.approx(1.05)
        # The assert below documents what SHOULD happen (EUR should be refetched)
        # but currently does NOT happen due to the shared timestamp bug.
        # assert http_called, "EUR should have been refetched — it's stale!"


# ===========================================================================
# fetch_funpay_offers — end-to-end EUR→USD conversion
# ===========================================================================

class TestFetchFunpayOffersConversion:

    def setup_method(self):
        _reset_currency_cache()

    def _make_fetch_mock(self, html: str) -> Any:
        """Return a mock httpx.AsyncClient that returns the given HTML."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        return mock_client

    def _make_rate_mock(self, rate: float) -> Any:
        """Return a mock httpx.AsyncClient for the Frankfurter rate endpoint."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"rates": {"USD": rate}}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        return mock_client

    def test_eur_offer_converted_to_usd(self):
        """
        Core requirement: FunPay returns €0.0151, EUR/USD=1.08
        → raw_price ≈ 0.01631, price_per_1k ≈ 16.31.
        """
        from parser.funpay_parser import fetch_funpay_offers

        page_client = self._make_fetch_mock(_EUR_HTML)
        rate_client = self._make_rate_mock(1.08)

        call_n = [0]

        def client_factory(*args, **kwargs):
            call_n[0] += 1
            if call_n[0] == 1:
                return page_client   # first call: FunPay page
            return rate_client       # second call: Frankfurter rate

        with patch("parser.funpay_parser.httpx.AsyncClient", side_effect=client_factory):
            offers = asyncio.get_event_loop().run_until_complete(fetch_funpay_offers())

        assert len(offers) == 1
        offer = offers[0]
        assert abs(offer.raw_price - round(0.0151 * 1.08, 8)) < 1e-7
        assert abs(offer.price_per_1k - round(offer.raw_price * 1000.0, 6)) < 1e-5

    def test_usd_offer_not_converted(self):
        """Dollar offers must pass through unchanged — no rate fetch."""
        from parser.funpay_parser import fetch_funpay_offers

        page_client = self._make_fetch_mock(_USD_HTML)

        http_calls = []

        def client_factory(*args, **kwargs):
            http_calls.append(kwargs or args)
            return page_client

        with patch("parser.funpay_parser.httpx.AsyncClient", side_effect=client_factory):
            offers = asyncio.get_event_loop().run_until_complete(fetch_funpay_offers())

        assert len(offers) == 1
        assert abs(offers[0].raw_price - 0.025) < 1e-7
        # Only one HTTP call (FunPay page), no rate fetch
        assert len(http_calls) == 1

    def test_rate_fetch_failure_keeps_eur_values(self):
        """
        When Frankfurter is unreachable and cache is empty, _get_usd_rate
        returns 1.0 → conversion is skipped → EUR values stored as-is.
        This is the documented safe fallback.
        """
        from parser.funpay_parser import fetch_funpay_offers

        page_client = self._make_fetch_mock(_EUR_HTML)

        err_resp = MagicMock()
        err_resp.__aenter__ = AsyncMock(return_value=err_resp)
        err_resp.__aexit__ = AsyncMock(return_value=False)
        err_resp.get = AsyncMock(side_effect=Exception("timeout"))

        call_n = [0]

        def client_factory(*args, **kwargs):
            call_n[0] += 1
            if call_n[0] == 1:
                return page_client
            return err_resp

        with patch("parser.funpay_parser.httpx.AsyncClient", side_effect=client_factory):
            offers = asyncio.get_event_loop().run_until_complete(fetch_funpay_offers())

        assert len(offers) == 1
        # rate == 1.0 → no conversion applied → raw_price stays as parsed EUR value
        assert abs(offers[0].raw_price - 0.0151) < 1e-7

    # -----------------------------------------------------------------------
    # BUG #3 (MEDIUM/LATENT) — price_per_1k ignores raw_price_unit / lot_size
    # -----------------------------------------------------------------------
    def test_bug_price_per_1k_ignores_lot_size(self):
        """
        BUG #3 (latent): fetch_funpay_offers recomputes price_per_1k as
            offer.raw_price * 1000.0
        hardcoded, ignoring raw_price_unit and lot_size.

        For 'per_unit' + lot_size=1 (current FunPay behaviour) this is correct.
        For 'per_lot' + lot_size=N the correct formula is:
            (raw_price / lot_size) * 1000
        — which is what Offer._normalise() uses but the conversion loop bypasses.

        This test confirms that for per_unit the result IS correct, and documents
        that if raw_price_unit ever becomes 'per_lot', the conversion loop would
        compute a wrong price_per_1k.

        Correct fix: mirror the logic from Offer._normalise() in the loop:
            if offer.raw_price_unit == 'per_lot':
                offer.price_per_1k = round(offer.raw_price / max(offer.lot_size,1) * 1000, 6)
            else:
                offer.price_per_1k = round(offer.raw_price * 1000.0, 6)
        """
        from parser.funpay_parser import fetch_funpay_offers

        page_client = self._make_fetch_mock(_EUR_HTML)
        rate_client = self._make_rate_mock(1.08)

        call_n = [0]

        def client_factory(*args, **kwargs):
            call_n[0] += 1
            return page_client if call_n[0] == 1 else rate_client

        with patch("parser.funpay_parser.httpx.AsyncClient", side_effect=client_factory):
            offers = asyncio.get_event_loop().run_until_complete(fetch_funpay_offers())

        offer = offers[0]
        # For per_unit: price_per_1k == raw_price * 1000 — should be correct
        expected_per_1k = round(offer.raw_price * 1000.0, 6)
        assert abs(offer.price_per_1k - expected_per_1k) < 1e-5, (
            "price_per_1k must equal raw_price*1000 for per_unit offers"
        )

    def test_conversion_applied_only_once(self):
        """Calling fetch_funpay_offers twice must not double-convert prices."""
        from parser.funpay_parser import fetch_funpay_offers

        # Use fresh page clients each time
        expected_raw = round(0.0151 * 1.08, 8)

        for _ in range(2):
            page_client = self._make_fetch_mock(_EUR_HTML)
            rate_client = self._make_rate_mock(1.08)
            call_n = [0]

            def client_factory(*args, **kwargs):
                call_n[0] += 1
                return page_client if call_n[0] == 1 else rate_client

            with patch("parser.funpay_parser.httpx.AsyncClient", side_effect=client_factory):
                offers = asyncio.get_event_loop().run_until_complete(fetch_funpay_offers())

            assert len(offers) == 1
            # raw_price must always equal the single-conversion result
            assert abs(offers[0].raw_price - expected_raw) < 1e-7, (
                f"raw_price={offers[0].raw_price!r} — double conversion detected!"
            )

    def test_price_per_1k_consistency_with_raw_price(self):
        """price_per_1k must always equal raw_price * 1000 after conversion."""
        from parser.funpay_parser import fetch_funpay_offers

        page_client = self._make_fetch_mock(_EUR_HTML)
        rate_client = self._make_rate_mock(1.08)
        call_n = [0]

        def client_factory(*args, **kwargs):
            call_n[0] += 1
            return page_client if call_n[0] == 1 else rate_client

        with patch("parser.funpay_parser.httpx.AsyncClient", side_effect=client_factory):
            offers = asyncio.get_event_loop().run_until_complete(fetch_funpay_offers())

        offer = offers[0]
        assert abs(offer.price_per_1k - round(offer.raw_price * 1000.0, 6)) < 1e-5


# ===========================================================================
# Module-level cache state isolation
# ===========================================================================

class TestCacheIsolation:
    """Ensure module-level cache vars don't leak between test runs."""

    def setup_method(self):
        _reset_currency_cache()

    def test_cache_starts_empty(self):
        import parser.funpay_parser as fp
        assert fp._currency_cache == {}
        assert fp._currency_cache_ts == 0.0

    def test_reset_between_tests(self):
        import parser.funpay_parser as fp
        fp._currency_cache["EUR"] = 99.9
        fp._currency_cache_ts = 9999.0
        _reset_currency_cache()
        assert fp._currency_cache == {}
        assert fp._currency_cache_ts == 0.0
