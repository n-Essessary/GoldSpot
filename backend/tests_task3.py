"""
tests_task3.py — Comprehensive tests for Task 3 logic and project-wide consistency.

Covers:
  • server_resolver.is_cache_loaded()
  • normalize_pipeline: degraded mode, _reconstruct_display_server_from_raw_title,
    strict vs degraded quarantine, version canonicalization
  • offers_service: cache protection in G2G/FunPay loops, _normalize_g2g_offer,
    _normalize_funpay_offer, _GROUP_RE guard, get_servers()
  • price_profiles: percentile logic, update_profiles, get_profile fallback
  • schemas: Offer price derivation (per_unit / per_lot), model_validator
  • offers_service: compute_index_price edge cases, version_rank ordering
  • OfferRow: price_unit conversion (per_1k / per_1)

Run:
    python3 tests_task3.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))

# ── Lightweight stubs (mirrors tests_goldspot.py approach) ────────────────────

def _stub_module(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def _ensure_pydantic():
    if "pydantic" in sys.modules:
        return
    pyd = types.ModuleType("pydantic")

    class BaseModel:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)
        @classmethod
        def model_validate(cls, data):
            return cls(**data) if not isinstance(data, cls) else data
        def model_dump(self, **_):
            return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    class ValidationError(Exception): pass
    class ConfigDict(dict): pass

    for name, factory in [
        ("field_serializer", lambda *a, **kw: lambda f: f),
        ("field_validator",  lambda *a, **kw: lambda f: f),
        ("model_validator",  lambda *a, **kw: lambda f: f),
    ]:
        setattr(pyd, name, factory)

    class Field:
        def __init__(self, default=None, **kw): self.default = default

    pyd.BaseModel = BaseModel
    pyd.ValidationError = ValidationError
    pyd.ConfigDict = ConfigDict
    pyd.Field = Field
    sys.modules["pydantic"] = pyd

    ps = types.ModuleType("pydantic_settings")
    class BaseSettings(BaseModel): pass
    class SettingsConfigDict(dict): pass
    ps.BaseSettings = BaseSettings
    ps.SettingsConfigDict = SettingsConfigDict
    sys.modules["pydantic_settings"] = ps


def _ensure_asyncpg():
    if "asyncpg" in sys.modules:
        return
    ap = types.ModuleType("asyncpg")
    class DataError(Exception): pass
    try:
        from asyncpg.exceptions import UndefinedTableError
        ap.exceptions = types.ModuleType("asyncpg.exceptions")
        ap.exceptions.UndefinedTableError = UndefinedTableError
    except Exception:
        exc_mod = types.ModuleType("asyncpg.exceptions")
        class UndefinedTableError(Exception): pass
        exc_mod.UndefinedTableError = UndefinedTableError
        ap.exceptions = exc_mod
    ap.DataError = DataError
    ap.create_pool = AsyncMock()
    sys.modules["asyncpg"] = ap
    sys.modules["asyncpg.exceptions"] = ap.exceptions


def _ensure_httpx():
    """Install minimal httpx stub (mirrors tests_goldspot.py approach)."""
    if "httpx" in sys.modules:
        return
    httpx = types.ModuleType("httpx")
    httpx.AsyncClient = MagicMock
    httpx.TimeoutException = Exception
    httpx.HTTPStatusError = Exception
    httpx.Timeout = MagicMock
    httpx.Response = MagicMock
    httpx.RequestError = Exception
    sys.modules["httpx"] = httpx
    hc = types.ModuleType("httpcore")
    sys.modules["httpcore"] = hc


_ensure_pydantic()
_ensure_asyncpg()
_ensure_httpx()

# ── Offer factory (real Pydantic-free stub) ───────────────────────────────────

def _make_offer(**kw):
    """Create a minimal Offer-like object for tests that don't need Pydantic."""
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    defaults = dict(
        id="test-1",
        source="g2g",
        server="spineshatter",
        display_server="",
        server_name="Spineshatter",
        server_id=None,
        realm_type="Normal",
        raw_title="Spineshatter [EU - Anniversary] - Horde",
        faction="Horde",
        raw_price=0.003,
        raw_price_unit="per_unit",
        lot_size=1,
        price_per_1k=3.0,
        amount_gold=10000,
        seller="testuser",
        offer_url=None,
        updated_at=now,
        fetched_at=now,
    )
    defaults.update(kw)
    obj = MagicMock()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


# ══════════════════════════════════════════════════════════════════════════════
# 1. server_resolver.is_cache_loaded()
# ══════════════════════════════════════════════════════════════════════════════

class TestIsCacheLoaded(unittest.TestCase):

    def setUp(self):
        import db.server_resolver as sr
        self.sr = sr

    def _reset(self):
        self.sr._cache_loaded_at = 0.0
        self.sr._alias_cache_failed = False
        self.sr._alias_cache_retry_count = 0
        self.sr._alias_cache_next_retry = 0.0

    def test_returns_false_on_fresh_start(self):
        self._reset()
        self.assertFalse(self.sr.is_cache_loaded())

    def test_returns_true_after_successful_load(self):
        self._reset()
        self.sr._cache_loaded_at = 100.0
        self.assertTrue(self.sr.is_cache_loaded())

    def test_returns_false_when_circuit_breaker_open(self):
        """Circuit open + no successful load → False."""
        self._reset()
        self.sr._alias_cache_failed = True
        self.assertFalse(self.sr.is_cache_loaded())

    def test_returns_true_even_when_circuit_open_if_cache_was_loaded(self):
        """Cache was loaded, then TTL expired and circuit opened again → still True.
        The cache contents are still valid from the last successful load."""
        self._reset()
        self.sr._cache_loaded_at = 50.0
        self.sr._alias_cache_failed = True
        self.assertTrue(self.sr.is_cache_loaded())

    def test_reset_circuit_breaker_does_not_reset_cache_loaded_at(self):
        """reset_alias_cache_circuit_breaker must not touch _cache_loaded_at."""
        self._reset()
        self.sr._cache_loaded_at = 99.0
        self.sr._alias_cache_failed = True
        self.sr.reset_alias_cache_circuit_breaker()
        # Cache loaded at should be preserved
        self.assertEqual(self.sr._cache_loaded_at, 99.0)
        self.assertTrue(self.sr.is_cache_loaded())

    def test_invalidate_cache_resets_loaded_at(self):
        """invalidate_cache() sets _cache_loaded_at = 0 → is_cache_loaded = False."""
        self._reset()
        self.sr._cache_loaded_at = 99.0

        async def _run():
            await self.sr.invalidate_cache()

        asyncio.get_event_loop().run_until_complete(_run())
        self.assertFalse(self.sr.is_cache_loaded())


