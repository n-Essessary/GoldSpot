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
Task 5: normalize_pipeline handles validation, canonicalization, and
        deduplication (source, offer_id).

Normalization flow (per parse cycle):
  raw offers
    → [phase 0] _normalize_*_offer()   — display_server string cleanup
    → [phase 1] normalize_offer_batch() — validate / resolve / canonicalize /
                                           dedup
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

from cachetools import TTLCache

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
# PA has two cycles (classic + retail) with separate intervals, plus combined
# sub-caches that get merged into _cache["playerauctions"] for the public read
# path — same pattern as g2g_retail_low / g2g_retail_rec.
_cache:         dict[str, list[Offer]]        = {
    "funpay": [], "g2g": [],
    "g2g_retail": [], "g2g_retail_low": [], "g2g_retail_rec": [],
    "playerauctions": [], "playerauctions_classic": [], "playerauctions_retail": [],
}
_last_update:   dict[str, Optional[datetime]] = {
    "funpay": None, "g2g": None, "g2g_retail": None,
    "playerauctions": None,
}
_running:       dict[str, bool]               = {
    "funpay": False, "g2g": False, "g2g_retail": False,
    "playerauctions_classic": False, "playerauctions_retail": False,
}
_cache_version: dict[str, int]               = {
    "funpay": 0, "g2g": 0, "g2g_retail": 0,
    "playerauctions": 0,
}
_last_error:    dict[str, Optional[str]]      = {
    "funpay": None, "g2g": None, "g2g_retail": None,
    "playerauctions": None,
}
_cache_initialized: dict[str, bool]          = {
    "funpay": False, "g2g": False, "g2g_retail": False,
    "playerauctions": False,
}

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
_MIN_OFFERS          = 1

# Throttle for raw price snapshots: skip write if price changed less than this
_SNAP_WRITE_THRESHOLD = 0.005   # 0.5%
# Per-offer last-written price: offer_id → last raw_price written to DB
# TTLCache: evicts entries after 1h — prevents unbounded growth if offer_ids
# rotate (e.g. sellers re-list at new IDs).
_last_snap_price: TTLCache = TTLCache(maxsize=10_000, ttl=3600)

_VERSION_ORDER: dict[str, int] = {
    "MoP Classic":                    0,
    "Retail":                         5,
    "Anniversary":                   10,
    "Season of Discovery":           20,
    "Classic":                       40,
    "Anniversary · Hardcore":       100,
    "Season of Discovery · Hardcore": 110,
    "Classic · Hardcore":           130,
}


# ── Retail cache helpers ──────────────────────────────────────────────────────

def _merge_retail_caches() -> list[Offer]:
    """Combine g2g_retail_low + g2g_retail_rec with dedup by offer_id.
    Each sub-cache is fully replaced per cycle — this merge never accumulates
    stale offers across cycles.
    """
    seen: set[str] = set()
    result: list[Offer] = []
    for o in _cache["g2g_retail_low"] + _cache["g2g_retail_rec"]:
        if o.id not in seen:
            seen.add(o.id)
            result.append(o)
    return result


def _merge_pa_caches() -> list[Offer]:
    """Combine PA classic + retail sub-caches with dedup by offer_id.
    Same shape as _merge_retail_caches: each sub-cache is fully replaced per
    cycle, never accumulating stale offers.
    """
    seen: set[str] = set()
    result: list[Offer] = []
    for o in _cache["playerauctions_classic"] + _cache["playerauctions_retail"]:
        if o.id not in seen:
            seen.add(o.id)
            result.append(o)
    return result


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
# TTLCache: evicts entries after 1h — prevents unbounded growth across restarts
# when display_server keys change (server retires, version rename, etc.).
_index_cache: TTLCache = TTLCache(maxsize=10_000, ttl=3600)


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
    if "mop classic" in t or "mists of pandaria" in t:
        return "MoP Classic"
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


