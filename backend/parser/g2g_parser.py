"""
g2g_parser.py — Production-ready G2G parser.

Verified pipeline:
  1) /offer/keyword_relation/region  -> region_id + relation_id
  2) /offer/search                   -> offers (username, unit_price_in_usd, qty)
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from api.schemas import Offer

logger = logging.getLogger(__name__)

SOURCE = "g2g"
BASE = "https://sls.g2g.com"

# IMPORTANT: httpx при уровне INFO пишет "HTTP Request: ...", что забивает логи.
# Оставляем логи в основном модуле, а сетевой "access log" глушим.
for _httpx_logger_name in ("httpx", "httpcore"):
    logging.getLogger(_httpx_logger_name).setLevel(logging.WARNING)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.g2g.com/",
    "Origin": "https://www.g2g.com",
}

GAME_CONFIG: dict[str, dict[str, str]] = {
    # Верифицировано: https://www.g2g.com/categories/wow-classic-era-vanilla-gold
    # Покрывает Classic Era, Seasonal, TBC Anniversary — один brand_id
    "wow_classic_era_seasonal_anniversary": {
        "brand_id":   "lgc_game_27816",
        "service_id": "lgc_service_1",
        "label":      "WoW Classic Era / Seasonal / TBC Anniversary",
    },
}

# Категория G2G для построения ссылок
_CATEGORY_SLUG = "wow-classic-era-vanilla-gold"


def _build_g2g_offer_url(region_id: str) -> str:
    """
    Строит рабочую ссылку на страницу группы офферов G2G для данного региона.

    Использует упрощённый формат без fa-параметра (работает в браузере).
    Полный формат с fa: ?fa=col_id:dat_id — можно добавить позже если нужна
    фильтрация по конкретному реалму внутри региона.
    """
    from urllib.parse import urlencode
    base = f"https://www.g2g.com/categories/{_CATEGORY_SLUG}/offer/group"
    params = {"region_id": region_id, "sort": "lowest_price"}
    return f"{base}?{urlencode(params)}"


# Строгий regex: "Server [Region - Version] - Faction"
_TITLE_RE = re.compile(
    r"^(?P<server>.+?)\s*"
    r"\[(?P<region>[A-Za-z]{2,})\s*-\s*(?P<version>[^\]]+?)\]\s*"
    r"(?:-\s*(?P<faction>Alliance|Horde))?$",
    re.IGNORECASE,
)

# Вспомогательные regex для гибкого fallback-парсинга
_REGION_RE         = re.compile(r"\b(EU|US|NA|OCE|KR|TW|SEA|RU)\b", re.IGNORECASE)
_BRACKET_REGION_RE = re.compile(r"\[([A-Za-z]{2,})\]")   # "[EU]" без версии
_FACTION_END_RE    = re.compile(r"-\s*(Alliance|Horde)\s*$", re.IGNORECASE)

# Версии в порядке приоритета (более длинные/специфичные — первыми).
# "Seasonal" на G2G == Season of Discovery (те же серверы: Crusader Strike, Lava Lash…).
_VERSION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Season of Discovery", re.compile(r"season\s+of\s+discovery|\bseasonal\b", re.I)),
    ("Anniversary",         re.compile(r"anniversary",                           re.I)),
    ("Classic Era",         re.compile(r"classic\s+era",                         re.I)),
    ("Classic",             re.compile(r"\bclassic\b",                           re.I)),
]


@dataclass
class G2GOffer:
    offer_id: str
    title: str
    server_name: str
    region_id: str
    relation_id: str
    price_usd: float
    min_qty: int
    available_qty: int
    seller: str
    brand_id: str
    service_id: str
    offer_url: Optional[str] = None   # правильный URL: группа или одиночный оффер
    is_group: bool = False            # True = is_group_display (групповой листинг)
    total_sellers: int = 1            # total_offer из группового листинга
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class G2GRegion:
    region_id: str
    relation_id: str


def _parse_title(title: str) -> tuple[str, str, str, str]:
    """Парсит RAW title G2G оффера → (server_name, region, version, faction).

    Двухуровневый парсинг:

    Уровень 1 — строгий regex (покрывает большинство тайтлов):
        "Spineshatter [EU - Anniversary] - Alliance"
          → ("Spineshatter", "EU", "Anniversary", "Alliance")
        "Lava Lash [EU - Seasonal] - Horde"
          → ("Lava Lash", "EU", "Season of Discovery", "Horde")

    Уровень 2 — гибкий fallback (нестандартные форматы):
        "Firemaw [EU] - Alliance"     → ("Firemaw",  "EU", "Classic",            "Alliance")
        "Classic Era Gold EU"         → ("",          "EU", "Classic Era",         "Horde")
        "Season of Discovery Gold"    → ("",          "",   "Season of Discovery",  "Horde")
    """
    t = (title or "").strip()
    if not t:
        return "", "", "", "Horde"

    # ── Уровень 1: строгий regex ──────────────────────────────────────────────
    m = _TITLE_RE.match(t)
    if m:
        server_name = (m.group("server") or "").strip()
        region      = (m.group("region") or "").upper().strip()
        version     = (m.group("version") or "").strip()
        faction     = (m.group("faction") or "").strip().capitalize() or (
            "Alliance" if "alliance" in t.lower() else "Horde"
        )
        return server_name, region, version, faction

    # ── Уровень 2: гибкий fallback ────────────────────────────────────────────

    # Faction: ищем " - Alliance" / " - Horde" в конце, иначе по вхождению
    fm = _FACTION_END_RE.search(t)
    if fm:
        faction = fm.group(1).capitalize()
    elif "alliance" in t.lower():
        faction = "Alliance"
    else:
        faction = "Horde"

    # server_name: часть до первой "[", либо до последнего " - faction"
    server_name = ""
    if "[" in t:
        server_name = t[:t.index("[")].strip()
    else:
        parts = re.split(r"\s+-\s+", t)
        if len(parts) >= 2 and parts[-1].strip().lower() in ("alliance", "horde"):
            server_name = parts[0].strip()

    # Region: сначала ищем в скобках "[EU]", затем в любом месте
    region = ""
    _KNOWN = {"EU", "US", "NA", "OCE", "KR", "TW", "SEA", "RU"}
    bm = _BRACKET_REGION_RE.search(t)
    if bm and bm.group(1).upper() in _KNOWN:
        region = bm.group(1).upper()
    else:
        rm = _REGION_RE.search(t)
        if rm:
            region = rm.group(1).upper()

    # Version: по ключевым словам в порядке приоритета
    version = ""
    for ver_name, pattern in _VERSION_PATTERNS:
        if pattern.search(t):
            version = ver_name
            break

    # Если регион есть, но версия не найдена — дефолт "Classic"
    if region and not version:
        version = "Classic"

    # Если server_name так и не определился — используем полный title как fallback
    if not server_name:
        server_name = t

    return server_name, region, version, faction


class G2GClient:
    def __init__(self, country: str = "SG", currency: str = "USD"):
        self.country = country
        self.currency = currency
        self._client: Optional[httpx.AsyncClient] = None
        self._region_cache: dict[str, list[G2GRegion]] = {}

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            headers=HEADERS,
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_):
        if self._client:
            await self._client.aclose()

    async def fetch_regions(self, brand_id: str, service_id: str) -> list[G2GRegion]:
        cache_key = f"{brand_id}:{service_id}"
        if cache_key in self._region_cache:
            return self._region_cache[cache_key]

        resp = await self._client.get(
            f"{BASE}/offer/keyword_relation/region",
            params={
                "brand_id": brand_id,
                "service_id": service_id,
                "country": self.country,
            },
        )
        resp.raise_for_status()

        payload = resp.json().get("payload", {})
        regions = [
            G2GRegion(region_id=r["region_id"], relation_id=r["relation_id"])
            for r in payload.get("results", [])
            if r.get("region_id") and r.get("relation_id")
        ]
        self._region_cache[cache_key] = regions
        logger.info(
            "G2G regions: brand_id=%s service_id=%s country=%s -> %d regions",
            brand_id,
            service_id,
            self.country,
            len(regions),
        )
        return regions

    def _make_offer(self, o: dict, brand_id: str, service_id: str) -> G2GOffer:
        """Создаёт G2GOffer из сырого dict API-ответа."""
        offer_id = o.get("offer_id", "")
        return G2GOffer(
            offer_id=offer_id,
            title=o.get("title", ""),
            server_name=(_parse_title(o.get("title", ""))[0] or o.get("title", "")),
            region_id=o.get("region_id", ""),
            relation_id=o.get("relation_id", ""),
            price_usd=float(o.get("unit_price_in_usd") or 0),
            min_qty=int(o.get("min_qty") or 1),
            available_qty=int(o.get("available_qty") or 0),
            seller=(o.get("username") or "").strip(),
            brand_id=brand_id,
            service_id=service_id,
            offer_url=f"https://www.g2g.com/offer/{offer_id}" if offer_id else None,
            is_group=bool(o.get("is_group_display", False)),
            total_sellers=int(o.get("total_offer") or 1),
            raw=o,
        )

    async def _expand_group(
        self,
        brand_id: str,
        service_id: str,
        relation_id: str,
        offer_id: str,
        offer_group: str,
        max_sellers: int = 10,
    ) -> list[G2GOffer]:
        """
        Получает до max_sellers продавцов группы.
        Один запрос page_size=100, фильтрация client-side по offer_group.
        Никаких циклов — один запрос, детерминированный результат.
        """
        try:
            resp = await self._client.get(
                f"{BASE}/offer/search",
                params={
                    "brand_id":    brand_id,
                    "service_id":  service_id,
                    "relation_id": relation_id,
                    "country":     self.country,
                    "currency":    self.currency,
                    "group":       "0",
                    "offer_id":    offer_id,
                    "page":        1,
                    "page_size":   100,
                    "v":           "v2",
                    "sort":        "lowest_price",
                },
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning("G2G _expand_group failed offer_id=%s: %s", offer_id, e)
            return []

        results = resp.json().get("payload", {}).get("results", [])

        # Фильтруем client-side по offer_group
        matching = [
            o for o in results
            if o.get("offer_group") == offer_group
            and not o.get("is_group_display", True)
        ]

        # Уже отсортированы по lowest_price, берём топ max_sellers
        out: list[G2GOffer] = []
        for o in matching[:max_sellers]:
            try:
                out.append(self._make_offer(o, brand_id, service_id))
            except (ValueError, TypeError) as e:
                logger.debug("G2G _expand_group skip offer: %s", e)
        return out

    async def fetch_offers(
        self,
        brand_id: str,
        service_id: str,
        relation_id: str,
        sort: str = "lowest_price",
        page_size: int = 48,
    ) -> list[G2GOffer]:
        all_offers: list[G2GOffer] = []
        page = 1
        max_pages = 10

        while page <= max_pages:
            try:
                resp = await self._client.get(
                    f"{BASE}/offer/search",
                    params={
                        "brand_id":    brand_id,
                        "service_id":  service_id,
                        "relation_id": relation_id,
                        "country":     self.country,
                        "currency":    self.currency,
                        "sort":        sort,
                        "page":        page,
                        "page_size":   page_size,
                    },
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.warning("G2G fetch_offers page=%d failed: %s", page, e)
                break

            results = resp.json().get("payload", {}).get("results", [])
            if not results:
                break

            # Диагностика: структура raw payload (первые 3 оффера первой страницы)
            if page == 1:
                for _dbg_o in results[:3]:
                    logger.debug(
                        "G2G raw offer sample: offer_id=%s available_qty=%s "
                        "min_qty=%s price=%s is_group=%s offer_group=%r title=%r",
                        _dbg_o.get("offer_id"),
                        _dbg_o.get("available_qty"),
                        _dbg_o.get("min_qty"),
                        _dbg_o.get("unit_price_in_usd"),
                        _dbg_o.get("is_group_display"),
                        _dbg_o.get("offer_group"),
                        _dbg_o.get("title"),
                    )

            singles: list[dict] = []
            group_tasks: list = []

            for o in results:
                is_group = o.get("is_group_display", False)
                total    = int(o.get("total_offer") or 1)
                og       = o.get("offer_group", "")
                oid      = o.get("offer_id", "")

                if is_group and total > 1 and og and oid:
                    group_tasks.append(
                        self._expand_group(
                            brand_id, service_id, relation_id,
                            oid, og, min(total, 10),
                        )
                    )
                else:
                    singles.append(o)

            for o in singles:
                try:
                    all_offers.append(self._make_offer(o, brand_id, service_id))
                except (ValueError, TypeError):
                    continue

            if group_tasks:
                expanded_lists = await asyncio.gather(*group_tasks, return_exceptions=True)
                for result in expanded_lists:
                    if isinstance(result, list):
                        all_offers.extend(result)
                    elif isinstance(result, Exception):
                        logger.warning("G2G expand group error: %s", result)

            logger.debug(
                "G2G: relation_id=%s page=%d → %d raw, итого %d офферов",
                relation_id, page, len(results), len(all_offers),
            )

            if len(results) < page_size:
                break

            page += 1
            await asyncio.sleep(0.2)

        return all_offers


async def fetch_g2g_game(
    game_key: str,
    sort: str = "lowest_price",
    country: str = "SG",
    max_regions: Optional[int] = None,
    delay: float = 0.35,
) -> list[G2GOffer]:
    if game_key not in GAME_CONFIG:
        raise ValueError(f"Unknown game: {game_key}. Available: {list(GAME_CONFIG)}")

    cfg = GAME_CONFIG[game_key]
    brand_id   = cfg["brand_id"]
    service_id = cfg["service_id"]
    all_offers: list[G2GOffer] = []

    async with G2GClient(country=country) as client:
        regions = await client.fetch_regions(brand_id, service_id)
        if max_regions:
            regions = regions[:max_regions]
        logger.info(
            "G2G game=%s: servers(regions)=%d (brand_id=%s service_id=%s country=%s)",
            game_key,
            len(regions),
            brand_id,
            service_id,
            country,
        )

        loaded_regions = 0
        for i, region in enumerate(regions):
            try:
                logger.info(
                    "G2G game=%s loading %d/%d: relation_id=%s region_id=%s",
                    game_key,
                    i + 1,
                    len(regions),
                    region.relation_id,
                    region.region_id,
                )
                offers = await client.fetch_offers(
                    brand_id=brand_id,
                    service_id=service_id,
                    relation_id=region.relation_id,
                    sort=sort,
                )
                all_offers.extend(offers)
                loaded_regions += 1
            except httpx.HTTPStatusError as e:
                logger.warning("G2G HTTP error for region %s: %s", region.region_id, e)
            if i < len(regions) - 1:
                await asyncio.sleep(delay)

        unique_sellers = {o.seller for o in all_offers if o.seller}
        logger.info(
            "G2G game=%s done: regions_found=%d regions_loaded=%d offers=%d unique_sellers=%d",
            game_key,
            len(regions),
            loaded_regions,
            len(all_offers),
            len(unique_sellers),
        )

    return all_offers


async def discover_brand_ids(
    category_id: str = "3c2a9034-2569-4484-92ad-c00e384e7085",
) -> list[dict]:
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as c:
        r = await c.get(
            f"{BASE}/offer/category/{category_id}/popular_brand",
            params={"country": "SG"},
        )
        r.raise_for_status()
        return r.json().get("payload", {}).get("results", [])


_MAX_PRICE_PER_1K = 300.0  # Жёсткий потолок: выше — аномалия, пропускаем


def _to_offer(raw: G2GOffer, fetched_at: datetime) -> Optional[Offer]:
    if raw.price_usd <= 0:
        return None

    # available_qty в G2G API может приходить как null/0 даже для живых офферов.
    # Используем min_qty как fallback — лучше показать оффер с неточным кол-вом,
    # чем потерять его совсем.
    amount_gold = raw.available_qty if raw.available_qty > 0 else raw.min_qty
    if amount_gold <= 0:
        return None

    price_per_1k = round(raw.price_usd * 1000.0, 4)
    if price_per_1k > _MAX_PRICE_PER_1K:
        return None

    server_name, region, version, faction = _parse_title(raw.title)
    # Строим display_server в формате: "(EU) Version"
    # Если ни региона, ни версии — оффер невозможно сгруппировать, пропускаем.
    if region and version:
        display_server = f"({region}) {version}"
    elif version:
        display_server = version
    else:
        logger.debug("G2G: пропуск нераспознанного оффера title=%r", raw.title)
        return None

    # Правильная ссылка: страница группы офферов G2G для данного региона
    if raw.region_id:
        offer_url = _build_g2g_offer_url(raw.region_id)
    else:
        offer_url = raw.offer_url  # fallback

    seller = raw.seller or "unknown"

    try:
        return Offer(
            id=f"g2g_{raw.offer_id}" if raw.offer_id else f"g2g_{raw.relation_id}",
            source=SOURCE,
            server=display_server,
            display_server=display_server,
            server_name=server_name,
            faction=faction,
            price_per_1k=price_per_1k,
            amount_gold=amount_gold,
            seller=seller,
            offer_url=offer_url,
            updated_at=fetched_at,
            fetched_at=fetched_at,
        )
    except Exception:
        return None


def _dedupe(offers: list[Offer]) -> list[Offer]:
    seen: set[str] = set()
    out: list[Offer] = []
    for offer in offers:
        if offer.id in seen:
            continue
        seen.add(offer.id)
        out.append(offer)
    return out


async def fetch_offers() -> list[Offer]:
    """
    Entrypoint для offers_service._run_g2g_loop().
    Парсит WoW Classic Era / Seasonal / TBC Anniversary (один brand_id).
    """
    fetched_at = datetime.now(timezone.utc)
    try:
        raw_offers = await fetch_g2g_game("wow_classic_era_seasonal_anniversary")

        total         = len(raw_offers)
        skipped_price = 0
        skipped_qty   = 0
        ok            = 0
        converted: list[Offer] = []

        for r in raw_offers:
            if r.price_usd <= 0:
                skipped_price += 1
                continue
            offer = _to_offer(r, fetched_at)
            if offer is None:
                skipped_qty += 1
            else:
                ok += 1
                converted.append(offer)

        logger.info(
            "G2G _to_offer: total=%d skipped_price=%d skipped_qty=%d ok=%d",
            total, skipped_price, skipped_qty, ok,
        )
        return _dedupe(converted)
    except Exception:
        logger.exception("G2G parser failed")
        return []