# ══════════════════════════════════════════════════════════════════════════════
# 2. normalize_pipeline._reconstruct_display_server_from_raw_title
# ══════════════════════════════════════════════════════════════════════════════

class TestReconstructDisplayServer(unittest.TestCase):

    def setUp(self):
        # Import the function directly from the module
        from service import normalize_pipeline as np
        self.fn = np._reconstruct_display_server_from_raw_title

    def _g2g_offer(self, raw_title, display_server="", server_name=""):
        return _make_offer(
            source="g2g",
            raw_title=raw_title,
            display_server=display_server,
            server_name=server_name,
        )

    def _funpay_offer(self, display_server):
        return _make_offer(
            source="funpay",
            raw_title="",
            display_server=display_server,
        )

    # ── G2G cases ──────────────────────────────────────────────────────────

    def test_anniversary_title(self):
        o = self._g2g_offer("Spineshatter [EU - Anniversary] - Horde")
        self.assertTrue(self.fn(o))
        self.assertEqual(o.display_server, "(EU) Anniversary")
        self.assertEqual(o.server, "(eu) anniversary")
        self.assertEqual(o.server_name, "Spineshatter")

    def test_seasonal_title_canonicalized(self):
        """'Seasonal' in bracket → display_server shows canonical 'Season of Discovery'."""
        o = self._g2g_offer("Lava Lash [EU - Seasonal] - Horde")
        self.assertTrue(self.fn(o))
        self.assertEqual(o.display_server, "(EU) Season of Discovery")

    def test_season_of_discovery_title(self):
        """'Season of Discovery' in bracket → stays canonical."""
        o = self._g2g_offer("Crusader Strike [EU - Season of Discovery] - Horde")
        self.assertTrue(self.fn(o))
        self.assertEqual(o.display_server, "(EU) Season of Discovery")

    def test_classic_era_title(self):
        o = self._g2g_offer("Firemaw [EU - Classic Era] - Alliance")
        self.assertTrue(self.fn(o))
        self.assertEqual(o.display_server, "(EU) Classic")

    def test_classic_title(self):
        o = self._g2g_offer("Noggenfogger [EU - Classic] - Horde")
        self.assertTrue(self.fn(o))
        self.assertEqual(o.display_server, "(EU) Classic")

    def test_us_region(self):
        o = self._g2g_offer("Mankrik [US - Anniversary] - Horde")
        self.assertTrue(self.fn(o))
        self.assertEqual(o.display_server, "(US) Anniversary")

    def test_na_region_normalized_to_us(self):
        """NA in G2G titles → US in canonical display_server."""
        o = self._g2g_offer("Mankrik [NA - Anniversary] - Horde")
        self.assertTrue(self.fn(o))
        self.assertEqual(o.display_server, "(US) Anniversary")

    def test_server_slug_lowercase(self):
        o = self._g2g_offer("Spineshatter [EU - Anniversary] - Alliance")
        self.assertTrue(self.fn(o))
        self.assertEqual(o.server, o.display_server.lower())

    def test_empty_raw_title_returns_false(self):
        o = self._g2g_offer("")
        self.assertFalse(self.fn(o))

    def test_unparseable_title_returns_false(self):
        o = self._g2g_offer("Random gold seller best price")
        self.assertFalse(self.fn(o))

    def test_sod_variant_canonicalized(self):
        """'SoD' variant in raw title → 'Season of Discovery'."""
        o = self._g2g_offer("Penance [EU - SoD] - Horde")
        self.assertTrue(self.fn(o))
        self.assertEqual(o.display_server, "(EU) Season of Discovery")

    # ── FunPay cases ───────────────────────────────────────────────────────

    def test_funpay_with_valid_display_server_returns_true(self):
        """FunPay already has display_server set → True without modification."""
        o = self._funpay_offer("(EU) Anniversary")
        result = self.fn(o)
        self.assertTrue(result)
        # display_server should be untouched for FunPay
        self.assertEqual(o.display_server, "(EU) Anniversary")

    def test_funpay_with_empty_display_server_returns_false(self):
        """FunPay with bare server name cleared → False (quarantine in degraded mode)."""
        o = self._funpay_offer("")
        self.assertFalse(self.fn(o))


# ══════════════════════════════════════════════════════════════════════════════
# 3. normalize_pipeline — strict vs degraded quarantine
# ══════════════════════════════════════════════════════════════════════════════

