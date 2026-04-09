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
Task 5: normalize_pipeline handles validation, canonicalization, price validation,
        price-assisted rerouting, and deduplication (source, offer_id).

Normalization flow (per parse cycle):
  raw offers
    → [phase 0] _normalize_*_offer()   — display_server string cleanup
    → [phase 1] normalize_offer_batch() — validate / resolve / canonicalize /
                                           price-validate / dedup
    → cached in _cache[source]
    → quarantine items added to _quarantine ring buffer

Quarantine:
  Offers that fail validation (broken title, unknown faction, etc.) are stored
  in a bounded ring buffer (_quarantine) visible at GET /admin/quarantine.
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
from utils.version_utils import _VERSION_ALIASES, _canonicalize_version

# ── Hardcore display suffix ───────────────────────────────────────────────────
# canonical display_server for Hardcore realms: "(EU) Anniversary · Hardcore"
# This suffix is set by normalize_pipeline._apply_canonical() and used here
# only for _version_rank() ordering — no string manipulation needed in get_servers().
_HARDCORE_SUFFIX = " · Hardcore"

# ── Valid sidebar group key pattern ───────────────────────────────────────────
# display_server MUST start with "(REGION) " to be a canonical group key.
# Bare server names like "Spineshatter", "spineshatter", "Maladath" are NOT
# valid keys — they appear when _apply_canonical was not called (cold cache)
# or when a parser set display_server to the realm name instead of the group.
# This regex gates get_servers() to prevent polluting the sidebar.
_GROUP_RE = re.compile(r"^\([A-Z]{2,}\)\s+\S", re.ASCII)

logger = logging.getLogger(__name__)

# ── Per-source state ──────────────────────────────────────────────────────────
_cache:         dict[str, list[Offer]]        = {"funpay": [], "g2g": []}
_last_update:   dict[str, Optional[datetime]] = {"funpay": None, "g2g": None}
_running:       dict[str, bool]               = {"funpay": False, "g2g": False}
_cache_version: dict[str, int]               = {"funpay": 0, "g2g": 0}
_last_error:    dict[str, Optional[str]]      = {"funpay": None, "g2g": None}
_cache_initialized: dict[str, bool]          = {"funpay": False, "g2g": False}

# ── Quarantine ring buffer ────────────────────────────────────────────────────
# Offers that failed the normalize pipeline are logged here for admin inspection.
# Bounded to _QUARANTINE_MAX entries (oldest dropped first on overflow).
_quarantine:     list[dict] = []
_QUARANTINE_MAX: int        = 500

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