def _normalize_pa_offer(offer: Offer) -> Offer:
    """Pre-normalize a PlayerAuctions offer's display_server before Phase 1.

    PA's parser leaves display_server="" and stashes the source region+version
    in raw_title as "(REGION) Version - ServerName - Faction". Here we extract
    the region and version, canonicalize the version string via _VERSION_ALIASES,
    and set display_server="(REGION) Version" so _build_alias_key (FunPay-style
    branch) can construct a stable lookup key.

    The canonical pipeline (_apply_canonical) overwrites display_server again
    once the alias resolves to a server_id, picking the canonical region,
    version, and any "· Hardcore" suffix from the registry.
    """
    raw = (offer.raw_title or "").strip()
    if not raw:
        return offer

    # raw_title shape from PA parser: "(REGION) Version - ServerName - Faction"
    m = re.match(
        r"^\((?P<region>[A-Za-z]{2,})\)\s*(?P<rest>.+)$",
        raw,
    )
    if not m:
        return offer

    region = m.group("region").upper()
    body   = m.group("rest").strip()

    # Body is "Version - ServerName - Faction". Strip the trailing
    # " - Faction" first (faction is the very last segment), then the
    # remaining " - ServerName" leaves just the version.
    if " - " in body:
        version_and_server, _trailing_faction = body.rsplit(" - ", 1)
    else:
        version_and_server = body

    if " - " in version_and_server:
        version_raw, _server = version_and_server.split(" - ", 1)
    else:
        version_raw = version_and_server

    version = _canonicalize_version(version_raw.strip())
    offer.display_server = f"({region}) {version}"
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


def _game_version_from_display(display_server: str) -> str:
    """Extract game_version from display_server string.
    "(EU) MoP Classic"         → "MoP Classic"
    "(EU) Anniversary"         → "Classic Era"
    "(EU) Season of Discovery" → "Classic Era"
    "(EU) Classic"             → "Classic Era"
    "(EU) Anniversary · Hardcore" → "Classic Era"
    """
    ds = display_server.strip()
    # Strip "(REGION) " prefix
    m = re.match(r"^\([A-Za-z]{2,}\)\s*(.+)$", ds)
    body = m.group(1).strip() if m else ds
    # Strip Hardcore suffix
    body = body.replace(" · Hardcore", "").strip()
    if body == "MoP Classic":
        return "MoP Classic"
    if body == "Retail":
        return "Retail"
    return "Classic Era"


# ── Public cache read ─────────────────────────────────────────────────────────

def get_all_offers() -> list[Offer]:
    return (
        _cache["funpay"]
        + _cache["g2g"]
        + _cache["g2g_retail"]
        + _cache["playerauctions"]
    )