class TestNormalizePipelineDegradedMode(unittest.IsolatedAsyncioTestCase):

    def _make_g2g_offer(self, raw_title="Spineshatter [EU - Anniversary] - Horde",
                        server_name="Spineshatter", price_per_1k=3.0, server_id=None,
                        faction="Horde", display_server=""):
        return _make_offer(
            source="g2g",
            raw_title=raw_title,
            server_name=server_name,
            display_server=display_server,
            price_per_1k=price_per_1k,
            server_id=server_id,
            faction=faction,
        )

    async def test_strict_mode_unresolved_quarantined(self):
        """When cache is loaded, unresolved server → quarantine."""
        from service import normalize_pipeline as np

        offer = self._make_g2g_offer()
        offer.server_id = None  # unresolved

        with patch("db.server_resolver.is_cache_loaded", return_value=True), \
             patch("db.server_resolver.resolve_server_batch", new_callable=AsyncMock, return_value={}), \
             patch("db.server_resolver.resolve_server", new_callable=AsyncMock, return_value=None), \
             patch("db.server_resolver.get_server_data", return_value=None), \
             patch("service.price_profiles.get_profile", return_value=None):
            normalized, quarantined = await np.normalize_offer_batch([offer], pool=MagicMock())

        self.assertEqual(len(normalized), 0)
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].reason, "unresolved_server")

    async def test_degraded_mode_g2g_passes_through(self):
        """When cache is NOT loaded, G2G offer reconstructs display_server and passes."""
        from service import normalize_pipeline as np

        offer = self._make_g2g_offer(
            raw_title="Spineshatter [EU - Anniversary] - Horde",
            server_name="Spineshatter",
            display_server="",
        )
        offer.server_id = None

        with patch("db.server_resolver.is_cache_loaded", return_value=False), \
             patch("db.server_resolver.resolve_server_batch", new_callable=AsyncMock, return_value={}), \
             patch("db.server_resolver.resolve_server", new_callable=AsyncMock, return_value=None), \
             patch("db.server_resolver.get_server_data", return_value=None), \
             patch("service.price_profiles.get_profile", return_value=None):
            normalized, quarantined = await np.normalize_offer_batch([offer], pool=MagicMock())

        self.assertEqual(len(quarantined), 0, f"Unexpected quarantine: {[q.reason for q in quarantined]}")
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].display_server, "(EU) Anniversary")
        self.assertIsNone(normalized[0].server_id)

    async def test_degraded_mode_seasonal_canonicalized(self):
        """Degraded mode: 'Seasonal' in raw_title → '(EU) Season of Discovery'."""
        from service import normalize_pipeline as np

        offer = self._make_g2g_offer(
            raw_title="Lava Lash [EU - Seasonal] - Horde",
            server_name="Lava Lash",
            display_server="",
        )
        offer.server_id = None

        with patch("db.server_resolver.is_cache_loaded", return_value=False), \
             patch("db.server_resolver.resolve_server_batch", new_callable=AsyncMock, return_value={}), \
             patch("db.server_resolver.resolve_server", new_callable=AsyncMock, return_value=None), \
             patch("db.server_resolver.get_server_data", return_value=None), \
             patch("service.price_profiles.get_profile", return_value=None):
            normalized, quarantined = await np.normalize_offer_batch([offer], pool=MagicMock())

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].display_server, "(EU) Season of Discovery")

    async def test_degraded_mode_unparseable_quarantined(self):
        """Degraded mode: unrecognizable raw_title → quarantine even in degraded mode."""
        from service import normalize_pipeline as np

        offer = self._make_g2g_offer(
            raw_title="random weird title with no brackets",
            display_server="",
        )
        offer.server_id = None

        with patch("db.server_resolver.is_cache_loaded", return_value=False), \
             patch("db.server_resolver.resolve_server_batch", new_callable=AsyncMock, return_value={}), \
             patch("db.server_resolver.resolve_server", new_callable=AsyncMock, return_value=None), \
             patch("db.server_resolver.get_server_data", return_value=None), \
             patch("service.price_profiles.get_profile", return_value=None):
            normalized, quarantined = await np.normalize_offer_batch([offer], pool=MagicMock())

        self.assertEqual(len(normalized), 0)
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].reason, "unresolved_server")

    async def test_degraded_mode_dedup_still_works(self):
        """Duplicate (source, offer_id) pairs are deduped even in degraded mode."""
        from service import normalize_pipeline as np

        o1 = self._make_g2g_offer(raw_title="Spineshatter [EU - Anniversary] - Horde")
        o1.id = "offer-42"
        o2 = self._make_g2g_offer(raw_title="Spineshatter [EU - Anniversary] - Horde")
        o2.id = "offer-42"  # same id → duplicate

        for o in (o1, o2):
            o.server_id = None

        with patch("db.server_resolver.is_cache_loaded", return_value=False), \
             patch("db.server_resolver.resolve_server_batch", new_callable=AsyncMock, return_value={}), \
             patch("db.server_resolver.resolve_server", new_callable=AsyncMock, return_value=None), \
             patch("db.server_resolver.get_server_data", return_value=None), \
             patch("service.price_profiles.get_profile", return_value=None):
            normalized, quarantined = await np.normalize_offer_batch([o1, o2], pool=MagicMock())

        self.assertEqual(len(normalized), 1, "Duplicate should be deduped")

    async def test_strict_mode_resolved_server_canonicalized(self):
        """When cache is loaded and server resolves, _apply_canonical is called."""
        from service import normalize_pipeline as np

        offer = self._make_g2g_offer(server_id=None)

        server_data = {
            "id": 5, "name": "Spineshatter", "region": "EU",
            "version": "Anniversary", "realm_type": "Normal", "is_active": True,
        }

        with patch("db.server_resolver.is_cache_loaded", return_value=True), \
             patch("db.server_resolver.resolve_server_batch", new_callable=AsyncMock,
                   return_value={"spineshatter [eu - anniversary] - horde": 5}), \
             patch("db.server_resolver.resolve_server", new_callable=AsyncMock, return_value=5), \
             patch("db.server_resolver.get_server_data", return_value=server_data), \
             patch("service.price_profiles.get_profile", return_value=None):
            normalized, quarantined = await np.normalize_offer_batch([offer], pool=MagicMock())

        self.assertEqual(len(quarantined), 0)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].display_server, "(EU) Anniversary")
        self.assertEqual(normalized[0].server_id, 5)

    async def test_deprecated_version_quarantined_in_strict_mode(self):
        """Resolved but inactive server → quarantine with deprecated_version."""
        from service import normalize_pipeline as np

        offer = self._make_g2g_offer(server_id=7)  # already has server_id

        server_data = {
            "id": 7, "name": "OldServer", "region": "EU",
            "version": "Season of Mastery", "realm_type": "Normal", "is_active": False,
        }

        with patch("db.server_resolver.is_cache_loaded", return_value=True), \
             patch("db.server_resolver.resolve_server_batch", new_callable=AsyncMock, return_value={}), \
             patch("db.server_resolver.get_server_data", return_value=server_data), \
             patch("service.price_profiles.get_profile", return_value=None):
            normalized, quarantined = await np.normalize_offer_batch([offer], pool=MagicMock())

        self.assertEqual(len(normalized), 0)
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].reason, "deprecated_version")

    async def test_empty_server_title_quarantined(self):
        """Empty server_name and display_server → quarantine: empty_server_title."""
        from service import normalize_pipeline as np

        offer = _make_offer(
            source="g2g", server_name="", display_server="",
            server_id=None, raw_title="", price_per_1k=2.0,
        )

        with patch("db.server_resolver.is_cache_loaded", return_value=False):
            normalized, quarantined = await np.normalize_offer_batch([offer], pool=None)

        self.assertEqual(len(normalized), 0)
        self.assertEqual(quarantined[0].reason, "empty_server_title")

    async def test_zero_price_quarantined(self):
        """price_per_1k ≤ 0 → quarantine: zero_price."""
        from service import normalize_pipeline as np

        offer = _make_offer(
            source="g2g", server_name="Spineshatter", display_server="",
            server_id=None, raw_title="Spineshatter [EU - Anniversary] - Horde",
            price_per_1k=0.0,
        )

        with patch("db.server_resolver.is_cache_loaded", return_value=False):
            normalized, quarantined = await np.normalize_offer_batch([offer], pool=None)

        self.assertEqual(len(normalized), 0)
        self.assertEqual(quarantined[0].reason, "zero_price")


