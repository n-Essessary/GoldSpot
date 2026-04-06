"""
service/offers_service.py — In-memory offer cache + background parser loops.

Architecture:
  - Separate background loops per parser (FunPay, G2G).
  - Reads from _cache are < 5 ms (no DB, no blocking).
  - After each parse cycle, fire-and-forget:
      1. _snapshot_all_servers()   — group-level IndexPrice → DB (legacy OHLC)
      2. _snapshot_server_indexes() — per-real-server index → DB (Task 4)
      3. write_price_snapshots()    — raw offer prices → DB (Task 1)

Task 2: price_per_1k is NEVER stored; always derived from raw_price at read-time.
Task 3: server_resolver maps raw titles → canonical server_id.
Task 4: index computed per individual server (not per group).
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from api.schemas import Offer, PriceHistoryPoint, ServerGroup

logger = logging.getLogger(__name__)

# ── Per-source state ──────────────────────────────────────────────────────────
_cache:         dict[str, list[Offer]]        = {"funpay": [], "g2g": []}
_last_update:   dict[str, Optional[datetime]] = {"funpay": None, "g2g": None}
_running:       dict[str, bool]               = {"funpay": False, "g2g": False}
_cache_version: dict[str, int]               = {"funpay": 0, "g2g": 0}
_last_error:    dict[str, Optional[str]]      = {"funpay": None, "g2g": None}
_cache_initialized: dict[str, bool]          = {"funpay": False, "g2g": False}

FUNPAY_INTERVAL = 60
G2G_INTERVAL    = 30

# ── Analytics constants ───────────────────────────────────────────────────────
_OUTLIER_MULTIPLIER  = 3.0
_MIN_LIQUID_GOLD     = 50_000
_VWAP_GOLD_CAP       = 1_000_000
_MIN_OFFERS          = 2
_INDEX_TOP_N         = 10   # Task 4: top-N cheapest offers for server index

# Throttle for raw price snapshots: skip write if price changed less than this
_SNAP_WRITE_THRESHOLD = 0.005   # 0.5%
# Per-offer last-written price: offer_id → last raw_price written to DB
_last_snap_price: dict[str, float] = {}

# ── Version aliases ───────────────────────────────────────────────────────────
_VERSION_ALIASES: dict[str, str] = {
    "seasonal":            "Season of Discovery",
    "season of discovery": "Season of Discovery",
    "sod":                 "Season of Discovery",
    "anniversary":         "Anniversary",
    "classic era":         "Classic Era",
    "classic":             "Classic",
}

_VERSION_ORDER: dict[str, int] = {
    "Anniversary":         0,
    "Season of Discovery": 1,
    "Classic Era":         2,
    "Classic":             3,
}


# ── IndexPrice (group-level, legacy) ─────────────────────────────────────────

@dataclass
class IndexPrice:
    index_price:  float   # VW-Median
    vwap:         float
    best_ask:     float
    price_min:    float
    price_max:    float
    offer_count:  int
    total_volume: int
    sources:      list[str]


# In-memory index cache: key = "display_server::faction"
_index_cache: dict[str, IndexPrice] = {}


# ── Utilities ─────────────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _canonicalize_version(version: str) -> str:
    return _VERSION_ALIASES.get(version.lower().strip(), version)


def _detect_version(text: str) -> str:
    t = _clean(text)
    if "season of discovery" in t or re.search(r"\bsod\b|\bseasonal\b", t):
        return "Season of Discovery"
    if "anniversary" in t:
        return "Anniversary"
    if "classic era" in t:
        return "Classic Era"
    return "Classic"


def _normalize_funpay_offer(offer: Offer) -> Offer:
    """Normalise FunPay offer: set display_server to '(EU) Version' format."""
    raw = (offer.display_server or "").strip()
    m = re.match(r"^\((?P<region>[A-Za-z]{2,})\)\s*(?P<body>.*)$", raw)
    if not m:
        return offer

    region  = m.group("region").upper()
    body    = (m.group("body") or "").strip()
    version = _detect_version(body)
    realm   = ""

    if " - " in body:
        left, right = body.rsplit(" - ", 1)
        realm   = right.strip()
        version = _detect_version(left or body)
    else:
        realm = body.strip()

    offer.display_server = f"({region}) {version}"
    if realm:
        offer.server_name = realm
    return offer


def _normalize_g2g_offer(offer: Offer) -> Offer:
    """Canonicalise display_server of G2G offer via _VERSION_ALIASES."""
    ds = offer.display_server or ""
    m = re.match(r"^\((?P<region>[A-Za-z]{2,})\)\s*(?P<version>.+)$", ds)
    if m:
        region  = m.group("region").upper()
        version = _canonicalize_version(m.group("version").strip())
        offer.display_server = f"({region}) {version}"
        offer.server         = offer.display_server
    return offer


def _version_rank(display_server: str) -> int:
    ds = display_server.strip()
    for ver, rank in _VERSION_ORDER.items():
        if ver.lower() in ds.lower():
            return rank
    return 99


# ── Public cache read ─────────────────────────────────────────────────────────

def get_all_offers() -> list[Offer]:
    return _cache["funpay"] + _cache["g2g"]


def get_parser_status() -> dict:
    return {
        src: {
            "offers":      len(_cache[src]),
            "last_update": _last_update[src].isoformat() if _last_update[src] else None,
            "running":     _running[src],
            "version":     _cache_version[src],
            "last_error":  _last_error[src],
        }
        for src in ("funpay", "g2g")
    }


# ── Group-level IndexPrice (legacy) ──────────────────────────────────────────

def compute_index_price(offers: list[Offer]) -> IndexPrice | None:
    """
    Three-component price index (exchange approach).

    index_price = Volume-Weighted Median (resilient to volume outliers)
    vwap        = Volume-Weighted Average Price on top offers up to 1M gold
    best_ask    = first price where accumulated volume ≥ 50k gold
    """
    if not offers or len(offers) < _MIN_OFFERS:
        return None

    prices_sorted = sorted(o.price_per_1k for o in offers)
    raw_median = prices_sorted[len(prices_sorted) // 2]
    clean = [
        o for o in offers
        if o.price_per_1k <= raw_median * _OUTLIER_MULTIPLIER and o.price_per_1k > 0
    ]
    if len(clean) < _MIN_OFFERS:
        clean = [o for o in offers if o.price_per_1k > 0]
    if not clean:
        return None

    clean.sort(key=lambda o: o.price_per_1k)

    total_vol = sum(o.amount_gold for o in clean)
    cumulative, vw_median = 0, clean[0].price_per_1k
    for o in clean:
        cumulative += o.amount_gold
        if cumulative >= total_vol * 0.5:
            vw_median = o.price_per_1k
            break

    selected, acc = [], 0
    for o in clean:
        selected.append(o)
        acc += o.amount_gold
        if acc >= _VWAP_GOLD_CAP:
            break
    total_w = sum(o.amount_gold for o in selected)
    vwap = (
        sum(o.price_per_1k * o.amount_gold for o in selected) / total_w
        if total_w else clean[0].price_per_1k
    )

    acc_ask = 0
    best_ask = clean[0].price_per_1k
    for o in clean:
        acc_ask += o.amount_gold
        best_ask = o.price_per_1k
        if acc_ask >= _MIN_LIQUID_GOLD:
            break

    return IndexPrice(
        index_price  = round(vw_median, 6),
        vwap         = round(vwap, 6),
        best_ask     = round(best_ask, 6),
        price_min    = round(clean[0].price_per_1k, 6),
        price_max    = round(clean[-1].price_per_1k, 6),
        offer_count  = len(clean),
        total_volume = total_vol,
        sources      = sorted({o.source for o in clean}),
    )


# ── Task 4: per-server index computation ──────────────────────────────────────

def compute_server_index(
    server_id: int,
    faction: str,
    offers: list[Offer],
) -> dict | None:
    """
    Compute price index for a specific server_id + faction.

    Algorithm (Task 4):
      1. Filter offers to same server_id + faction.
      2. Sort by price_per_1k ASC — already normalised for both sources:
           G2G (per_unit):  price_per_1k = raw_price * 1000
           FunPay (per_lot): price_per_1k = (raw_price / lot_size) * 1000
         Sorting by raw_price directly would give wrong results because
         FunPay raw_price is per-lot (e.g. 3.0 for 1000 gold) while
         G2G raw_price is per-unit (e.g. 0.003 per 1 gold), making FunPay
         look 1000× more expensive.
      3. Take top _INDEX_TOP_N cheapest.
      4. Return mean as index_price in per-unit (per 1 gold) form.

    Returns dict with index_price (per unit), min, max, sample_size.
    Returns None if not enough offers.
    """
    matching = [
        o for o in offers
        if o.server_id == server_id
        and (faction == "All" or o.faction.lower() == faction.lower())
        and o.price_per_1k > 0  # use normalised price — correct for all sources
    ]

    if len(matching) < _MIN_OFFERS:
        return None

    # Sort by normalised price_per_1k ASC (works correctly for FunPay + G2G)
    matching.sort(key=lambda o: o.price_per_1k)
    top = matching[:_INDEX_TOP_N]

    # Compute mean in per-1k space, then convert to per-unit for DB storage
    mean_per_1k = sum(o.price_per_1k for o in top) / len(top)
    prices_per_1k = [o.price_per_1k for o in top]

    return {
        "index_price": round(mean_per_1k / 1000.0, 8),   # per unit (per 1 gold)
        "sample_size": len(top),
        "min_price":   round(min(prices_per_1k) / 1000.0, 8),
        "max_price":   round(max(prices_per_1k) / 1000.0, 8),
    }


# ── Background snapshots ──────────────────────────────────────────────────────

_snapshot_running = False   # guard against concurrent _snapshot_all_servers runs


async def _snapshot_all_servers() -> None:
    """
    After each parse cycle:
      1. Compute group-level IndexPrice → write_index_snapshot (legacy OHLC)
      2. Compute per-server index → upsert_server_index (Task 4)
      3. Write raw offer snapshots → write_price_snapshot (Task 1)

    Protected by _snapshot_running flag: if the previous snapshot hasn't
    finished (e.g. slow DB on Railway), the new call exits immediately to
    prevent duplicate writes and connection pool exhaustion.
    """
    global _snapshot_running
    if _snapshot_running:
        logger.debug("_snapshot_all_servers still running — skipping this cycle")
        return
    _snapshot_running = True
    try:
        await _do_snapshot_all_servers()
    finally:
        _snapshot_running = False


async def _do_snapshot_all_servers() -> None:
    """Actual snapshot logic — called only when no concurrent snapshot is running."""
    from db.writer import (
        upsert_server_index,
        write_index_snapshot,
        write_price_snapshot,
    )
    ts = datetime.now(timezone.utc)
    all_offers = get_all_offers()

    # ── 1. Group-level index (legacy OHLC path) ───────────────────────────────
    groups: dict[tuple[str, str], list[Offer]] = {}
    for o in all_offers:
        ds = o.display_server
        if not ds:
            continue
        groups.setdefault((ds, o.faction), []).append(o)
        groups.setdefault((ds, "All"), []).append(o)

    index_tasks = []
    for (server, faction), offers in groups.items():
        idx = compute_index_price(offers)
        if idx is not None:
            _index_cache[f"{server}::{faction}"] = idx
            index_tasks.append(write_index_snapshot(server, faction, idx, ts))
    if index_tasks:
        await asyncio.gather(*index_tasks, return_exceptions=True)

    # ── 2. Per-server index (Task 4) ──────────────────────────────────────────
    # Collect unique (server_id, faction) pairs from offers that have server_id
    server_faction_pairs: set[tuple[int, str]] = set()
    for o in all_offers:
        if o.server_id is not None:
            server_faction_pairs.add((o.server_id, o.faction))
            server_faction_pairs.add((o.server_id, "All"))

    server_index_tasks = []
    for (server_id, faction) in server_faction_pairs:
        result = compute_server_index(server_id, faction, all_offers)
        if result is not None:
            server_index_tasks.append(
                upsert_server_index(
                    server_id=server_id,
                    faction=faction,
                    index_price=result["index_price"],
                    sample_size=result["sample_size"],
                    min_price=result["min_price"],
                    max_price=result["max_price"],
                    computed_at=ts,
                )
            )
    if server_index_tasks:
        await asyncio.gather(*server_index_tasks, return_exceptions=True)

    # ── 3. Raw price snapshots (Task 1) ───────────────────────────────────────
    # Only write if the offer's raw_price changed > 0.5% since last write.
    # This prevents ~500k rows/day from writing every offer on every cycle.
    snap_tasks = []
    for o in all_offers:
        last = _last_snap_price.get(o.id)
        if last is not None and last != 0:
            if abs(o.raw_price - last) / last <= _SNAP_WRITE_THRESHOLD:
                continue  # price unchanged within threshold — skip DB write
        _last_snap_price[o.id] = o.raw_price
        snap_tasks.append(
            write_price_snapshot(
                source=o.source,
                offer_id=o.id,
                server_id=o.server_id,
                faction=o.faction,
                raw_price=o.raw_price,
                raw_price_unit=o.raw_price_unit,
                lot_size=o.lot_size,
                seller=o.seller,
                offer_url=o.offer_url,
                fetched_at=o.fetched_at,
            )
        )
    if snap_tasks:
        # Process in batches to avoid overwhelming the DB connection pool
        batch_size = 50
        for i in range(0, len(snap_tasks), batch_size):
            await asyncio.gather(*snap_tasks[i:i + batch_size], return_exceptions=True)


# ── Server resolver integration ───────────────────────────────────────────────

def _collect_resolve_keys(offers: list[Offer]) -> list[tuple[str, str]]:
    """Build (alias, source) pairs for batch alias lookup (deduped order preserved)."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for offer in offers:
        if offer.server_id is not None:
            continue
        if offer.source == "g2g" and offer.server_name:
            m = re.match(
                r"^\((?P<region>[A-Z]{2,})\)\s*(?P<version>.+)$",
                offer.display_server or "",
            )
            if m:
                region = m.group("region")
                version = m.group("version")
                for faction in (offer.faction, "Alliance", "Horde"):
                    raw_key = f"{offer.server_name} [{region} - {version}] - {faction}"
                    t = (raw_key, offer.source)
                    if t not in seen:
                        seen.add(t)
                        out.append(t)
        elif offer.source == "funpay" and offer.server_name:
            m = re.match(
                r"^\((?P<region>[A-Z]{2,})\)\s*(?P<version>.+)$",
                offer.display_server or "",
            )
            if m:
                region = m.group("region")
                version = m.group("version")
                raw_key = f"({region}) {version} - {offer.server_name}"
                t = (raw_key, offer.source)
                if t not in seen:
                    seen.add(t)
                    out.append(t)
    return out