def get_parser_status() -> dict:
    # `running` for PA aggregates the two sub-loops: True if either is active.
    return {
        src: {
            "offers":      len(_cache[src]),
            "last_update": _last_update[src].isoformat() if _last_update[src] else None,
            "running": (
                _running.get(src, False) if src != "playerauctions"
                else (
                    _running.get("playerauctions_classic", False)
                    or _running.get("playerauctions_retail", False)
                )
            ),
            "version":     _cache_version[src],
            "last_error":  _last_error[src],
        }
        for src in ("funpay", "g2g", "g2g_retail", "playerauctions")
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
    matching = [
        o for o in offers
        if o.server_id == server_id
        and (faction == "All" or o.faction.lower() == faction.lower())
        and o.price_per_1k > 0
    ]
    if len(matching) < _MIN_OFFERS:
        return None

    # Top-1 per (source, faction) pair
    by_source: dict[str, list[Offer]] = {}
    for o in matching:
        by_source.setdefault(o.source, []).append(o)

    top_map: dict[tuple[str, str], Offer] = {}
    for o in matching:
        key = (o.source, o.faction)
        if key not in top_map or o.price_per_1k < top_map[key].price_per_1k:
            top_map[key] = o
    top = list(top_map.values())

    if len(top) < _MIN_OFFERS:
        return None

    top.sort(key=lambda o: o.price_per_1k)

    # Compute average price per platform (mean of one offer per (source, faction) per source)
    platform_avgs: dict[str, float] = {}
    for src in by_source:
        src_top = [o for o in top if o.source == src]
        if src_top:
            platform_avgs[src] = sum(o.price_per_1k for o in src_top) / len(src_top)

    # Exclude platforms more than 2x more expensive than the cheapest platform avg
    min_avg = min(platform_avgs.values())
    allowed = {src for src, avg in platform_avgs.items() if avg <= min_avg * 2.0}
    top = [o for o in top if o.source in allowed]

    if len(top) < _MIN_OFFERS:
        return None

    prices = [o.price_per_1k for o in top]
    mean_per_1k = sum(prices) / len(prices)

    # best_ask = cheapest single offer across all platforms
    best_ask_per_1k = prices[0]

    return {
        "index_price": round(mean_per_1k / 1000.0, 8),      # per unit
        "best_ask":    round(best_ask_per_1k / 1000.0, 8),  # per unit
        "sample_size": len(top),
        "min_price":   round(min(prices) / 1000.0, 8),
        "max_price":   round(max(prices) / 1000.0, 8),
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

    # ── 2. Per-server index (current value table + _index_cache) ─────────────
    # History writes (server_price_history / server_price_history_short) were
    # removed in migration 012 — the tiered snapshot loop now handles those via
    # snapshots_1m / 5m / 1h / 1d.  We still:
    #   a) populate _index_cache for the /index/{server} endpoint
    #   b) upsert server_price_index for the /price-index endpoint

    # Build: server_id → (server_name, region, version) from offers
    # Also pre-group offers by server_id (Fix 1) to avoid O(N_servers × N_offers).
    _server_id_meta: dict[int, tuple[str, str, str]] = {}
    by_sid: dict[int, list] = {}
    server_faction_pairs: set[tuple[int, str]] = set()
    for o in all_offers:
        if o.server_id is not None:
            # Pre-group
            if o.server_id not in by_sid:
                by_sid[o.server_id] = []
            by_sid[o.server_id].append(o)
            server_faction_pairs.add((o.server_id, o.faction))
            server_faction_pairs.add((o.server_id, "All"))
            # Meta
            if o.server_id not in _server_id_meta:
                ds = o.display_server or ""
                import re as _re
                _ds_match = _re.match(r"^\(([A-Za-z]{2,})\)\s*(.+)$", ds)
                if _ds_match:
                    _region  = _ds_match.group(1).upper()
                    _version = _ds_match.group(2).strip()
                else:
                    _region, _version = "", ds
                _server_id_meta[o.server_id] = (o.server_name or "", _region, _version)

    server_index_tasks = []
    for (server_id, faction) in server_faction_pairs:
        result = compute_server_index(server_id, faction, by_sid.get(server_id, []))
        if result is not None:
            # Populate per-server _index_cache key (used by /index/{server})
            meta = _server_id_meta.get(server_id)
            if meta:
                srv_name, region, version = meta
                if srv_name:
                    cache_key = f"{srv_name}::{region}::{version}::{faction}"
                    _index_cache[cache_key] = IndexPrice(
                        index_price  = result["index_price"] * 1000,  # convert to per_1k
                        vwap         = result["index_price"] * 1000,
                        best_ask     = result["best_ask"] * 1000,
                        price_min    = result["min_price"]  * 1000,
                        price_max    = result["max_price"]  * 1000,
                        offer_count  = result["sample_size"],
                        total_volume = 0,
                        sources      = [],
                    )

            # Upsert server_price_index (current value — no history writes)
            server_index_tasks.append(
                upsert_server_index(
                    server_id=server_id,
                    faction=faction,
                    index_price=result["index_price"],
                    best_ask=result["best_ask"],
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


async def _run_g2g_retail_loop() -> None:
    """G2G Retail lowest_price loop. Runs every ~60s (interval after parse).
    Fetches ~1028 groups / Semaphore(30). Expected cycle time: ~35-50s.
    Results merged into _cache["g2g_retail"] alongside recommended_v2 results.
    """
    from parser.g2g_parser import fetch_retail_offers
    from service.normalize_pipeline import normalize_offer_batch

    while True:
        _running["g2g_retail"] = True
        t0 = asyncio.get_running_loop().time()
        try:
            raw_offers = await fetch_retail_offers(
                sort="lowest_price",
                semaphore_limit=30,
            )
            if raw_offers:
                raw_offers = [_normalize_g2g_offer(o) for o in raw_offers]
                from db.writer import get_pool
                pool = await get_pool()
                offers, quarantined = await normalize_offer_batch(raw_offers, pool)
                _add_to_quarantine(quarantined)
                if quarantined:
                    logger.info(
                        "G2G Retail (lowest) quarantined %d offers (reasons: %s)",
                        len(quarantined),
                        ", ".join({q.reason for q in quarantined}),
                    )
                # Full replace of lowest_price sub-cache; combined cache rebuilt via merge helper.
                # Never accumulates stale offers across cycles.
                if not offers and _cache_initialized["g2g_retail"]:
                    _last_error["g2g_retail"] = "empty_after_normalize"
                    elapsed = asyncio.get_running_loop().time() - t0
                    logger.warning(
                        "G2G Retail (lowest) normalize returned 0 offers in %.1fs "
                        "— keeping %d cached",
                        elapsed, len(_cache["g2g_retail"]),
                    )
                else:
                    _cache["g2g_retail_low"] = offers
                    merged = _merge_retail_caches()
                    _cache["g2g_retail"] = merged
                    _cache_initialized["g2g_retail"] = True
                    _cache_version["g2g_retail"] += 1
                    _last_update["g2g_retail"] = datetime.now(timezone.utc)
                    _last_error["g2g_retail"] = None
                    elapsed = asyncio.get_running_loop().time() - t0
                    logger.info(
                        "G2G Retail (lowest) updated: %d offers (%d quarantined) "
                        "in %.1fs",
                        len(merged), len(quarantined), elapsed,
                    )
                    asyncio.create_task(_snapshot_all_servers())
            elif _cache_initialized["g2g_retail"]:
                _last_error["g2g_retail"] = "empty_result"
                logger.warning(
                    "G2G Retail (lowest) returned 0 — keeping %d cached",
                    len(_cache["g2g_retail"]),
                )
            else:
                _last_error["g2g_retail"] = "empty_cold_start"
                logger.warning("G2G Retail (lowest) returned 0 on cold start")
        except Exception as e:
            _last_error["g2g_retail"] = type(e).__name__
            logger.exception("G2G Retail (lowest) parser failed")
        finally:
            _running["g2g_retail"] = False
        await asyncio.sleep(60)


async def _run_g2g_retail_rec_loop() -> None:
    """G2G Retail recommended_v2 loop. Low-priority, runs every 180-300s.
    Startup delay of 90s to avoid overlapping with the first lowest_price cycle.
    Semaphore(20) — lower concurrency since this is background/low-priority.
    Results merged into _cache["g2g_retail"] alongside lowest_price results.
    """
    from parser.g2g_parser import fetch_retail_offers
    from service.normalize_pipeline import normalize_offer_batch

    await asyncio.sleep(90)  # startup delay
    while True:
        try:
            raw_offers = await fetch_retail_offers(
                sort="recommended_v2",
                semaphore_limit=20,
            )
            if raw_offers:
                raw_offers = [_normalize_g2g_offer(o) for o in raw_offers]
                from db.writer import get_pool
                pool = await get_pool()
                offers, quarantined = await normalize_offer_batch(raw_offers, pool)
                _add_to_quarantine(quarantined)
                # Full replace of recommended_v2 sub-cache; combined cache rebuilt via merge helper.
                # Never accumulates stale offers across cycles.
                if offers:
                    _cache["g2g_retail_rec"] = offers
                    merged = _merge_retail_caches()
                    _cache["g2g_retail"] = merged
                    _cache_version["g2g_retail"] += 1
                    _last_update["g2g_retail"] = datetime.now(timezone.utc)
                    logger.info(
                        "G2G Retail (rec) updated: %d offers (%d quarantined)",
                        len(merged), len(quarantined),
                    )
                else:
                    logger.warning("G2G Retail (rec) returned 0 after normalize")
        except Exception:
            logger.exception("G2G Retail (recommended) parser failed")
        interval = random.uniform(180, 300)
        logger.debug("G2G Retail (rec) next update in %.0fs", interval)
        await asyncio.sleep(interval)


async def _run_pa_classic_loop() -> None:
    """PlayerAuctions Classic + MoP loop. Runs every PA_CLASSIC_INTERVAL.
    Merges into _cache["playerauctions"] alongside the retail sub-cache.
    """
    from parser.playerauctions_parser import fetch_classic_offers
    from parser.playerauctions_parser import PA_SEMAPHORE
    from service.normalize_pipeline import normalize_offer_batch

    while True:
        _running["playerauctions_classic"] = True
        t0 = asyncio.get_running_loop().time()
        try:
            sem = asyncio.Semaphore(PA_SEMAPHORE)
            raw_offers = await fetch_classic_offers(None, sem)

            if raw_offers:
                raw_offers = [_normalize_pa_offer(o) for o in raw_offers]
                from db.writer import get_pool
                pool = await get_pool()
                offers, quarantined = await normalize_offer_batch(raw_offers, pool)
                _add_to_quarantine(quarantined)
                if quarantined:
                    logger.info(
                        "PA Classic quarantined %d offers (reasons: %s)",
                        len(quarantined),
                        ", ".join({q.reason for q in quarantined}),
                    )

                if not offers and _cache_initialized["playerauctions"]:
                    _last_error["playerauctions"] = "empty_after_normalize"
                    elapsed = asyncio.get_running_loop().time() - t0
                    logger.warning(
                        "PA Classic normalize returned 0 offers in %.1fs — "
                        "keeping %d cached",
                        elapsed, len(_cache["playerauctions"]),
                    )
                else:
                    _cache["playerauctions_classic"] = offers
                    merged = _merge_pa_caches()
                    _cache["playerauctions"] = merged
                    _cache_initialized["playerauctions"] = True
                    _cache_version["playerauctions"] += 1
                    _last_update["playerauctions"] = datetime.now(timezone.utc)
                    _last_error["playerauctions"] = None
                    elapsed = asyncio.get_running_loop().time() - t0
                    logger.info(
                        "PA Classic updated: %d offers (%d quarantined) in %.1fs",
                        len(merged), len(quarantined), elapsed,
                    )
                    asyncio.create_task(_snapshot_all_servers())
            elif _cache_initialized["playerauctions"]:
                _last_error["playerauctions"] = "empty_result"
                logger.warning(
                    "PA Classic returned 0 — keeping %d cached",
                    len(_cache["playerauctions"]),
                )
            else:
                _last_error["playerauctions"] = "empty_cold_start"
                logger.warning("PA Classic returned 0 on cold start")
        except Exception as e:
            _last_error["playerauctions"] = type(e).__name__
            logger.exception("PA Classic parser failed")
        finally:
            _running["playerauctions_classic"] = False
        await asyncio.sleep(1800)


async def _run_pa_retail_loop() -> None:
    """PlayerAuctions Retail loop. Runs every PA_RETAIL_INTERVAL.
    Startup delay of 60s avoids overlapping with the first classic cycle.
    Merges into _cache["playerauctions"] alongside the classic sub-cache.
    """
    from parser.playerauctions_parser import fetch_retail_offers
    from parser.playerauctions_parser import PA_SEMAPHORE
    from service.normalize_pipeline import normalize_offer_batch

    await asyncio.sleep(60)
    while True:
        _running["playerauctions_retail"] = True
        t0 = asyncio.get_running_loop().time()
        try:
            sem = asyncio.Semaphore(PA_SEMAPHORE)
            raw_offers = await fetch_retail_offers(None, sem)

            if raw_offers:
                raw_offers = [_normalize_pa_offer(o) for o in raw_offers]
                from db.writer import get_pool
                pool = await get_pool()
                offers, quarantined = await normalize_offer_batch(raw_offers, pool)
                _add_to_quarantine(quarantined)
                if quarantined:
                    logger.info(
                        "PA Retail quarantined %d offers (reasons: %s)",
                        len(quarantined),
                        ", ".join({q.reason for q in quarantined}),
                    )

                if not offers and _cache_initialized["playerauctions"]:
                    elapsed = asyncio.get_running_loop().time() - t0
                    logger.warning(
                        "PA Retail normalize returned 0 offers in %.1fs — "
                        "keeping %d cached",
                        elapsed, len(_cache["playerauctions"]),
                    )
                else:
                    _cache["playerauctions_retail"] = offers
                    merged = _merge_pa_caches()
                    _cache["playerauctions"] = merged
                    _cache_initialized["playerauctions"] = True
                    _cache_version["playerauctions"] += 1
                    _last_update["playerauctions"] = datetime.now(timezone.utc)
                    elapsed = asyncio.get_running_loop().time() - t0
                    logger.info(
                        "PA Retail updated: %d offers (%d quarantined) in %.1fs",
                        len(merged), len(quarantined), elapsed,
                    )
                    asyncio.create_task(_snapshot_all_servers())
            elif _cache_initialized["playerauctions"]:
                logger.warning(
                    "PA Retail returned 0 — keeping %d cached",
                    len(_cache["playerauctions"]),
                )
            else:
                logger.warning("PA Retail returned 0 on cold start")
        except Exception:
            logger.exception("PA Retail parser failed")
        finally:
            _running["playerauctions_retail"] = False
        await asyncio.sleep(10800)


async def start_background_parsers() -> None:
    """Start background FunPay, G2G Classic/MoP, G2G Retail, and PA loops."""
    asyncio.create_task(_run_funpay_loop())
    asyncio.create_task(_run_g2g_loop())
    asyncio.create_task(_run_g2g_retail_loop())
    asyncio.create_task(_run_g2g_retail_rec_loop())
    asyncio.create_task(_run_pa_classic_loop())
    asyncio.create_task(_run_pa_retail_loop())
    logger.info(
        "Background parsers started (funpay + g2g + g2g_retail x2 + pa x2)"
    )


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
    group_realm_sources: dict[str, dict[str, set[str]]] = {}

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
            group_realm_sources.setdefault(ds, {})
            group_realm_sources[ds].setdefault(offer.server_name, set())
            group_realm_sources[ds][offer.server_name].add(offer.source)
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
            realm_sources={
                realm: sorted(group_realm_sources.get(ds, {}).get(realm, set()))
                for realm in sorted(group_realms.get(ds, set()))
            },
            min_price=round(group_min_price[ds], 4),
            game_version=_game_version_from_display(ds),
        )
        for ds in sorted_groups
    ]


def get_offers(
    server: str | None = None,
    faction: str | None = None,
    sort_by: str = "price",
    server_name: str | None = None,
) -> list[Offer]:
    result = (
        _cache["funpay"]
        + _cache["g2g"]
        + _cache["g2g_retail"]
        + _cache["playerauctions"]
    )

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