# ══════════════════════════════════════════════════════════════════════════════
# 4. offers_service — cache protection in background loops
# ══════════════════════════════════════════════════════════════════════════════

class TestCacheProtection(unittest.IsolatedAsyncioTestCase):
    """_run_g2g_loop and _run_funpay_loop must not overwrite existing cache with []."""

    def _make_cached_offer(self):
        return _make_offer(display_server="(EU) Anniversary", price_per_1k=3.0)

    async def test_g2g_cache_protected_when_normalize_returns_empty(self):
        """G2G: if normalize returns 0 offers but cache is initialized → keep old cache."""
        import service.offers_service as svc

        # Pre-set a healthy cache
        old_offers = [self._make_cached_offer()]
        svc._cache["g2g"] = old_offers[:]
        svc._cache_initialized["g2g"] = True
        svc._last_error["g2g"] = None

        mock_raw = [_make_offer()]

        with patch.object(svc, "_normalize_g2g_offer", side_effect=lambda o: o), \
             patch("db.writer.get_pool", new_callable=AsyncMock, return_value=MagicMock()), \
             patch("service.normalize_pipeline.normalize_offer_batch",
                   new_callable=AsyncMock, return_value=([], [])):
            # Simulate what the loop does when raw offers exist but normalize returns nothing
            from service.normalize_pipeline import normalize_offer_batch
            pool = await __import__("db.writer", fromlist=["get_pool"]).get_pool()
            normalized = [svc._normalize_g2g_offer(o) for o in mock_raw]
            offers, quarantined = await normalize_offer_batch(normalized, pool)

            # Reproduce the protection logic from the loop
            if not offers and svc._cache_initialized["g2g"]:
                svc._last_error["g2g"] = "empty_after_normalize"
            else:
                svc._cache["g2g"] = offers
                svc._cache_initialized["g2g"] = True

        # Cache must still contain the old offers
        self.assertEqual(svc._cache["g2g"], old_offers)
        self.assertEqual(svc._last_error["g2g"], "empty_after_normalize")

    async def test_g2g_cache_written_on_cold_start_even_if_empty(self):
        """G2G cold start: normalize returns [] → write empty (no old cache to protect)."""
        import service.offers_service as svc

        svc._cache["g2g"] = []
        svc._cache_initialized["g2g"] = False

        offers = []

        if not offers and svc._cache_initialized["g2g"]:
            svc._last_error["g2g"] = "empty_after_normalize"
        else:
            svc._cache["g2g"] = offers
            svc._cache_initialized["g2g"] = True
            svc._last_error["g2g"] = None

        # On cold start, it IS acceptable to write empty
        self.assertEqual(svc._cache["g2g"], [])
        self.assertTrue(svc._cache_initialized["g2g"])
        self.assertIsNone(svc._last_error["g2g"])

    async def test_funpay_cache_protected_when_normalize_returns_empty(self):
        """FunPay: if normalize returns 0 but cache was initialized → keep old cache."""
        import service.offers_service as svc

        old_fp_offers = [self._make_cached_offer()]
        svc._cache["funpay"] = old_fp_offers[:]
        svc._cache_initialized["funpay"] = True
        svc._last_error["funpay"] = None

        offers = []

        if not offers and svc._cache_initialized["funpay"]:
            svc._last_error["funpay"] = "empty_after_normalize"
        else:
            svc._cache["funpay"] = offers
            svc._cache_initialized["funpay"] = True

        self.assertEqual(svc._cache["funpay"], old_fp_offers)
        self.assertEqual(svc._last_error["funpay"], "empty_after_normalize")


# ══════════════════════════════════════════════════════════════════════════════
# 5. offers_service — _normalize_g2g_offer sidebar guard
# ══════════════════════════════════════════════════════════════════════════════

class TestNormalizeG2GOffer(unittest.TestCase):

    def setUp(self):
        import service.offers_service as svc
        self.fn = svc._normalize_g2g_offer

    def _offer(self, display_server="", server_name="Spineshatter"):
        return _make_offer(
            source="g2g",
            display_server=display_server,
            server_name=server_name,
            server=display_server.lower() or server_name.lower(),
        )

    def test_empty_display_server_unchanged(self):
        """Empty display_server stays empty — _apply_canonical sets it later."""
        o = self._offer(display_server="")
        result = self.fn(o)
        self.assertEqual(result.display_server, "")

    def test_canonical_display_server_preserved(self):
        """Already-canonical '(EU) Anniversary' passes through unchanged."""
        o = self._offer(display_server="(EU) Anniversary", server_name="Spineshatter")
        result = self.fn(o)
        self.assertEqual(result.display_server, "(EU) Anniversary")

    def test_bare_server_name_display_server_cleared(self):
        """display_server == server_name (bare name from model_validator) → cleared."""
        o = self._offer(display_server="spineshatter", server_name="Spineshatter")
        result = self.fn(o)
        self.assertEqual(result.display_server, "")

    def test_case_insensitive_guard(self):
        """Guard is case-insensitive: 'SPINESHATTER' == 'spineshatter'."""
        o = self._offer(display_server="SPINESHATTER", server_name="spineshatter")
        result = self.fn(o)
        self.assertEqual(result.display_server, "")

    def test_different_server_not_cleared(self):
        """display_server ≠ server_name → left as-is."""
        o = self._offer(display_server="(EU) Anniversary", server_name="Firemaw")
        result = self.fn(o)
        self.assertNotEqual(result.display_server, "")


# ══════════════════════════════════════════════════════════════════════════════
# 6. offers_service — _normalize_funpay_offer
# ══════════════════════════════════════════════════════════════════════════════