async def _resolve_server_ids(offers: list[Offer]) -> list[Offer]:
    """
    For each offer that doesn't have server_id set, try to resolve via
    server_resolver. Updates offer.server_id in-place.

    If DB pool is unavailable, returns offers unchanged (graceful degradation).
    """
    from db.writer import get_pool
    pool = await get_pool()
    if pool is None:
        return offers

    from db.server_resolver import resolve_server, resolve_server_batch

    batch_keys = _collect_resolve_keys(offers)
    alias_map = await resolve_server_batch(pool, batch_keys)

    for offer in offers:
        if offer.server_id is not None:
            continue
        if offer.source == "g2g" and offer.server_name:
            m = re.match(
                r"^\((?P<region>[A-Z]{2,})\)\s*(?P<version>.+)$",
                offer.display_server or "",
            )
            if m:
                region = m.group("region")
                version = m.group("version")
                for faction in (offer.faction, "Alliance", "Horde"):
                    raw_key = f"{offer.server_name} [{region} - {version}] - {faction}"
                    lk = raw_key.lower().strip()
                    sid = alias_map.get(lk)
                    if sid is None:
                        sid = await resolve_server(raw_key, offer.source, pool)
                    if sid is not None:
                        offer.server_id = sid
                        break
        elif offer.source == "funpay":
            if offer.server_name:
                m = re.match(
                    r"^\((?P<region>[A-Z]{2,})\)\s*(?P<version>.+)$",
                    offer.display_server or "",
                )
                if m:
                    region = m.group("region")
                    version = m.group("version")
                    raw_key = f"({region}) {version} - {offer.server_name}"
                    lk = raw_key.lower().strip()
                    sid = alias_map.get(lk)
                    if sid is None:
                        sid = await resolve_server(raw_key, offer.source, pool)
                    if sid is not None:
                        offer.server_id = sid

    return offers