_VERSION_ORDER: dict[str, int] = {
    # Normal realms sorted by version
    "Anniversary":         0,
    "Season of Discovery": 1,
    "Classic Era":         2,
    "Classic":             3,
    # Hardcore variants come after their Normal counterpart
    # (e.g. "(EU) Anniversary · Hardcore" sorts after "(EU) Anniversary")
    "Anniversary · Hardcore":         10,
    "Season of Discovery · Hardcore": 11,
    "Classic Era · Hardcore":         12,
    "Classic · Hardcore":             13,
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


# ── Quarantine helpers ────────────────────────────────────────────────────────

def _add_to_quarantine(items: list) -> None:
    """
    Append QuarantinedOffer items to the ring buffer.
    Drops the oldest entries when the buffer exceeds _QUARANTINE_MAX.
    """
    if not items:
        return
    _quarantine.extend(
        {
            "raw_id":    q.raw_id,
            "source":    q.source,
            "reason":    q.reason,
            "raw_title": q.raw_title,
            "price":     q.price,
            "details":   q.details,
            "ts":        q.ts,
        }
        for q in items
    )
    overflow = len(_quarantine) - _QUARANTINE_MAX
    if overflow > 0:
        del _quarantine[:overflow]


def get_quarantine() -> list[dict]:
    """Return quarantine log (newest-first) for /admin/quarantine endpoint."""
    return list(reversed(_quarantine))


def _detect_version(text: str) -> str:
    t = _clean(text)
    if "season of discovery" in t or re.search(r"\bsod\b|\bseasonal\b", t):
        return "Season of Discovery"
    if "anniversary" in t:
        return "Anniversary"
    if "classic era" in t:
        return "Classic Era"
    if "hardcore" in t:
        return "Hardcore"
    return "Classic"


def _normalize_funpay_offer(offer: Offer) -> Offer:
    """Normalise FunPay offer: set display_server to '(REGION) Version' format.

    FunPay .tc-server text arrives as one of:
      "(EU) Anniversary - Spineshatter"  → group + realm
      "(EU) Anniversary"                 → group only
      "Spineshatter"                     → bare realm name (broken / OCE format)

    The bare-name case is explicitly cleared at the end so that
    _build_alias_key returns None and the offer is routed to quarantine
    (reason="unresolved_server") rather than creating a fake top-level
    sidebar group named after the server.
    """
    raw = (offer.display_server or "").strip()
    m = re.match(r"^\((?P<region>[A-Za-z]{2,})\)\s*(?P<body>.*)$", raw)
    if not m:
        # display_server has no "(REGION)" prefix — bare server name or unknown
        # format. Clear it so _build_alias_key returns None → quarantine.
        # The _GROUP_RE guard in get_servers() is the last line of defence, but
        # clearing here prevents the unresolved offer from reaching the cache.
        if offer.display_server:
            logger.debug(
                "FunPay: display_server=%r has no (REGION) prefix — clearing "
                "to prevent bare-name sidebar group",
                offer.display_server,
            )
            offer.display_server = ""
        return offer

    region  = m.group("region").upper()
    body    = (m.group("body") or "").strip()
    # Task 1: use _canonicalize_version (from _VERSION_ALIASES) so both
    # _normalize_funpay_offer and _normalize_g2g_offer produce identical
    # canonical version strings for the same logical server.
    version = _canonicalize_version(_detect_version(body))
    realm   = ""

    if " - " in body:
        left, right = body.rsplit(" - ", 1)
        realm   = right.strip()
        version = _canonicalize_version(_detect_version(left or body))
    else:
        realm = body.strip()

    offer.display_server = f"({region}) {version}"
    if realm:
        offer.server_name = realm

    # Guard: display_server must not equal server_name after normalisation.
    # If they're equal, something went wrong upstream (FunPay sent just a realm
    # name that happened to start with "(REGION)"). Clear display_server so the
    # offer quarantines rather than creating a single-realm group.
    if offer.display_server and offer.server_name:
        if offer.display_server == offer.server_name:
            logger.warning(
                "FunPay: display_server=%r equals server_name — clearing",
                offer.display_server,
            )
            offer.display_server = ""

    return offer


def _normalize_g2g_offer(offer: Offer) -> Offer:
    """Pre-normalize G2G offer display_server string before canonical pipeline.

    With the new parser design (g2g_parser._to_offer sets display_server=""),
    this function is effectively a no-op for current offers: the regex won't
    match an empty string. Canonical values (region, version, realm_type) are
    assigned exclusively by normalize_pipeline._apply_canonical() after DB lookup.

    Kept for backward compatibility with any legacy cached offers that may still
    carry a "(Region) Version" display_server from a previous parser version.

    Guard (model_validator fallback):
    Offer.model_validator sets display_server = server.lower() when display_server
    is empty. For G2G, server = server_name.lower() (e.g. "spineshatter"). If
    _apply_canonical is later skipped (cold server_data_cache), the offer reaches
    get_servers() with display_server = "spineshatter" — a bare realm name rather
    than a canonical group key like "(EU) Anniversary". Clearing it here ensures
    the offer is always either canonicalised by _apply_canonical or filtered by
    the _GROUP_RE guard in get_servers().
    """
    ds = offer.display_server or ""
    m = re.match(r"^\((?P<region>[A-Za-z]{2,})\)\s*(?P<version>.+)$", ds)
    if m:
        region  = m.group("region").upper()
        version = _canonicalize_version(m.group("version").strip())
        offer.display_server = f"({region}) {version}"
        offer.server         = offer.display_server.lower()
        return offer

    # Guard: if display_server equals the server_name (bare realm, from the
    # model_validator fallback "display_server = server.lower()"), clear it.
    # _apply_canonical will set the correct "(REGION) Version" group key.
    # Without this, cold-cache cycles leave offers with display_server="spineshatter"
    # that pollute the sidebar with single-realm top-level groups.
    if offer.display_server and offer.server_name:
        if offer.display_server.lower() == offer.server_name.lower():
            offer.display_server = ""

    return offer


def _version_rank(display_server: str) -> int:
    """Return sort rank for display_server string.

    Hardcore variants (containing " · Hardcore") are matched first because their
    key strings are longer and more specific (e.g. "Anniversary · Hardcore" before
    "Anniversary"). We sort the keys longest-first to ensure correct precedence.
    """
    ds = display_server.strip().lower()
    for ver in sorted(_VERSION_ORDER, key=len, reverse=True):
        if ver.lower() in ds:
            return _VERSION_ORDER[ver]
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
    Task 4: Top-pick price index — one best offer per (source, faction) pair.

    Algorithm:
      1. Collect the cheapest offer per (source, faction) pair — up to 4 picks:
           funpay/Alliance, funpay/Horde, g2g/Alliance, g2g/Horde.
      2. index_price = simple mean of top-pick price_per_1k values.
      3. best_ask    = minimum price among top picks.
      4. offer_count = number of top picks found (1–4).

    Returns None if fewer than 2 raw offers are supplied, or if after building
    the top-pick map fewer than 2 distinct (source, faction) pairs exist
    (thin market — single noisy offer must not pollute the index).
    Signature and return type (IndexPrice dataclass) are unchanged.
    """
    if not offers or len(offers) < 2:
        return None

    # Find cheapest offer per (source, faction) pair
    top_pick_map: dict[tuple[str, str], Offer] = {}
    for o in offers:
        if o.price_per_1k <= 0:
            continue
        key = (o.source.lower(), o.faction.lower())
        if key not in top_pick_map or o.price_per_1k < top_pick_map[key].price_per_1k:
            top_pick_map[key] = o

    # Require at least 2 distinct (source, faction) pairs — thin market returns None
    if len(top_pick_map) < 2:
        return None

    top_picks = list(top_pick_map.values())
    prices    = [o.price_per_1k for o in top_picks]
    index_price = sum(prices) / len(prices)
    best_ask    = min(prices)

    return IndexPrice(
        index_price  = round(index_price, 6),
        vwap         = round(index_price, 6),   # same as index in top-pick model
        best_ask     = round(best_ask, 6),
        price_min    = round(min(prices), 6),
        price_max    = round(max(prices), 6),
        offer_count  = len(top_picks),
        total_volume = sum(o.amount_gold for o in top_picks),
        sources      = sorted({o.source for o in top_picks}),
    )


# ── Task 4: per-server index computation ──────────────────────────────────────

def compute_server_index(
    server_id: int,
    faction: str,
    offers: list[Offer],
) -> dict | None:
    """
    Compute price index for a specific server_id + faction.

    Algorithm (updated Task 4 — top-pick-per-source):
      1. Filter offers to same server_id + faction.
      2. Find cheapest offer per source (funpay, g2g) — mirrors the
         top-pick selection in compute_index_price and buildDisplayList.
         Sorting by price_per_1k (normalised) is correct for both sources:
           G2G  (per_unit):  price_per_1k = raw_price * 1000
           FunPay (per_lot): price_per_1k = (raw_price / lot_size) * 1000
      3. Return mean of top-pick prices as index_price (per-unit, per 1 gold).

    Returns dict with index_price (per unit), min, max, sample_size.
    Returns None if no valid offers exist.
    """
    matching = [
        o for o in offers
        if o.server_id == server_id
        and (faction == "All" or o.faction.lower() == faction.lower())
        and o.price_per_1k > 0
    ]

    if not matching:
        return None

    # Find cheapest offer per source — top-pick-per-source logic
    top_pick_map: dict[str, Offer] = {}
    for o in matching:
        src = o.source.lower()
        if src not in top_pick_map or o.price_per_1k < top_pick_map[src].price_per_1k:
            top_pick_map[src] = o

    top_picks     = list(top_pick_map.values())
    prices_per_1k = [o.price_per_1k for o in top_picks]
    mean_per_1k   = sum(prices_per_1k) / len(prices_per_1k)

    return {
        "index_price": round(mean_per_1k / 1000.0, 8),   # per unit (per 1 gold)
        "sample_size": len(top_picks),
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
    from service.price_profiles import update_profiles

    ts = datetime.now(timezone.utc)
    all_offers = get_all_offers()

    # Refresh price profiles from latest offer cache so normalize_pipeline
    # can use them for price validation on the next parse cycle.
    update_profiles(all_offers)

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
            m = re.match(r"^\((?P<region>[A-Za-z]{2,})\)\s*(?P<version>.+)$", server)
            if m:
                region = m.group("region").upper()
                version = m.group("version").strip()
                realm_names = sorted({o.server_name for o in offers if o.server_name})
                for realm in realm_names:
                    _index_cache[f"{realm}::{region}::{version}::{faction}"] = idx
            index_tasks.append(write_index_snapshot(server, faction, idx, ts))
    if index_tasks:
        await asyncio.gather(*index_tasks, return_exceptions=True)

    # ── 2. Per-server index (Task 4) ──────────────────────────────────────────
    # Collect unique (server_id, faction) pairs AND metadata for cache key:
    #   "server_name::region::version::faction"
    # region+version are extracted from display_server "(EU) Anniversary" format.

    # Build: server_id → (server_name, region, version) from offers
    _server_id_meta: dict[int, tuple[str, str, str]] = {}
    for o in all_offers:
        if o.server_id is not None and o.server_id not in _server_id_meta:
            ds = o.display_server or ""   # "(EU) Anniversary"
            import re as _re
            _ds_match = _re.match(r"^\(([A-Za-z]{2,})\)\s*(.+)$", ds)
            if _ds_match:
                _region  = _ds_match.group(1).upper()
                _version = _ds_match.group(2).strip()
            else:
                _region, _version = "", ds
            _server_id_meta[o.server_id] = (o.server_name or "", _region, _version)

    server_faction_pairs: set[tuple[int, str]] = set()
    for o in all_offers:
        if o.server_id is not None:
            server_faction_pairs.add((o.server_id, o.faction))
            server_faction_pairs.add((o.server_id, "All"))

    server_index_tasks = []
    for (server_id, faction) in server_faction_pairs:
        result = compute_server_index(server_id, faction, all_offers)
        if result is not None:
            # Populate per-server _index_cache key (Task 4)
            meta = _server_id_meta.get(server_id)
            if meta:
                srv_name, region, version = meta
                if srv_name:
                    cache_key = f"{srv_name}::{region}::{version}::{faction}"
                    _index_cache[cache_key] = IndexPrice(
                        index_price  = result["index_price"] * 1000,  # convert to per_1k
                        vwap         = result["index_price"] * 1000,
                        best_ask     = result["min_price"]  * 1000,
                        price_min    = result["min_price"]  * 1000,
                        price_max    = result["max_price"]  * 1000,
                        offer_count  = result["sample_size"],
                        total_volume = 0,
                        sources      = [],
                    )

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


# ── (Server resolver integration moved to service/normalize_pipeline.py) ──────
# _resolve_server_ids() and _collect_resolve_keys() are superseded by
# normalize_offer_batch() which handles resolution, canonicalization,
# price validation and deduplication in one deterministic pipeline.


# ── Background loops ──────────────────────────────────────────────────────────

async def _run_funpay_loop() -> None:
    from parser.funpay_parser import fetch_offers as fp_fetch
    from service.normalize_pipeline import normalize_offer_batch

    while True:
        _running["funpay"] = True
        try:
            raw_offers = await fp_fetch()
            if raw_offers:
                # Phase 0: normalize display_server string format
                raw_offers = [_normalize_funpay_offer(o) for o in raw_offers]
                # Task 1: log sample display_server values after Phase 0 to
                # verify FunPay strings match G2G canonical values.
                _fp_samples = [o.display_server for o in raw_offers if o.display_server][:5]
                logger.debug("FunPay Phase0 display_server samples: %s", _fp_samples)

                # Phase 1: full normalization pipeline
                from db.writer import get_pool
                pool = await get_pool()
                offers, quarantined = await normalize_offer_batch(raw_offers, pool)

                _add_to_quarantine(quarantined)
                if quarantined:
                    logger.info(
                        "FunPay quarantined %d offers (reasons: %s)",
                        len(quarantined),
                        ", ".join({q.reason for q in quarantined}),
                    )

                # Cache protection: don't overwrite a healthy cache with empty
                # results (e.g. all offers quarantined because resolver is down).
                if not offers and _cache_initialized["funpay"]:
                    _last_error["funpay"] = "empty_after_normalize"
                    logger.warning(
                        "FunPay normalize returned 0 offers (%d quarantined) — "
                        "keeping %d cached offers",
                        len(quarantined), len(_cache["funpay"]),
                    )
                else:
                    _cache["funpay"] = offers
                    _cache_initialized["funpay"] = True
                    _cache_version["funpay"] += 1
                    _last_update["funpay"] = datetime.now(timezone.utc)
                    _last_error["funpay"] = None
                    logger.info(
                        "FunPay updated: %d offers (%d quarantined)",
                        len(offers), len(quarantined),
                    )
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
    from service.normalize_pipeline import normalize_offer_batch

    while True:
        _running["g2g"] = True
        t0 = asyncio.get_running_loop().time()
        try:
            raw_offers = await g2g_fetch()
            if raw_offers:
                # Phase 0: normalize display_server string format
                raw_offers = [_normalize_g2g_offer(o) for o in raw_offers]
                # Task 1: log sample display_server values after Phase 0 to
                # verify G2G strings (after _apply_canonical in Phase 1) match FunPay.
                _g2g_samples = [o.display_server for o in raw_offers if o.display_server][:5]
                logger.debug("G2G Phase0 display_server samples: %s", _g2g_samples)

                # Phase 1: full normalization pipeline
                from db.writer import get_pool
                pool = await get_pool()
                offers, quarantined = await normalize_offer_batch(raw_offers, pool)
                # TODO(I3): include seller_count from parser once g2g_fetch returns meta.

                _add_to_quarantine(quarantined)
                if quarantined:
                    logger.info(
                        "G2G quarantined %d offers (reasons: %s)",
                        len(quarantined),
                        ", ".join({q.reason for q in quarantined}),
                    )

                # Cache protection: don't overwrite a healthy cache with empty
                # results (e.g. all offers quarantined because resolver is down).
                if not offers and _cache_initialized["g2g"]:
                    _last_error["g2g"] = "empty_after_normalize"
                    elapsed = asyncio.get_running_loop().time() - t0
                    logger.warning(
                        "G2G normalize returned 0 offers (%d quarantined) in %.1fs — "
                        "keeping %d cached offers",
                        len(quarantined), elapsed, len(_cache["g2g"]),
                    )
                else:
                    _cache["g2g"] = offers
                    _cache_initialized["g2g"] = True
                    _cache_version["g2g"] += 1
                    _last_update["g2g"] = datetime.now(timezone.utc)
                    _last_error["g2g"] = None
                    elapsed = asyncio.get_running_loop().time() - t0
                    logger.info(
                        "G2G updated: %d offers (%d quarantined) in %.1fs",
                        len(offers), len(quarantined), elapsed,
                    )
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

    Grouping: by display_server (set by normalize_pipeline._apply_canonical()).
      Normal realms:   "(EU) Anniversary"
      Hardcore realms: "(EU) Anniversary · Hardcore"  ← separate group

    min_price = best_ask from _index_cache (realistic buy price).
    Falls back to simple min across offers if cache not yet populated.

    Sorting: version rank (Anniversary first) then min_price ASC.
    Hardcore groups appear after their Normal counterparts (see _VERSION_ORDER).

    Note: Penance and Shadowstrike region correction (EU→AU) is now handled
    at normalization time by _apply_canonical() — no post-hoc filtering needed.
    """
    group_min_price: dict[str, float]    = {}
    group_realms:    dict[str, set[str]] = {}

    for offer in get_all_offers():
        ds = offer.display_server
        # Guard: only accept canonical group keys in "(REGION) Version" format.
        # Bare server names (e.g. "spineshatter", "Maladath") reach here when
        # _apply_canonical was skipped on a cold cache cycle. Filtering them here
        # prevents single-realm top-level groups from polluting the sidebar.
        if not ds or not _GROUP_RE.match(ds) or offer.price_per_1k <= 0:
            continue
        group_realms.setdefault(ds, set())
        if offer.server_name:
            group_realms[ds].add(offer.server_name)
        cur = group_min_price.get(ds)
        if cur is None or offer.price_per_1k < cur:
            group_min_price[ds] = offer.price_per_1k

    # Override fallback min_price with cached best_ask (more reliable)
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