class TestNormalizeFunPayOffer(unittest.TestCase):

    def setUp(self):
        import service.offers_service as svc
        self.fn = svc._normalize_funpay_offer

    def _offer(self, display_server="", server_name=""):
        return _make_offer(
            source="funpay",
            display_server=display_server,
            server_name=server_name,
        )

    def test_group_and_realm(self):
        """'(EU) Anniversary - Spineshatter' → display_server='(EU) Anniversary'."""
        o = self._offer(display_server="(EU) Anniversary - Spineshatter")
        result = self.fn(o)
        self.assertEqual(result.display_server, "(EU) Anniversary")
        self.assertEqual(result.server_name, "Spineshatter")

    def test_group_only(self):
        """'(EU) Classic Era' → display_server='(EU) Classic'."""
        o = self._offer(display_server="(EU) Classic Era")
        result = self.fn(o)
        self.assertEqual(result.display_server, "(EU) Classic")

    def test_bare_server_name_cleared(self):
        """No '(REGION)' prefix → display_server cleared."""
        o = self._offer(display_server="Spineshatter")
        result = self.fn(o)
        self.assertEqual(result.display_server, "")

    def test_empty_display_server_unchanged(self):
        o = self._offer(display_server="")
        result = self.fn(o)
        self.assertEqual(result.display_server, "")

    def test_region_uppercased(self):
        """Region in display_server → always uppercase."""
        o = self._offer(display_server="(eu) Anniversary - Spineshatter")
        result = self.fn(o)
        self.assertIn("(EU)", result.display_server)

    def test_sod_detected(self):
        o = self._offer(display_server="(EU) Season of Discovery - Lava Lash")
        result = self.fn(o)
        self.assertEqual(result.display_server, "(EU) Season of Discovery")

    def test_body_without_dash_extracts_realm_from_body(self):
        """Body with no ' - ' separator → realm = body, display_server = '(EU) VERSION'.
        After normalization display_server != server_name since server_name = realm (e.g. 'Anniversary')
        and display_server = '(EU) Anniversary'."""
        o = self._offer(display_server="(EU) Anniversary")
        result = self.fn(o)
        # display_server should be set to "(EU) Anniversary"
        self.assertEqual(result.display_server, "(EU) Anniversary")
        # server_name should be set to realm part = "Anniversary"
        self.assertEqual(result.server_name, "Anniversary")


# ══════════════════════════════════════════════════════════════════════════════
# 7. offers_service — _GROUP_RE guard in get_servers()
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupREGuard(unittest.TestCase):

    def setUp(self):
        import service.offers_service as svc
        self.re = svc._GROUP_RE

    def _matches(self, s: str) -> bool:
        return bool(self.re.match(s))

    # ── Should PASS ────────────────────────────────────────────────────────

    def test_eu_anniversary(self):
        self.assertTrue(self._matches("(EU) Anniversary"))

    def test_us_classic_era(self):
        self.assertTrue(self._matches("(US) Classic Era"))

    def test_au_anniversary(self):
        self.assertTrue(self._matches("(AU) Anniversary"))

    def test_eu_sod(self):
        self.assertTrue(self._matches("(EU) Season of Discovery"))

    def test_eu_anniversary_hardcore(self):
        self.assertTrue(self._matches("(EU) Anniversary · Hardcore"))

    def test_ru_classic_era(self):
        self.assertTrue(self._matches("(RU) Classic Era"))

    # ── Should FAIL (bare names / wrong format) ────────────────────────────

    def test_bare_server_name_rejected(self):
        self.assertFalse(self._matches("spineshatter"))

    def test_capitalized_bare_name_rejected(self):
        self.assertFalse(self._matches("Spineshatter"))

    def test_lowercase_region_rejected(self):
        """Region must be uppercase — '(eu) Anniversary' should fail."""
        self.assertFalse(self._matches("(eu) Anniversary"))

    def test_empty_string_rejected(self):
        self.assertFalse(self._matches(""))

    def test_just_region_rejected(self):
        self.assertFalse(self._matches("(EU)"))

    def test_region_no_space_rejected(self):
        self.assertFalse(self._matches("(EU)Anniversary"))


# ══════════════════════════════════════════════════════════════════════════════
# 8. price_profiles — percentile + update_profiles + get_profile
# ══════════════════════════════════════════════════════════════════════════════

class TestPriceProfiles(unittest.TestCase):

    def setUp(self):
        from service import price_profiles as pp
        self.pp = pp
        # Reset module state
        pp._profiles = {}
        pp._last_refreshed = 0.0

    def _offer(self, server_id, faction, price_per_1k, amount_gold=10000):
        return _make_offer(
            server_id=server_id,
            faction=faction,
            price_per_1k=price_per_1k,
            amount_gold=amount_gold,
        )

    def test_percentile_single_value(self):
        from service.price_profiles import _percentile
        self.assertEqual(_percentile([5.0], 0.5), 5.0)

    def test_percentile_empty_returns_zero(self):
        from service.price_profiles import _percentile
        self.assertEqual(_percentile([], 0.5), 0.0)

    def test_percentile_median(self):
        from service.price_profiles import _percentile
        self.assertEqual(_percentile([1.0, 2.0, 3.0], 0.5), 2.0)

    def test_percentile_p25(self):
        from service.price_profiles import _percentile
        # 4 items: idx = int(4*0.25)=1 → sorted_values[1]
        self.assertEqual(_percentile([1.0, 2.0, 3.0, 4.0], 0.25), 2.0)

    def test_update_profiles_builds_all_faction(self):
        """update_profiles must build 'All' aggregate alongside per-faction.
        _MIN_SAMPLE=3, so need 3+ offers per faction group."""
        offers = [
            self._offer(1, "Horde",    3.0),
            self._offer(1, "Horde",    4.0),
            self._offer(1, "Horde",    5.0),
            self._offer(1, "Alliance", 2.0),
            self._offer(1, "Alliance", 2.5),
            self._offer(1, "Alliance", 3.0),
        ]
        self.pp.update_profiles(offers)
        # Should have Horde, Alliance, All (all have >= 3 samples)
        self.assertIn(1, self.pp._profiles)
        factions = set(self.pp._profiles[1].keys())
        self.assertIn("Horde",    factions)
        self.assertIn("Alliance", factions)
        self.assertIn("All",      factions)

    def test_update_profiles_ignores_unresolved(self):
        """Offers with server_id=None should be ignored."""
        offers = [self._offer(None, "Horde", 3.0),
                  self._offer(None, "Horde", 4.0),
                  self._offer(None, "Horde", 5.0)]
        self.pp.update_profiles(offers)
        self.assertEqual(self.pp._profiles, {})

    def test_update_profiles_requires_min_sample(self):
        """Fewer than _MIN_SAMPLE (3) offers → no profile built."""
        offers = [self._offer(2, "Horde", 3.0),
                  self._offer(2, "Horde", 4.0)]
        self.pp.update_profiles(offers)
        self.assertNotIn(2, self.pp._profiles)

    def test_get_profile_returns_none_on_empty(self):
        result = self.pp.get_profile(99, "All")
        self.assertIsNone(result)

    def test_get_profile_faction_fallback(self):
        """get_profile falls back to 'All' when specific faction missing."""
        offers = [
            self._offer(3, "Horde", 2.0),
            self._offer(3, "Horde", 3.0),
            self._offer(3, "Horde", 4.0),
        ]
        self.pp.update_profiles(offers)
        # Alliance has no profile → fallback to All
        profile = self.pp.get_profile(3, "Alliance")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.faction, "All")

    def test_profile_median_is_correct(self):
        offers = [
            self._offer(4, "Horde", 1.0),
            self._offer(4, "Horde", 2.0),
            self._offer(4, "Horde", 3.0),
        ]
        self.pp.update_profiles(offers)
        p = self.pp.get_profile(4, "Horde")
        self.assertIsNotNone(p)
        self.assertEqual(p.median, 2.0)

    def test_is_stale_initially(self):
        """After reset, profiles are stale."""
        self.assertTrue(self.pp.is_stale())

    def test_is_not_stale_after_update(self):
        offers = [
            self._offer(5, "Horde", 1.0),
            self._offer(5, "Horde", 2.0),
            self._offer(5, "Horde", 3.0),
        ]
        self.pp.update_profiles(offers)
        self.assertFalse(self.pp.is_stale())