# ── Background loops ──────────────────────────────────────────────────────────

async def _run_funpay_loop() -> None:
    from parser.funpay_parser import fetch_offers as fp_fetch

    while True:
        _running["funpay"] = True
        try:
            offers = await fp_fetch()
            if offers:
                offers = [_normalize_funpay_offer(o) for o in offers]
                offers = await _resolve_server_ids(offers)
                _cache["funpay"] = offers
                _cache_initialized["funpay"] = True
                _cache_version["funpay"] += 1
                _last_update["funpay"] = datetime.now(timezone.utc)
                _last_error["funpay"] = None
                logger.info("FunPay updated: %d offers", len(offers))
                asyncio.create_task(_snapshot_all_servers())
            elif _cache_initialized["funpay"]:
                _last_error["funpay"] = "empty_result"
                logger.warning(
                    "funpay returned 0 offers — keeping %d cached",
                    len(_cache["funpay"]),
                )
            else:
                _last_error["funpay"] = "empty_cold_start"
                logger.warning(
                    "funpay returned 0 offers on cold start — cache remains empty",
                )
        except Exception as e:
            _last_error["funpay"] = type(e).__name__
            logger.exception("FunPay parser failed")
        finally:
            _running["funpay"] = False

        delay = random.uniform(50, 70)
        logger.debug("FunPay next update in %.1fs", delay)
        await asyncio.sleep(delay)


