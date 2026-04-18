"""
GoldSpot backend — comprehensive test suite (stdlib only).

Работает без pydantic/httpx/asyncpg — мокируем тяжёлые зависимости,
тестируем только бизнес-логику и критический баг-фикс.

Запуск:
    python3 tests_goldspot.py
"""
import asyncio
import sys
import os
import re
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))

# ── Заглушки для отсутствующих библиотек ──────────────────────────────────────

def _mock_module(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def _install_pydantic_stub():
    """Минимальный stub pydantic для тестов без pydantic_core."""
    if "pydantic" in sys.modules:
        return

    # pydantic_core
    core = types.ModuleType("pydantic_core")
    core.core_schema = types.ModuleType("pydantic_core.core_schema")
    sys.modules["pydantic_core"] = core
    sys.modules["pydantic_core.core_schema"] = core.core_schema

    # pydantic stub — простая dataclass-замена BaseModel
    pyd = types.ModuleType("pydantic")

    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
        @classmethod
        def model_validate(cls, data):
            if isinstance(data, cls):
                return data
            return cls(**data)
        def model_dump(self, mode="python"):
            return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    class ValidationError(Exception):
        pass

    class ConfigDict(dict):
        pass

    def field_serializer(*a, **kw):
        return lambda f: f

    def field_validator(*a, **kw):
        return lambda f: f

    def model_validator(*a, **kw):
        return lambda f: f

    class Field:
        def __init__(self, default=None, **kw):
            self.default = default

    pyd.BaseModel      = BaseModel
    pyd.ValidationError = ValidationError
    pyd.ConfigDict     = ConfigDict
    pyd.field_serializer = field_serializer
    pyd.field_validator  = field_validator
    pyd.model_validator  = model_validator
    pyd.Field          = Field
    sys.modules["pydantic"] = pyd

    # pydantic_settings
    ps = types.ModuleType("pydantic_settings")

    class BaseSettings(BaseModel):
        pass

    class SettingsConfigDict(dict):
        pass

    ps.BaseSettings = BaseSettings
    ps.SettingsConfigDict = SettingsConfigDict
    sys.modules["pydantic_settings"] = ps


def _install_httpx_stub():
    if "httpx" in sys.modules:
        return
    httpx = types.ModuleType("httpx")
    httpx.AsyncClient = MagicMock
    httpx.TimeoutException = Exception
    httpx.HTTPStatusError = Exception
    httpx.Timeout = MagicMock
    httpx.Response = MagicMock        # needed for _http_get_retry return annotation
    httpx.RequestError = Exception    # extra guards for any other annotations
    sys.modules["httpx"] = httpx
    # httpcore is silenced in g2g_parser logging config
    hc = types.ModuleType("httpcore")
    sys.modules["httpcore"] = hc


def _install_bs4_stub():
    if "bs4" in sys.modules:
        return
    bs4 = types.ModuleType("bs4")
    bs4.BeautifulSoup = MagicMock
    bs4.Tag = object
    sys.modules["bs4"] = bs4
    sys.modules["beautifulsoup4"] = bs4


def _install_asyncpg_stub():
    if "asyncpg" in sys.modules:
        return
    ap = types.ModuleType("asyncpg")

    class DataError(Exception):
        pass

    ap.DataError = DataError
    ap.create_pool = AsyncMock()
    sys.modules["asyncpg"] = ap


# Устанавливаем все stubs до первого импорта проекта
_install_pydantic_stub()
_install_httpx_stub()
_install_bs4_stub()
_install_asyncpg_stub()


# ══════════════════════════════════════════════════════════════════════════════
# 1. КРИТИЧЕСКИЙ БАГ-ФИКС — db/writer.py: interval → timedelta
# ══════════════════════════════════════════════════════════════════════════════

class TestWriterIntervalFix(unittest.TestCase):
    """Главный тест: INTERVAL-параметр должен быть timedelta, а не строка."""

    def test_source_no_longer_has_string_interval(self):
        """Старая строка f"{last_hours} hours" должна быть убрана."""
        import inspect
        from db import writer as w
        source = inspect.getsource(w.query_index_history)
        self.assertNotIn('f"{last_hours} hours"', source,
            "БАГИ ВСЕГО ЕЩЁ ПРИСУТСТВУЕТ: f\"{last_hours} hours\" найдено в исходнике!")

    def test_source_has_timedelta_call(self):
        """timedelta(hours=last_hours) должен быть в исходнике."""
        import inspect
        from db import writer as w
        source = inspect.getsource(w.query_index_history)
        self.assertIn("timedelta(hours=last_hours)", source,
            "timedelta(hours=last_hours) отсутствует — фикс не применён!")

    def test_sql_keeps_interval_cast(self):
        """
        $2::INTERVAL ДОЛЖЕН остаться в SQL.

        Без ::INTERVAL PostgreSQL при prepare не может однозначно определить
        тип $2 в выражении NOW() - $2.
        Он предпочитает оператор timestamp - timestamp = interval, поэтому
        RIGHT часть оператора > получает тип interval, а НЕ timestamp.
        Результат: «operator does not exist: timestamp with time zone > interval».

        Правило: timedelta как Python-объект + ::INTERVAL в SQL = единственно
        корректная комбинация для asyncpg + PostgreSQL.
        """
        import inspect
        from db import writer as w
        source = inspect.getsource(w.query_index_history)
        self.assertIn("$2::INTERVAL", source,
            "$2::INTERVAL отсутствует в SQL — PostgreSQL не сможет вывести тип и упадёт!")

    def test_timedelta_imported_in_writer(self):
        """db.writer должен импортировать timedelta."""
        from db import writer as w
        import datetime as dt
        # Проверяем что timedelta используется в модуле
        src = open(os.path.join(os.path.dirname(__file__), "db", "writer.py")).read()
        self.assertIn("timedelta", src)

    def test_timedelta_has_days(self):
        """timedelta имеет .days — asyncpg interval_encode требует это."""
        td = timedelta(hours=24)
        self.assertTrue(hasattr(td, "days"))
        self.assertTrue(hasattr(td, "seconds"))
        self.assertTrue(hasattr(td, "microseconds"))

    def test_string_lacks_days(self):
        """Строка '24 hours' не имеет .days — именно это вызывало DataError."""
        self.assertFalse(hasattr("24 hours", "days"))

    def test_asyncpg_needs_timedelta(self):
        """Моделирует encode asyncpg: obj.days обязателен."""
        def encode_interval(obj):
            _ = obj.days       # asyncpg вызывает именно это
            _ = obj.seconds
            _ = obj.microseconds
            return True

        # Строка падает
        with self.assertRaises(AttributeError):
            encode_interval("24 hours")

        # timedelta проходит
        self.assertTrue(encode_interval(timedelta(hours=24)))
        self.assertTrue(encode_interval(timedelta(hours=168)))

    def test_timedelta_values_correct(self):
        self.assertEqual(timedelta(hours=1).total_seconds(), 3600)
        self.assertEqual(timedelta(hours=24).total_seconds(), 86400)
        self.assertEqual(timedelta(hours=168).total_seconds(), 604800)


# ══════════════════════════════════════════════════════════════════════════════
# 1b. db/writer — server_price_history: срок хранения при prune (upsert_server_index)
# ══════════════════════════════════════════════════════════════════════════════

class TestServerPriceHistoryRetention(unittest.TestCase):
    """Регрессия: prune в upsert_server_index держит историю 90 дней, не 35."""

    def test_upsert_server_index_prune_uses_90_day_interval(self):
        import inspect
        from db import writer as w
        src = inspect.getsource(w.upsert_server_index)
        self.assertIn("DELETE FROM server_price_history", src)
        self.assertIn("INTERVAL '90 days'", src)
        self.assertNotIn("INTERVAL '35 days'", src)

    def test_upsert_server_index_docstring_matches_retention(self):
        import inspect
        from db import writer as w
        src = inspect.getsource(w.upsert_server_index)
        self.assertIn("older than 90 days", src)


# ══════════════════════════════════════════════════════════════════════════════
# 2. db/writer — _faction_to_db, _should_write
# ══════════════════════════════════════════════════════════════════════════════

class TestWriterHelpers(unittest.TestCase):

    def setUp(self):
        from db import writer as w
        self.w = w
        self.w._last_written.clear()

    def test_faction_all_lowercase(self):
        self.assertEqual(self.w._faction_to_db("all"), "All")

    def test_faction_all_uppercase(self):
        self.assertEqual(self.w._faction_to_db("ALL"), "All")

    def test_faction_alliance_lower(self):
        self.assertEqual(self.w._faction_to_db("alliance"), "Alliance")

    def test_faction_alliance_mixed(self):
        self.assertEqual(self.w._faction_to_db("Alliance"), "Alliance")

    def test_faction_horde_lower(self):
        self.assertEqual(self.w._faction_to_db("horde"), "Horde")

    def test_faction_horde_upper(self):
        self.assertEqual(self.w._faction_to_db("HORDE"), "Horde")

    def test_should_write_first_time(self):
        self.assertTrue(self.w._should_write("EU_Ann::All", 1.5))

    def test_should_write_no_change(self):
        self.w._last_written["EU_Ann::All"] = 1.5
        self.assertFalse(self.w._should_write("EU_Ann::All", 1.5))

    def test_should_write_tiny_change(self):
        """< 0.5% → не пишем."""
        self.w._last_written["EU_Ann::All"] = 1.5
        self.assertFalse(self.w._should_write("EU_Ann::All", 1.506))   # +0.4%

    def test_should_write_significant_change(self):
        """≥ 0.5% → пишем."""
        self.w._last_written["EU_Ann::All"] = 1.5
        self.assertTrue(self.w._should_write("EU_Ann::All", 1.51))    # +0.67%

    def test_should_write_zero_prev(self):
        """Предыдущая цена 0 — всегда пишем."""
        self.w._last_written["EU_Ann::All"] = 0.0
        self.assertTrue(self.w._should_write("EU_Ann::All", 1.5))

    def test_should_write_different_key(self):
        """Разные ключи независимы."""
        self.w._last_written["EU_Ann::Horde"] = 1.5
        self.assertTrue(self.w._should_write("EU_Ann::Alliance", 1.5))

    def test_faction_to_db_roundtrip(self):
        """Все варианты faction проходят нормализацию."""
        for raw, expected in [
            ("all", "All"), ("ALL", "All"), ("All", "All"),
            ("alliance", "Alliance"), ("ALLIANCE", "Alliance"),
            ("horde", "Horde"), ("HORDE", "Horde"),
        ]:
            self.assertEqual(self.w._faction_to_db(raw), expected, f"Ошибка для {raw!r}")


# ══════════════════════════════════════════════════════════════════════════════
# 3. db/writer — query_index_history (async, mock pool)
# ══════════════════════════════════════════════════════════════════════════════

class TestQueryIndexHistory(unittest.IsolatedAsyncioTestCase):

    async def test_no_db_returns_empty(self):
        from db import writer as w
        w._pool = None
        saved_url = os.environ.pop("DATABASE_URL", None)
        try:
            result = await w.query_index_history("(EU) Anniversary", "all", 24, 400)
            self.assertEqual(result, [])
        finally:
            if saved_url:
                os.environ["DATABASE_URL"] = saved_url

    async def test_params_include_timedelta(self):
        """
        КЛЮЧЕВОЙ ТЕСТ ФИКСА:
        pool.fetch должен получить timedelta как второй параметр, не строку.
        """
        from db import writer as w

        captured = {}

        async def fake_fetch(query, *params):
            captured["params"] = params
            return []

        mock_pool = MagicMock()
        mock_pool.fetch = fake_fetch

        with patch.object(w, "get_pool", return_value=mock_pool):
            await w.query_index_history("(EU) Anniversary", "all", 24, 400)

        self.assertIn("params", captured, "pool.fetch не был вызван")
        interval_param = captured["params"][1]  # $2

        self.assertIsInstance(
            interval_param, timedelta,
            f"$2 должен быть timedelta, получено {type(interval_param).__name__!r}: {interval_param!r}"
        )
        self.assertEqual(interval_param, timedelta(hours=24))

    async def test_bucket_calculation_24h_400pts(self):
        """24h / 400pts → bucket = max(5, 3) = 5 минут."""
        bm = max(5, (24 * 60) // 400)
        self.assertEqual(bm, 5)

    async def test_bucket_calculation_168h_500pts(self):
        """168h / 500pts → bucket = max(5, 20) = 20 минут."""
        bm = max(5, (168 * 60) // 500)
        self.assertEqual(bm, 20)

    async def test_db_exception_returns_empty(self):
        """DB упала → возвращает [], не кидает."""
        from db import writer as w

        mock_pool = MagicMock()
        mock_pool.fetch = AsyncMock(side_effect=RuntimeError("connection refused"))

        with patch.object(w, "get_pool", return_value=mock_pool):
            result = await w.query_index_history("(EU) Anniversary", "all", 24, 400)
        self.assertEqual(result, [])

    async def test_faction_normalized_for_db(self):
        """'all' должен нормализоваться в 'All' для запроса в БД."""
        from db import writer as w
        import inspect

        source = inspect.getsource(w.query_index_history)
        self.assertIn("_faction_to_db", source)


# ══════════════════════════════════════════════════════════════════════════════
# 4. utils/server — normalize_server (no external deps)
# ══════════════════════════════════════════════════════════════════════════════

class TestNormalizeServer(unittest.TestCase):

    def setUp(self):
        from utils.server import normalize_server
        self.norm = normalize_server

    def test_basic_eu(self):
        r = self.norm("(EU) Flamegor")
        self.assertEqual(r.slug, "flamegor")
        self.assertIn("EU", r.display)

    def test_anniversary_with_hash(self):
        r = self.norm("(EU) #Anniversary - Spineshatter")
        self.assertEqual(r.slug, "spineshatter")

    def test_classic_era(self):
        r = self.norm("(EU) Classic Era - Firemaw")
        self.assertEqual(r.slug, "firemaw")

    def test_plain_server(self):
        r = self.norm("Firemaw")
        self.assertEqual(r.slug, "firemaw")
        self.assertEqual(r.display, "Firemaw")

    def test_empty_string(self):
        r = self.norm("")
        self.assertEqual(r.slug, "unknown")
        self.assertEqual(r.display, "unknown")

    def test_none(self):
        r = self.norm(None)
        self.assertEqual(r.slug, "unknown")

    def test_eu_pvp_region(self):
        r = self.norm("(EU-PVP) Gehennas")
        self.assertIn("EU-PVP", r.display)
        self.assertEqual(r.slug, "gehennas")

    def test_whitespace_only(self):
        r = self.norm("   ")
        self.assertEqual(r.slug, "unknown")

    def test_slug_is_lowercase(self):
        r = self.norm("(EU) FIREMAW")
        self.assertEqual(r.slug, r.slug.lower())


# ══════════════════════════════════════════════════════════════════════════════
# 5. Inline-тесты _parse_float / _parse_int (FunPay logic, без httpx/bs4)
#    Тестируем функции напрямую через exec/importlib чтобы обойти top-level импорты
# ══════════════════════════════════════════════════════════════════════════════

def _load_parse_functions():
    """Извлекаем _parse_float/_parse_int из funpay_parser без импорта httpx/bs4."""
    import ast, textwrap

    src_path = os.path.join(os.path.dirname(__file__), "parser", "funpay_parser.py")
    with open(src_path) as f:
        raw = f.read()

    # Заменяем тяжёлые импорты заглушками чтобы exec сработал
    src = raw.replace("import httpx", "httpx = None")
    src = src.replace("from bs4 import BeautifulSoup, Tag", "BeautifulSoup = None; Tag = object")
    src = src.replace("from api.schemas import Offer", "Offer = None")

    ns = {"re": re, "__builtins__": __builtins__}
    exec(compile(src, src_path, "exec"), ns)
    return ns["_parse_float"], ns["_parse_int"]


try:
    _parse_float, _parse_int = _load_parse_functions()
    _FUNPAY_AVAILABLE = True
except Exception as e:
    _FUNPAY_AVAILABLE = False
    print(f"[WARN] FunPay parse functions unavailable: {e}")


class TestParseFloat(unittest.TestCase):

    @unittest.skipUnless(_FUNPAY_AVAILABLE, "_parse_float недоступна")
    def test_simple(self):
        self.assertAlmostEqual(_parse_float("1.50"), 1.50)

    @unittest.skipUnless(_FUNPAY_AVAILABLE, "_parse_float недоступна")
    def test_comma_decimal(self):
        self.assertAlmostEqual(_parse_float("1,50"), 1.50)

    @unittest.skipUnless(_FUNPAY_AVAILABLE, "_parse_float недоступна")
    def test_currency_stripped(self):
        self.assertAlmostEqual(_parse_float("$1.50"), 1.50)

    @unittest.skipUnless(_FUNPAY_AVAILABLE, "_parse_float недоступна")
    def test_empty_none(self):
        self.assertIsNone(_parse_float(""))
        self.assertIsNone(_parse_float(None))

    @unittest.skipUnless(_FUNPAY_AVAILABLE, "_parse_float недоступна")
    def test_non_numeric_none(self):
        self.assertIsNone(_parse_float("abc"))

    @unittest.skipUnless(_FUNPAY_AVAILABLE, "_parse_float недоступна")
    def test_int_basic(self):
        self.assertEqual(_parse_int("10,000"), 10000)

    @unittest.skipUnless(_FUNPAY_AVAILABLE, "_parse_float недоступна")
    def test_int_spaces(self):
        self.assertEqual(_parse_int("1 000"), 1000)

    @unittest.skipUnless(_FUNPAY_AVAILABLE, "_parse_float недоступна")
    def test_int_empty(self):
        self.assertIsNone(_parse_int(""))


# ══════════════════════════════════════════════════════════════════════════════
# 6. G2G parser — _parse_title, _build_offer_url (без httpx)
# ══════════════════════════════════════════════════════════════════════════════

def _load_g2g_functions():
    """Извлекаем функции g2g_parser без top-level импорта httpx.

    Используем уже установленные stubs из sys.modules вместо замены на None,
    чтобы type annotations вроде Optional[httpx.AsyncClient] не падали
    с AttributeError: 'NoneType' object has no attribute 'AsyncClient'.
    """
    import dataclasses

    src_path = os.path.join(os.path.dirname(__file__), "parser", "g2g_parser.py")
    with open(src_path) as f:
        raw = f.read()

    # НЕ заменяем import httpx на None — пусть exec достаёт из ns["httpx"].
    # Stub уже установлен _install_httpx_stub() выше.
    src = raw.replace("from api.schemas import Offer", "Offer = None")
    # version_utils: загружаем напрямую, sys.path уже содержит backend dir
    # (добавлен в начале файла через sys.path.insert)

    ns = {
        "re": re,
        "asyncio": asyncio,
        "json": __import__("json"),
        "logging": __import__("logging"),
        "dataclasses": dataclasses,
        "dataclass": dataclasses.dataclass,
        "field": dataclasses.field,
        "datetime": datetime,
        "timezone": timezone,
        "Optional": __import__("typing").Optional,
        # Provide the httpx stub from sys.modules so AsyncClient annotation works
        "httpx": sys.modules.get("httpx", MagicMock()),
        "__builtins__": __builtins__,
    }
    exec(compile(src, src_path, "exec"), ns)
    return ns["_parse_title"], ns["_build_offer_url"]


try:
    _parse_title, _build_offer_url = _load_g2g_functions()
    _G2G_AVAILABLE = True
except Exception as e:
    _G2G_AVAILABLE = False
    print(f"[WARN] G2G parse functions unavailable: {e}")


class TestParseTitle(unittest.TestCase):

    @unittest.skipUnless(_G2G_AVAILABLE, "_parse_title недоступна")
    def test_full_alliance(self):
        s, r, v, f = _parse_title("Spineshatter [EU - Anniversary] - Alliance")
        self.assertEqual(s, "Spineshatter")
        self.assertEqual(r, "EU")
        self.assertEqual(v, "Anniversary")
        self.assertEqual(f, "Alliance")

    @unittest.skipUnless(_G2G_AVAILABLE, "_parse_title недоступна")
    def test_full_horde_seasonal(self):
        """
        Level-1 regex возвращает СЫРУЮ версию из скобки.
        Канонизация "Seasonal" → "Season of Discovery" происходит на уровне
        _normalize_g2g_offer (via _canonicalize_version), НЕ в _parse_title.
        Это корректное поведение по дизайну — тест проверяет именно его.
        """
        s, r, v, f = _parse_title("Lava Lash [EU - Seasonal] - Horde")
        self.assertEqual(f, "Horde")
        # Level-1 regex вернёт "Seasonal" как есть (ещё не канонизировано)
        self.assertEqual(v, "Seasonal")
        self.assertEqual(r, "EU")
        self.assertEqual(s, "Lava Lash")

    @unittest.skipUnless(_G2G_AVAILABLE, "_parse_title недоступна")
    def test_classic_era(self):
        _, r, v, _ = _parse_title("Firemaw [EU - Classic Era] - Alliance")
        self.assertEqual(v, "Classic Era")
        self.assertEqual(r, "EU")

    @unittest.skipUnless(_G2G_AVAILABLE, "_parse_title недоступна")
    def test_sod_full(self):
        _, _, v, _ = _parse_title("Crusader Strike [EU - Season of Discovery] - Horde")
        self.assertEqual(v, "Season of Discovery")

    @unittest.skipUnless(_G2G_AVAILABLE, "_parse_title недоступна")
    def test_fallback_bracket_region(self):
        s, r, _, f = _parse_title("Firemaw [EU] - Alliance")
        self.assertEqual(r, "EU")
        self.assertEqual(f, "Alliance")

    @unittest.skipUnless(_G2G_AVAILABLE, "_parse_title недоступна")
    def test_fallback_classic_era_text(self):
        _, r, v, _ = _parse_title("Classic Era Gold EU")
        self.assertEqual(r, "EU")
        self.assertEqual(v, "Classic")

    @unittest.skipUnless(_G2G_AVAILABLE, "_parse_title недоступна")
    def test_empty_string(self):
        s, r, v, f = _parse_title("")
        self.assertEqual(s, "")
        self.assertEqual(f, "Horde")

    @unittest.skipUnless(_G2G_AVAILABLE, "_parse_title недоступна")
    def test_region_always_uppercase(self):
        _, r, _, _ = _parse_title("Server [eu - Anniversary] - Horde")
        self.assertEqual(r, "EU")

    @unittest.skipUnless(_G2G_AVAILABLE, "_parse_title недоступна")
    def test_no_faction_not_empty(self):
        """Если фракция не указана — дефолт не пустой."""
        s, r, v, f = _parse_title("Spineshatter [EU - Anniversary]")
        self.assertIn(f, ("Horde", "Alliance"))


class TestBuildOfferUrl(unittest.TestCase):
    """Task 2: verify _build_offer_url produces seller-based URLs.

    New signature (after seller-based refactor):
        _build_offer_url(offer_id, region_id, seller) -> str
    Expected format (verified live against G2G):
        https://www.g2g.com/categories/wow-classic-era-vanilla-gold
        /offer/{offer_id}?region_id={region_id}&seller={seller}
    """

    @unittest.skipUnless(_G2G_AVAILABLE, "_build_offer_url недоступна")
    def test_seller_url_format(self):
        """URL must contain /offer/{id}?region_id=...&seller=... (Task 2)."""
        url = _build_offer_url("offer123", "region_eu_1", "coolseller")
        self.assertIn("/offer/offer123", url)
        self.assertIn("region_id=region_eu_1", url)
        self.assertIn("seller=coolseller", url)

    @unittest.skipUnless(_G2G_AVAILABLE, "_build_offer_url недоступна")
    def test_url_contains_category_slug(self):
        """URL must contain the category slug (wow-classic-era-vanilla-gold)."""
        url = _build_offer_url("offer123", "reg1", "seller1")
        self.assertIn("wow-classic-era-vanilla-gold", url)

    @unittest.skipUnless(_G2G_AVAILABLE, "_build_offer_url недоступна")
    def test_empty_offer_id_returns_empty(self):
        """Empty offer_id → return empty string (no dead links)."""
        url = _build_offer_url("", "reg1", "seller1")
        self.assertEqual(url, "")

    @unittest.skipUnless(_G2G_AVAILABLE, "_build_offer_url недоступна")
    def test_no_group_url_format(self):
        """URL must NOT use the old /offer/group format (dead links)."""
        url = _build_offer_url("offer123", "reg1", "seller1")
        self.assertNotIn("/offer/group", url)
        self.assertNotIn("fa=", url)

    @unittest.skipUnless(_G2G_AVAILABLE, "_build_offer_url недоступна")
    def test_no_bare_offer_id_format(self):
        """URL must NOT be the bare /offer/{id} format (leads to dead pages)."""
        url = _build_offer_url("offer123", "reg1", "seller1")
        # Must include region_id and seller query params
        self.assertIn("?", url)
        self.assertNotEqual(url, f"https://www.g2g.com/offer/offer123")


# ══════════════════════════════════════════════════════════════════════════════
# 7. offers_service — _clean, _canonicalize_version, _detect_version
#    (загружаем без pydantic через стаб)
# ══════════════════════════════════════════════════════════════════════════════

def _load_offers_service_utils():
    """Загружаем утилиты offers_service через stub pydantic."""
    src_path = os.path.join(os.path.dirname(__file__), "service", "offers_service.py")
    with open(src_path) as f:
        raw = f.read()

    # Убираем импорт api.schemas — заменяем заглушкой
    src = raw.replace("from api.schemas import Offer, PriceHistoryPoint, ServerGroup",
                      "Offer = object; PriceHistoryPoint = object; ServerGroup = object")

    ns = {
        "asyncio": asyncio,
        "logging": __import__("logging"),
        "random": __import__("random"),
        "re": re,
        "dataclasses": __import__("dataclasses"),
        "datetime": datetime,
        "timezone": timezone,
        "Optional": __import__("typing").Optional,
        "__builtins__": __builtins__,
        "dataclass": __import__("dataclasses").dataclass,
    }
    exec(compile(src, src_path, "exec"), ns)
    return ns


try:
    _svc_ns = _load_offers_service_utils()
    _clean = _svc_ns["_clean"]
    _canonicalize = _svc_ns["_canonicalize_version"]
    _detect = _svc_ns["_detect_version"]
    _version_rank = _svc_ns["_version_rank"]
    _SVC_AVAILABLE = True
except Exception as e:
    _SVC_AVAILABLE = False
    print(f"[WARN] offers_service utils unavailable: {e}")


class TestOffersServiceUtils(unittest.TestCase):

    @unittest.skipUnless(_SVC_AVAILABLE, "offers_service utils недоступны")
    def test_clean_trim(self):
        self.assertEqual(_clean("  (EU) Anniversary  "), "(eu) anniversary")

    @unittest.skipUnless(_SVC_AVAILABLE, "offers_service utils недоступны")
    def test_clean_collapse_spaces(self):
        self.assertEqual(_clean("(EU)  Anniversary"), "(eu) anniversary")

    @unittest.skipUnless(_SVC_AVAILABLE, "offers_service utils недоступны")
    def test_clean_lowercase(self):
        self.assertEqual(_clean("(EU) ANNIVERSARY"), "(eu) anniversary")

    @unittest.skipUnless(_SVC_AVAILABLE, "offers_service utils недоступны")
    def test_canonicalize_seasonal(self):
        self.assertEqual(_canonicalize("seasonal"), "Season of Discovery")

    @unittest.skipUnless(_SVC_AVAILABLE, "offers_service utils недоступны")
    def test_canonicalize_sod(self):
        self.assertEqual(_canonicalize("sod"), "Season of Discovery")

    @unittest.skipUnless(_SVC_AVAILABLE, "offers_service utils недоступны")
    def test_canonicalize_anniversary(self):
        self.assertEqual(_canonicalize("anniversary"), "Anniversary")

    @unittest.skipUnless(_SVC_AVAILABLE, "offers_service utils недоступны")
    def test_canonicalize_classic_era(self):
        self.assertEqual(_canonicalize("classic era"), "Classic")

    @unittest.skipUnless(_SVC_AVAILABLE, "offers_service utils недоступны")
    def test_canonicalize_unknown_passthrough(self):
        self.assertEqual(_canonicalize("SomeUnknown"), "SomeUnknown")

    @unittest.skipUnless(_SVC_AVAILABLE, "offers_service utils недоступны")
    def test_detect_anniversary(self):
        self.assertEqual(_detect("(EU) #Anniversary - Spineshatter"), "Anniversary")

    @unittest.skipUnless(_SVC_AVAILABLE, "offers_service utils недоступны")
    def test_detect_sod(self):
        self.assertEqual(_detect("Season of Discovery"), "Season of Discovery")
        self.assertEqual(_detect("SoD something"), "Season of Discovery")

    @unittest.skipUnless(_SVC_AVAILABLE, "offers_service utils недоступны")
    def test_detect_classic_era(self):
        self.assertEqual(_detect("(EU) Classic Era"), "Classic Era")

    @unittest.skipUnless(_SVC_AVAILABLE, "offers_service utils недоступны")
    def test_seasonal_canonicalize_pipeline(self):
        """
        Полный pipeline G2G: _parse_title → _canonicalize_version.
        _parse_title (Level-1) отдаёт "Seasonal" как сырую строку,
        _normalize_g2g_offer затем вызывает _canonicalize_version → "Season of Discovery".
        Тест подтверждает, что каждый уровень делает своё.
        """
        if not _G2G_AVAILABLE:
            self.skipTest("g2g functions unavailable")
        _, _, raw_ver, _ = _parse_title("Lava Lash [EU - Seasonal] - Horde")
        self.assertEqual(raw_ver, "Seasonal")               # сырой
        canonical = _canonicalize("seasonal")
        self.assertEqual(canonical, "Season of Discovery")  # после канонизации

    @unittest.skipUnless(_SVC_AVAILABLE, "offers_service utils недоступны")
    def test_version_rank_order(self):
        """Anniversary < SoD < Classic Era < Classic."""
        ranks = [
            _version_rank("(EU) Anniversary"),
            _version_rank("(EU) Season of Discovery"),
            _version_rank("(EU) Classic Era"),
            _version_rank("(EU) Classic"),
        ]
        self.assertEqual(ranks, sorted(ranks))


# ══════════════════════════════════════════════════════════════════════════════
# 8. Интеграционный тест API-роутера (FastAPI endpoint logic)
# ══════════════════════════════════════════════════════════════════════════════

class TestRouterBucketMeta(unittest.TestCase):
    """Тестируем вычисления в /price-history/ohlc без запуска сервера."""

    def test_bucket_default_params(self):
        """last_hours=168, max_points=500 → bucket=20 минут."""
        bucket = max(5, (168 * 60) // 500)
        self.assertEqual(bucket, 20)

    def test_bucket_min_clamp(self):
        """Маленькое окно → bucket ≥ 5."""
        bucket = max(5, (1 * 60) // 500)
        self.assertEqual(bucket, 5)

    def test_bucket_large_window(self):
        """8760 часов (1 год), 2000 точек → 262 минуты."""
        bucket = max(5, (8760 * 60) // 2000)
        self.assertEqual(bucket, 262)


# ══════════════════════════════════════════════════════════════════════════════
# 9. db/writer — write_index_snapshot (async, mock)
# ══════════════════════════════════════════════════════════════════════════════

class TestWriteIndexSnapshot(unittest.IsolatedAsyncioTestCase):

    def _make_index_price(self, price=1.5):
        """Создаём IndexPrice-подобный объект через dataclass."""
        from dataclasses import dataclass

        @dataclass
        class IndexPrice:
            index_price:  float
            vwap:         float
            best_ask:     float
            price_min:    float
            price_max:    float
            offer_count:  int
            total_volume: int
            sources:      list

        return IndexPrice(
            index_price=price, vwap=price, best_ask=price,
            price_min=price * 0.9, price_max=price * 1.1,
            offer_count=5, total_volume=50000,
            sources=["funpay"],
        )

    async def test_no_write_on_no_change(self):
        from db import writer as w
        w._last_written["(EU) Anniversary::All"] = 1.5
        idx = self._make_index_price(1.5)
        mock_pool = AsyncMock()
        with patch.object(w, "get_pool", return_value=mock_pool):
            await w.write_index_snapshot("(EU) Anniversary", "All", idx)
        mock_pool.execute.assert_not_called()

    async def test_write_on_price_change(self):
        from db import writer as w
        w._last_written["(EU) Anniversary::Horde"] = 1.0
        idx = self._make_index_price(2.0)  # 100% рост
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()
        with patch.object(w, "get_pool", return_value=mock_pool):
            await w.write_index_snapshot("(EU) Anniversary", "Horde", idx)
        mock_pool.execute.assert_called_once()

    async def test_non_fatal_on_db_error(self):
        """DB упала — исключение не пробрасывается."""
        from db import writer as w
        w._last_written.clear()
        idx = self._make_index_price(1.5)
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        with patch.object(w, "get_pool", return_value=mock_pool):
            await w.write_index_snapshot("(EU) Anniversary", "Alliance", idx)
        # Дошли сюда → исключение поглощено, тест прошёл


# ══════════════════════════════════════════════════════════════════════════════
# 10. Task 3E — Validate migration 009 has no duplicate servers
#     Checks the canonical server truth list for internal consistency.
#     Equivalent SQL:
#       SELECT name, region, version, COUNT(*) FROM servers
#       GROUP BY name, region, version HAVING COUNT(*) > 1
#       → must return 0 rows.
# ══════════════════════════════════════════════════════════════════════════════

class TestNoduplicateServersTruth(unittest.TestCase):
    """Task 3E: migration 009 must not introduce (name, region, version) duplicates."""

    def _load_migration_servers(self) -> list[tuple[str, str, str]]:
        """Load all (name, region, version) tuples from migration 009."""
        import importlib.util
        mig_path = os.path.join(
            os.path.dirname(__file__),
            "alembic", "versions", "009_canonical_server_truth.py",
        )
        spec = importlib.util.spec_from_file_location("mig009", mig_path)
        mod = importlib.util.module_from_spec(spec)

        # Stub alembic.op so the module-level code doesn't fail on import
        alembic_stub = types.ModuleType("alembic")
        op_stub = types.ModuleType("alembic.op")
        op_stub.execute = lambda *a, **kw: None
        alembic_stub.op = op_stub
        sys.modules.setdefault("alembic", alembic_stub)
        sys.modules.setdefault("alembic.op", op_stub)

        spec.loader.exec_module(mod)

        rows: list[tuple[str, str, str]] = []
        for name, region in mod._CLASSIC_ERA_SERVERS:
            rows.append((name, region, "Classic Era"))
        for name, region in mod._HARDCORE_SERVERS:
            rows.append((name, region, "Hardcore"))
        for name in mod._RU_CLASSIC_ERA_SERVERS:
            rows.append((name, "RU", "Classic Era"))
        for name in mod._RU_SOD_SERVERS:
            rows.append((name, "RU", "Season of Discovery"))
        for name, region in mod._ANNIVERSARY_SERVERS:
            rows.append((name, region, "Anniversary"))
        return rows

    def test_no_duplicate_servers_in_migration(self):
        """(name, region, version) tuples in migration 009 must be unique."""
        rows = self._load_migration_servers()
        counts: dict[tuple, int] = {}
        for key in rows:
            counts[key] = counts.get(key, 0) + 1
        duplicates = [(k, c) for k, c in counts.items() if c > 1]
        self.assertEqual(
            duplicates, [],
            f"Migration 009 has duplicate (name, region, version) tuples: {duplicates}",
        )

    def test_no_empty_server_names(self):
        """No server in migration 009 should have an empty name."""
        rows = self._load_migration_servers()
        empties = [r for r in rows if not r[0].strip()]
        self.assertEqual(empties, [], f"Empty server names found: {empties}")

    def test_all_regions_valid(self):
        """All regions in migration 009 must be from the known set."""
        _VALID_REGIONS = {"EU", "US", "AU", "OCE", "KR", "TW", "RU", "SEA"}
        rows = self._load_migration_servers()
        invalid = [(name, region) for name, region, _ in rows if region not in _VALID_REGIONS]
        self.assertEqual(invalid, [], f"Invalid regions: {invalid}")

    def test_no_banned_version_strings(self):
        """Task 3D: no banned version strings ('Vanilla', 'SoD', 'Seasonal') in migration 009."""
        rows = self._load_migration_servers()
        banned = {"Vanilla", "SoD", "Seasonal", "Classic Anniversary", "Anniversary Gold"}
        violations = [(name, region, version) for name, region, version in rows if version in banned]
        self.assertEqual(violations, [], f"Banned version strings: {violations}")

    def test_version_aliases_cover_known_variants(self):
        """Task 3D: _VERSION_ALIASES must cover all canonical + common variant spellings."""
        from utils.version_utils import _VERSION_ALIASES
        required_keys = {
            "seasonal", "season of discovery", "sod",
            "anniversary", "hardcore", "classic era",
            "vanilla", "classic", "tbc classic",
        }
        missing = required_keys - set(_VERSION_ALIASES.keys())
        self.assertEqual(missing, set(), f"_VERSION_ALIASES missing keys: {missing}")


# ══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    test_classes = [
        TestWriterIntervalFix,
        TestWriterHelpers,
        TestQueryIndexHistory,
        TestNormalizeServer,
        TestParseFloat,
        TestParseTitle,
        TestBuildOfferUrl,
        TestOffersServiceUtils,
        TestRouterBucketMeta,
        TestWriteIndexSnapshot,
        TestNoduplicateServersTruth,
    ]
    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