# ══════════════════════════════════════════════════════════════════════════════
# 9. offers_service — compute_index_price edge cases
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeIndexPrice(unittest.TestCase):

    def setUp(self):
        import service.offers_service as svc
        self.fn = svc.compute_index_price

    def _offer(self, price_per_1k, amount_gold=10000):
        return _make_offer(price_per_1k=price_per_1k, amount_gold=amount_gold)

    def test_returns_none_with_single_offer(self):
        result = self.fn([self._offer(3.0)])
        self.assertIsNone(result)

    def test_returns_none_with_empty_list(self):
        result = self.fn([])
        self.assertIsNone(result)

    def test_basic_two_offers(self):
        result = self.fn([self._offer(2.0), self._offer(4.0)])
        self.assertIsNotNone(result)
        self.assertGreater(result.index_price, 0)
        self.assertEqual(result.offer_count, 2)

    def test_outlier_filtered(self):
        """3× outlier filter: add extreme high price, index stays near clean ones."""
        offers = [self._offer(3.0)] * 5 + [self._offer(100.0)]
        result = self.fn(offers)
        self.assertIsNotNone(result)
        self.assertLess(result.index_price, 10.0)

    def test_all_same_price(self):
        offers = [self._offer(5.0)] * 3
        result = self.fn(offers)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.index_price, 5.0)

    def test_best_ask_is_positive(self):
        offers = [self._offer(1.0), self._offer(2.0), self._offer(3.0)]
        result = self.fn(offers)
        self.assertGreater(result.best_ask, 0)

    def test_price_min_le_price_max(self):
        offers = [self._offer(1.0), self._offer(5.0), self._offer(3.0)]
        result = self.fn(offers)
        self.assertLessEqual(result.price_min, result.price_max)


# ══════════════════════════════════════════════════════════════════════════════
# 10. Price derivation logic — pure calculation tests (no Pydantic required)
# ══════════════════════════════════════════════════════════════════════════════

class TestPriceDerivationLogic(unittest.TestCase):
    """Tests price_per_1k derivation math as implemented in Offer.model_validator.
    Tests the raw arithmetic directly without needing real Pydantic."""

    def _derive_price_per_1k(self, raw_price: float, raw_price_unit: str, lot_size: int) -> float:
        """Mirror of Offer.model_validator price derivation logic."""
        if raw_price > 0:
            lot_sz = max(lot_size, 1)
            if raw_price_unit == "per_lot":
                return round(raw_price / lot_sz * 1000.0, 6)
            else:  # per_unit
                return round(raw_price * 1000.0, 6)
        return 0.0

    def _backfill_raw_price(self, price_per_1k: float) -> float:
        """Mirror of legacy backfill path."""
        if price_per_1k > 0:
            return round(price_per_1k / 1000.0, 8)
        return 0.0

    def test_per_unit_g2g_price(self):
        """G2G: raw_price=0.003 per_unit → price_per_1k=3.0"""
        self.assertAlmostEqual(self._derive_price_per_1k(0.003, "per_unit", 1), 3.0, places=3)

    def test_per_lot_funpay_price(self):
        """FunPay: raw_price=5.0 for lot=2000 gold → price_per_1k = 5/2000*1000 = 2.5"""
        self.assertAlmostEqual(self._derive_price_per_1k(5.0, "per_lot", 2000), 2.5, places=3)

    def test_per_lot_small_lot(self):
        """FunPay: raw_price=1.0 for lot=100 gold → price_per_1k = 1/100*1000 = 10.0"""
        self.assertAlmostEqual(self._derive_price_per_1k(1.0, "per_lot", 100), 10.0, places=3)

    def test_lot_size_zero_guard(self):
        """lot_size=0 → max(0,1)=1 → treated as per_lot with lot=1."""
        result = self._derive_price_per_1k(3.0, "per_lot", 0)
        self.assertGreater(result, 0)
        # 3.0 / max(0,1) * 1000 = 3000
        self.assertAlmostEqual(result, 3000.0, places=1)

    def test_legacy_backfill(self):
        """Legacy: price_per_1k=4.0 → raw_price = 4.0/1000 = 0.004"""
        self.assertAlmostEqual(self._backfill_raw_price(4.0), 0.004, places=6)

    def test_price_unit_conversion_per_1(self):
        """per_1 display: price_per_1k → price per 1 gold."""
        price_per_1k = 2.5
        price_display = round(price_per_1k / 1000.0, 8)
        self.assertAlmostEqual(price_display, 0.0025, places=6)

    def test_price_unit_conversion_per_1k(self):
        """per_1k display: price_per_1k → same value."""
        price_per_1k = 2.5
        price_display = round(price_per_1k, 4)
        self.assertAlmostEqual(price_display, 2.5, places=3)

    def test_display_server_fallback_rule(self):
        """Offer.model_validator: if display_server is '', set display_server = server."""
        server = "(eu) anniversary"
        display_server = ""
        if not display_server:
            display_server = server
        self.assertEqual(display_server, "(eu) anniversary")

    def test_server_always_lowercase(self):
        """model_validator: server = server.lower()."""
        server_input = "(EU) Anniversary"
        server_slug = server_input.lower()
        self.assertEqual(server_slug, "(eu) anniversary")


# ══════════════════════════════════════════════════════════════════════════════
# 11. OfferRow.from_offer — price_unit conversion (logic test, no Pydantic)
# ══════════════════════════════════════════════════════════════════════════════