async def _run_g2g_loop() -> None:
    from parser.g2g_parser import fetch_offers as g2g_fetch

    while True:
        _running["g2g"] = True
        t0 = asyncio.get_running_loop().time()   # get_event_loop() is deprecated in 3.10+
        try:
            offers = await g2g_fetch()
            if offers:
                offers = [_normalize_g2g_offer(o) for o in offers]
                offers = await _resolve_server_ids(offers)
                _cache["g2g"] = offers
                _cache_initialized["g2g"] = True
                _cache_version["g2g"] += 1
                _last_update["g2g"] = datetime.now(timezone.utc)
                _last_error["g2g"] = None
                elapsed = asyncio.get_running_loop().time() - t0
                logger.info("G2G updated: %d offers in %.1fs", len(offers), elapsed)
                asyncio.create_task(_snapshot_all_servers())
            elif _cache_initialized["g2g"]:
                _last_error["g2g"] = "empty_result"
                logger.warning(
                    "g2g returned 0 offers — keeping %d cached",
                    len(_cache["g2g"]),
                )
            else:
                _last_error["g2g"] = "empty_cold_start"
                logger.warning(
                    "g2g returned 0 offers on cold start — cache remains empty",
                )
        except Exception as e:
            _last_error["g2g"] = type(e).__name__
            logger.exception("G2G parser failed")
        finally:
            _running["g2g"] = False
        await asyncio.sleep(G2G_INTERVAL)