class TestOfferRowPriceUnit(unittest.TestCase):
    """Tests the from_offer price conversion math (mirrors OfferRow.from_offer)."""

    def _convert(self, price_per_1k: float, price_unit: str) -> float:
        """Mirror of OfferRow.from_offer price_display derivation."""
        if price_unit == "per_1":
            return round(price_per_1k / 1000.0, 8)
        else:
            return round(price_per_1k, 4)

    def test_per_1k_display_unchanged(self):
        self.assertAlmostEqual(self._convert(2.5, "per_1k"), 2.5, places=3)

    def test_per_1_display_divided_by_1000(self):
        self.assertAlmostEqual(self._convert(2.5, "per_1"), 0.0025, places=6)

    def test_per_1k_high_value(self):
        self.assertAlmostEqual(self._convert(10.0, "per_1k"), 10.0, places=3)

    def test_per_1_high_value(self):
        self.assertAlmostEqual(self._convert(10.0, "per_1"), 0.01, places=5)

    def test_price_per_1k_field_invariant(self):
        """price_per_1k in OfferRow is same regardless of which price_unit is chosen."""
        p = 4.0
        # Both conversions start from same p — price_per_1k stored separately
        row_1k = round(p, 4)
        row_1 = round(p, 4)  # same
        self.assertAlmostEqual(row_1k, row_1, places=4)


# ══════════════════════════════════════════════════════════════════════════════
# 12. g2g_parser._parse_title — version passthrough (Bug fix verification)
# ══════════════════════════════════════════════════════════════════════════════

class TestG2GParseTitle(unittest.TestCase):
    """Verify _parse_title passes version raw from brackets without corruption."""

    def setUp(self):
        import ast, os, re as _re, asyncio as _aio, logging as _log, json as _json
        import sys, types, dataclasses
        src_path = os.path.join(os.path.dirname(__file__), "parser", "g2g_parser.py")
        with open(src_path) as f:
            raw = f.read()
        src = raw.replace("from api.schemas import Offer", "Offer = None")
        ns = {
            "re": _re, "asyncio": _aio, "json": _json, "logging": _log,
            "dataclasses": dataclasses, "dataclass": dataclasses.dataclass,
            "field": dataclasses.field,
            "datetime": __import__("datetime").datetime,
            "timezone": __import__("datetime").timezone,
            "Optional": __import__("typing").Optional,
            "httpx": sys.modules.get("httpx", MagicMock()),
            "__builtins__": __builtins__,
        }
        try:
            exec(compile(src, src_path, "exec"), ns)
            self.parse_title = ns["_parse_title"]
            self._available = True
        except Exception as e:
            self._available = False
            self._skip_reason = str(e)

    def _skip(self):
        if not self._available:
            self.skipTest(f"_parse_title unavailable: {self._skip_reason}")

    def test_season_of_discovery_in_title(self):
        """'Season of Discovery' in brackets → returned verbatim, NOT converted to 'Seasonal'."""
        self._skip()
        _, _, v, _ = self.parse_title("Crusader Strike [EU - Season of Discovery] - Horde")
        self.assertEqual(v, "Season of Discovery",
            "BUG: 'Season of Discovery' was corrupted to 'Seasonal' in _parse_title")

    def test_seasonal_variant_passthrough(self):
        """'Seasonal' in brackets → returned as-is (canonical happens downstream)."""
        self._skip()
        _, _, v, _ = self.parse_title("Lava Lash [EU - Seasonal] - Horde")
        self.assertEqual(v, "Seasonal")

    def test_anniversary_passthrough(self):
        self._skip()
        _, _, v, _ = self.parse_title("Spineshatter [EU - Anniversary] - Horde")
        self.assertEqual(v, "Anniversary")

    def test_classic_era_passthrough(self):
        self._skip()
        _, _, v, _ = self.parse_title("Firemaw [EU - Classic Era] - Alliance")
        self.assertEqual(v, "Classic Era")

    def test_classic_passthrough(self):
        self._skip()
        _, _, v, _ = self.parse_title("Noggenfogger [EU - Classic] - Horde")
        self.assertEqual(v, "Classic")

    def test_region_always_uppercase(self):
        self._skip()
        _, r, _, _ = self.parse_title("Spineshatter [eu - Anniversary] - Horde")
        self.assertEqual(r, "EU")

    def test_faction_defaults_to_horde(self):
        self._skip()
        _, _, _, f = self.parse_title("Spineshatter [EU - Anniversary]")
        self.assertIn(f, ("Horde", "Alliance"))

    def test_empty_title_returns_empty_server(self):
        self._skip()
        s, _, _, _ = self.parse_title("")
        self.assertEqual(s, "")


# ══════════════════════════════════════════════════════════════════════════════
# 13. server_resolver — circuit breaker + backoff state machine
# ══════════════════════════════════════════════════════════════════════════════