async def start_background_parsers() -> None:
    """Start background FunPay and G2G loops. Call once in lifespan."""
    asyncio.create_task(_run_funpay_loop())
    asyncio.create_task(_run_g2g_loop())
    logger.info("Background parsers started (funpay + g2g)")


# ── Public read API ───────────────────────────────────────────────────────────

def get_meta() -> Optional[datetime]:
    updates = [t for t in _last_update.values() if t is not None]
    return max(updates) if updates else None


def get_price_history(
    server: str = "all",
    faction: str = "all",
    last: int = 50,
) -> list[PriceHistoryPoint]:
    """In-memory price history snapshot — backward compat for /price-history."""
    offers = get_all_offers()

    if server != "all":
        offers = [o for o in offers if _clean(o.display_server) == _clean(server)]
    if faction != "all":
        offers = [o for o in offers if o.faction.lower() == faction.lower()]

    result = compute_index_price(offers)
    if result is None:
        return []

    return [
        PriceHistoryPoint(
            timestamp=datetime.now(timezone.utc),
            price=result.index_price,
            min=result.price_min,
            max=result.price_max,
            count=result.offer_count,
        )
    ]


def get_servers() -> list[ServerGroup]:
    """
    Hierarchical server group list for the sidebar.

    min_price = best_ask from _index_cache (realistic buy price).
    Falls back to simple min across offers if cache not yet populated.
    Sorted by: version (Anniversary=0 … Classic=3), then min_price ASC.
    """
    group_min_price: dict[str, float]    = {}
    group_realms:    dict[str, set[str]] = {}

    for offer in get_all_offers():
        ds = offer.display_server
        if not ds:
            continue
        group_realms.setdefault(ds, set())
        if offer.server_name:
            group_realms[ds].add(offer.server_name)
        cur = group_min_price.get(ds)
        if cur is None or offer.price_per_1k < cur:
            group_min_price[ds] = offer.price_per_1k

    # Override fallback with cached best_ask
    for ds in group_min_price:
        cached = (
            _index_cache.get(f"{ds}::All")
            or _index_cache.get(f"{ds}::Alliance")
            or _index_cache.get(f"{ds}::Horde")
        )
        if cached is not None:
            group_min_price[ds] = cached.best_ask

    sorted_groups = sorted(
        group_min_price,
        key=lambda s: (_version_rank(s), group_min_price[s]),
    )

    return [
        ServerGroup(
            display_server=ds,
            realms=sorted(group_realms.get(ds, set())),
            min_price=round(group_min_price[ds], 4),
        )
        for ds in sorted_groups
    ]


def get_offers(
    server: str | None = None,
    faction: str | None = None,
    sort_by: str = "price",
    server_name: str | None = None,
) -> list[Offer]:
    result = get_all_offers()

    if server:
        result = [o for o in result if _clean(o.display_server) == _clean(server)]

    if server_name:
        result = [
            o for o in result
            if _clean(o.server_name) == _clean(server_name)
        ]

    if faction:
        result = [o for o in result if o.faction.lower() == faction.lower()]

    if sort_by == "price":
        result.sort(key=lambda o: (o.price_per_1k, -o.amount_gold))
    else:
        result.sort(key=lambda o: (-o.amount_gold, o.price_per_1k))

    return result