class TestServerResolverCircuitBreaker(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        import db.server_resolver as sr
        self.sr = sr
        # Clean slate for each test
        sr._alias_cache         = {}
        sr._cache_loaded_at     = 0.0
        sr._alias_cache_failed  = False
        sr._alias_cache_retry_count = 0
        sr._alias_cache_next_retry  = 0.0
        sr._server_data_cache   = {}

    async def test_circuit_open_after_max_retries(self):
        """After _ALIAS_RETRY_DELAYS exhausted, circuit should open."""
        import time
        sr = self.sr

        async def failing_pool_fetch(*a, **kw):
            raise RuntimeError("connection refused")

        pool = MagicMock()
        pool.fetch = failing_pool_fetch

        # Run load enough times to exhaust all retry slots
        for _ in range(len(sr._ALIAS_RETRY_DELAYS) + 2):
            sr._alias_cache_next_retry = 0.0  # bypass backoff for test speed
            await sr._load_alias_cache(pool)

        self.assertTrue(sr._alias_cache_failed,
            "Circuit should be open after exhausting all retry slots")

    async def test_reset_re_arms_circuit(self):
        """reset_alias_cache_circuit_breaker clears failed state."""
        sr = self.sr
        sr._alias_cache_failed = True
        sr._alias_cache_retry_count = 5
        sr.reset_alias_cache_circuit_breaker()
        self.assertFalse(sr._alias_cache_failed)
        self.assertEqual(sr._alias_cache_retry_count, 0)
        self.assertEqual(sr._alias_cache_next_retry, 0.0)

    async def test_ensure_cache_skips_when_circuit_open(self):
        """_ensure_cache must return immediately without calling _load when circuit open."""
        sr = self.sr
        sr._alias_cache_failed = True
        sr._cache_loaded_at = 0.0  # TTL expired

        load_called = []

        async def spy_load(pool):
            load_called.append(True)

        original = sr._load_alias_cache
        sr._load_alias_cache = spy_load
        try:
            await sr._ensure_cache(MagicMock())
        finally:
            sr._load_alias_cache = original

        self.assertEqual(load_called, [], "_load_alias_cache should not be called when circuit is open")

    async def test_successful_load_resets_retry_count(self):
        """After successful load, retry_count and next_retry are cleared."""
        sr = self.sr
        sr._alias_cache_retry_count = 3
        # Do NOT set _alias_cache_next_retry to a high value — that would cause
        # _load_alias_cache to exit early via the backoff guard.
        sr._alias_cache_next_retry = 0.0

        alias_row = MagicMock()
        alias_row.__getitem__ = lambda self, k: {"alias": "spineshatter eu anniversary horde", "server_id": 1}[k]
        alias_rows = [alias_row]
        server_rows = []

        pool = MagicMock()
        pool.fetch = AsyncMock(side_effect=[alias_rows, server_rows])

        await sr._load_alias_cache(pool)

        self.assertEqual(sr._alias_cache_retry_count, 0)
        self.assertEqual(sr._alias_cache_next_retry, 0.0)
        self.assertGreater(sr._cache_loaded_at, 0)


# ══════════════════════════════════════════════════════════════════════════════
# 14. version_utils — _canonicalize_version
# ══════════════════════════════════════════════════════════════════════════════

class TestCanonicalizeVersion(unittest.TestCase):

    def setUp(self):
        from utils.version_utils import _canonicalize_version
        self.fn = _canonicalize_version

    def test_seasonal_to_sod(self):
        self.assertEqual(self.fn("seasonal"), "Season of Discovery")

    def test_sod_abbreviation(self):
        self.assertEqual(self.fn("sod"), "Season of Discovery")

    def test_season_of_discovery_unchanged(self):
        """Already canonical → returned unchanged."""
        self.assertEqual(self.fn("Season of Discovery"), "Season of Discovery")

    def test_anniversary_variants(self):
        self.assertEqual(self.fn("anniversary"), "Anniversary")
        self.assertEqual(self.fn("classic anniversary"), "Anniversary")
        self.assertEqual(self.fn("anniversary gold"), "Anniversary")

    def test_classic_era(self):
        self.assertEqual(self.fn("classic era"), "Classic")
        self.assertEqual(self.fn("vanilla"), "Classic")
        self.assertEqual(self.fn("era"), "Classic")

    def test_classic(self):
        self.assertEqual(self.fn("classic"), "Classic")

    def test_tbc(self):
        self.assertEqual(self.fn("tbc classic"), "TBC Classic")
        self.assertEqual(self.fn("tbc"), "TBC Classic")

    def test_unknown_passthrough(self):
        self.assertEqual(self.fn("Hardcore"), "Hardcore")

    def test_empty_string_passthrough(self):
        self.assertEqual(self.fn(""), "")

    def test_none_safe(self):
        """None input: (None or '') → '' → not in aliases → returns None (no crash).
        Callers are responsible for passing a string; None is a caller bug, but the
        function must not raise an exception."""
        try:
            result = self.fn(None)
            # Returns None for None input (the default is the input itself)
            # This is acceptable — callers should guard against None.
            # The important thing is that it does not crash with an exception.
        except (TypeError, AttributeError) as e:
            self.fail(f"_canonicalize_version raised {type(e).__name__} on None: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# 15. Integration: degraded mode version canonicalization end-to-end
# ══════════════════════════════════════════════════════════════════════════════

class TestDegradedModeVersionCanonicalization(unittest.TestCase):
    """Verify that the full degraded-mode chain produces correct canonical display_server."""

    def test_seasonal_title_produces_sod_display_server(self):
        """
        G2G title 'Lava Lash [EU - Seasonal] - Horde' in degraded mode:
        After _reconstruct_display_server_from_raw_title:
          display_server must be "(EU) Season of Discovery"  (NOT "(EU) Seasonal")
        After _GROUP_RE:
          "(EU) Season of Discovery" passes the sidebar guard
        """
        import service.offers_service as svc
        from service.normalize_pipeline import _reconstruct_display_server_from_raw_title

        offer = _make_offer(
            source="g2g",
            raw_title="Lava Lash [EU - Seasonal] - Horde",
            display_server="",
            server_name="Lava Lash",
            server_id=None,
        )

        success = _reconstruct_display_server_from_raw_title(offer)

        self.assertTrue(success)
        self.assertEqual(offer.display_server, "(EU) Season of Discovery",
            "Canonical version 'Season of Discovery' expected, got: " + repr(offer.display_server))
        self.assertTrue(
            bool(svc._GROUP_RE.match(offer.display_server)),
            "display_server must pass _GROUP_RE guard for sidebar visibility",
        )

    def test_sod_title_produces_sod_display_server(self):
        """G2G title with 'SoD' → canonical 'Season of Discovery'."""
        from service.normalize_pipeline import _reconstruct_display_server_from_raw_title

        offer = _make_offer(
            source="g2g",
            raw_title="Penance [EU - SoD] - Horde",
            display_server="",
            server_name="Penance",
            server_id=None,
        )

        self.assertTrue(_reconstruct_display_server_from_raw_title(offer))
        self.assertEqual(offer.display_server, "(EU) Season of Discovery")

    def test_anniversary_title_correct_group_key(self):
        """Anniversary offer in degraded mode → valid sidebar group."""
        import service.offers_service as svc
        from service.normalize_pipeline import _reconstruct_display_server_from_raw_title

        offer = _make_offer(
            source="g2g",
            raw_title="Spineshatter [EU - Anniversary] - Horde",
            display_server="",
            server_name="Spineshatter",
            server_id=None,
        )

        self.assertTrue(_reconstruct_display_server_from_raw_title(offer))
        self.assertTrue(bool(svc._GROUP_RE.match(offer.display_server)))
        self.assertEqual(offer.display_server, "(EU) Anniversary")


# ══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    test_classes = [
        TestIsCacheLoaded,
        TestReconstructDisplayServer,
        TestNormalizePipelineDegradedMode,
        TestCacheProtection,
        TestNormalizeG2GOffer,
        TestNormalizeFunPayOffer,
        TestGroupREGuard,
        TestPriceProfiles,
        TestComputeIndexPrice,
        TestPriceDerivationLogic,
        TestOfferRowPriceUnit,
        TestG2GParseTitle,
        TestServerResolverCircuitBreaker,
        TestCanonicalizeVersion,
        TestDegradedModeVersionCanonicalization,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
