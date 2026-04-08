"""
api/router.py — FastAPI route definitions.

Backward-compat guarantee:
  GET /offers             — still works, new optional ?price_unit param
  GET /servers            — unchanged
  GET /price-history      — unchanged (in-memory snapshot)
  GET /price-history/ohlc — unchanged (legacy OHLC from DB)
  GET /index/{server}     — unchanged

New endpoints (refactor):
  GET /price-history      — now also supports ?server=Firemaw&region=EU&version=...
                            falls back to legacy in-memory if no DB data
  GET /price-index        — current index for all active servers (per real server)
  GET /admin/unresolved-servers — titles that couldn't be mapped to a canonical server
"""
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi.security import APIKeyHeader

from api.schemas import (
    MetaResponse,
    OfferRow,
    OffersResponse,
    PriceHistoryResponse,
    PriceIndexResponse,
    PriceUnit,
    ServerHistoryPoint,
    ServerHistoryResponse,
    ServerPriceIndexEntry,
    ServersResponse,
)
from service.offers_service import (
    get_meta,
    get_offers,
    get_parser_status,
    get_price_history,
    get_quarantine,
    get_servers,
)

router = APIRouter()

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


async def require_admin_key(key: str | None = Security(api_key_header)) -> None:
    if not ADMIN_API_KEY or key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")


# ── Data version ──────────────────────────────────────────────────────────────

@router.get("/meta", response_model=MetaResponse)
async def get_meta_handler():
    """Data version. Frontend polls every ~10 s.
    If last_update changed — client re-fetches /offers + /price-history.
    """
    return MetaResponse(last_update=get_meta())


# ── Server list ───────────────────────────────────────────────────────────────

@router.get("/servers", response_model=ServersResponse)
async def get_servers_handler():
    """
    Hierarchical server group list.

    Each group:
      display_server: "(EU) Anniversary"
      realms:         ["Firemaw", "Spineshatter", ...]  (G2G only; empty for FunPay)
      min_price:      best_ask from IndexPrice (realistic buy price now, per 1k)
    """
    groups = get_servers()
    return ServersResponse(count=len(groups), servers=groups)


# ── Offers ────────────────────────────────────────────────────────────────────

@router.get("/offers", response_model=OffersResponse)
async def get_offers_handler(
    server:      str | None = Query(None),
    server_name: str | None = Query(None),
    faction:     str | None = Query(None),
    sort_by:     str = Query("price", pattern="^(price|amount)$"),
    price_unit:  PriceUnit = Query(
        "per_1k",
        description=(
            "Display unit for price_display field. "
            "'per_1k' = price per 1000 gold (default), "
            "'per_1' = price per 1 gold."
        ),
    ),
):
    """
    Returns offers, optionally filtered by server / server_name / faction.

    Task 2: price_unit query param controls price_display field:
      per_1k  → price_display = price_per_1k  (default, backward compat)
      per_1   → price_display = price per 1 gold = price_per_1k / 1000

    price_per_1k is always present for backward compatibility.
    Conversion happens ONLY in the serializer layer — never stored.
    """
    offers = get_offers(server, faction, sort_by, server_name)
    rows = [OfferRow.from_offer(o, price_unit) for o in offers]
    return OffersResponse(count=len(rows), offers=rows, price_unit=price_unit)


# ── Parser diagnostics ────────────────────────────────────────────────────────

@router.get("/parser-status")
async def parser_status_handler():
    """
    Diagnostic endpoint: state of each parser.

    Example:
      {
        "funpay": {"offers": 142, "last_update": "...", "running": false, "version": 5},
        "g2g":    {"offers":  87, "last_update": "...", "running": true,  "version": 3}
      }
    """
    return get_parser_status()


# ── Price history (legacy in-memory + new per-server from DB) ─────────────────

@router.get("/price-history", summary="Price history (in-memory snapshot or per-server DB)")
async def get_price_history_handler(
    server:  str = Query("all"),
    faction: str = Query("all"),
    last:    int = Query(50, ge=1, le=200),
    # Per-server params (Task 4) — all three required to use DB path
    region:  str | None = Query(None, description="Region, e.g. 'EU'. Required for per-server DB query."),
    version: str | None = Query(None, description="Version, e.g. 'Classic Era'. Required for per-server DB query."),
):
    """
    Two modes:

    1. Legacy in-memory (default, ?server=all or group label):
       Returns current snapshot from in-memory cache.
       Same as before — backward compatible.

    2. Per-server from DB (Task 4): requires server + region + version:
       GET /price-history?server=Firemaw&region=EU&version=Classic+Era&faction=Horde
       Returns real price history for that specific realm from server_price_history.
    """
    # Mode 2: per-server DB query
    if server != "all" and region and version:
        from db.writer import query_server_history
        rows = await query_server_history(
            server_name=server,
            region=region.upper(),
            version=version,
            faction=faction,
            last=last,
        )
        if rows:
            points = [
                ServerHistoryPoint(
                    recorded_at=p["recorded_at"],
                    index_price=p["index_price"],
                    index_price_per_1k=p["index_price_per_1k"],
                    sample_size=p["sample_size"],
                )
                for p in rows
            ]
            return ServerHistoryResponse(
                server=server,
                region=region.upper(),
                version=version,
                faction=faction,
                count=len(points),
                points=points,
            )
        # DB empty / unavailable — fall through to in-memory

    # Mode 1: legacy in-memory
    points = get_price_history(server, faction, last)
    return {
        "count": len(points),
        "points": points,
    }


# ── OHLC (legacy group-level) ─────────────────────────────────────────────────

@router.get("/price-history/ohlc")
async def price_history_ohlc(
    server:     str = Query(..., description="display_server, e.g. '(EU) Anniversary'"),
    faction:    str = Query("all", description="'all' | 'Alliance' | 'Horde'"),
    last_hours: int = Query(168, ge=1, le=8760),
    max_points: int = Query(500, ge=50, le=2000),
):
    """
    OHLC + VWAP + best_ask from price_index_snapshots (group-level).
    Adaptive bucket = max(5, last_hours*60 / max_points) minutes.
    Returns [] if DATABASE_URL not set or DB unavailable.
    """
    from db.writer import query_index_history
    points = await query_index_history(server, faction, last_hours, max_points)
    return {
        "count":  len(points),
        "points": points,
        "meta": {
            "server":         server,
            "faction":        faction,
            "last_hours":     last_hours,
            "bucket_minutes": max(5, (last_hours * 60) // max_points),
        },
    }


# ── Per-server current index (Task 4) ────────────────────────────────────────

@router.get("/price-index", response_model=PriceIndexResponse)
async def get_price_index(
    faction: str = Query(
        "all",
        description="Filter by faction: 'all' | 'Alliance' | 'Horde'",
    ),
):
    """
    Current price index for all active servers (Task 4).

    Returns one entry per server+faction combination.
    index_price is price per unit (per 1 gold).
    index_price_per_1k is the display convenience value (per 1000 gold).
    """
    from db.writer import query_price_index_all
    rows = await query_price_index_all(faction)

    entries = [
        ServerPriceIndexEntry(
            server_name=r["server_name"],
            region=r["region"],
            version=r["version"],
            faction=r["faction"],
            index_price=r["index_price"],
            index_price_per_1k=r["index_price_per_1k"],
            sample_size=r["sample_size"],
            min_price=r["min_price"],
            max_price=r["max_price"],
            computed_at=r["computed_at"],
        )
        for r in rows
    ]
    return PriceIndexResponse(count=len(entries), entries=entries)


# ── Legacy group-level index (in-memory) ─────────────────────────────────────

@router.get("/index/{server:path}")
async def get_index_price(
    server:  str,
    faction: str = Query("All"),
    region:  str | None = Query(None, description="Region for per-server lookup (e.g. 'EU')"),
    version: str | None = Query(None, description="Version for per-server lookup (e.g. 'Anniversary')"),
):
    """
    Current IndexPrice from in-memory cache (< 1ms).

    Two lookup modes:
    1. Group (legacy): key = "{display_server}::{faction}"
       e.g. /index/(EU) Anniversary?faction=All
    2. Per-server (Task 4): key = "{server_name}::{region}::{version}::{faction}"
       e.g. /index/Firemaw?region=EU&version=Anniversary&faction=Horde
    """
    from service.offers_service import _index_cache

    # Try per-server key first if region+version supplied (Task 4)
    idx = None
    if region and version:
        per_server_key = f"{server}::{region.upper()}::{version}::{faction}"
        idx = _index_cache.get(per_server_key)

    # Fallback to group-level key (legacy)
    if idx is None:
        key = f"{server}::{faction}"
        idx = _index_cache.get(key)

    if idx is None:
        raise HTTPException(
            status_code=404,
            detail=f"No index data for {server!r} / {faction!r}",
        )
    return {
        "server":       server,
        "faction":      faction,
        "index_price":  idx.index_price,
        "vwap":         idx.vwap,
        "best_ask":     idx.best_ask,
        "price_min":    idx.price_min,
        "price_max":    idx.price_max,
        "offer_count":  idx.offer_count,
        "total_volume": idx.total_volume,
        "sources":      idx.sources,
    }


# ── Admin: unresolved server titles (Task 3) ──────────────────────────────────

@router.get("/admin/unresolved-servers", dependencies=[Depends(require_admin_key)])
async def get_unresolved_servers():
    """
    Admin endpoint: list all offer titles that couldn't be mapped to a
    canonical server in the servers table.

    Use this to discover missing aliases and populate server_aliases.

    Response:
      [
        {
          "raw_title":  "Shadowmoon [EU - Anniversary] - Horde",
          "source":     "g2g",
          "first_seen": 1712345678.0,   // Unix timestamp
          "count":      42              // how many times seen unresolved
        },
        ...
      ]
    Sorted by count DESC (most frequent first).
    """
    from db.server_resolver import get_unresolved
    items = get_unresolved()
    return {
        "count": len(items),
        "unresolved": items,
    }


@router.get("/admin/quarantine", dependencies=[Depends(require_admin_key)])
async def get_quarantine_handler():
    """
    Admin endpoint: offers rejected by the normalization pipeline.

    Returns up to 500 most-recently quarantined offers (newest first).
    Each entry contains:
      raw_id    — offer ID as produced by the parser
      source    — "funpay" | "g2g"
      reason    — rejection reason, e.g. "empty_server_title",
                  "unknown_faction:...", "zero_price", "pipeline_exception"
      raw_title — display_server as received from parser
      price     — price_per_1k at the time of rejection
      ts        — Unix timestamp when the offer was quarantined

    Use this alongside /admin/unresolved-servers to diagnose parser issues
    and missing server aliases.
    """
    items = get_quarantine()
    return {"count": len(items), "quarantined": items}


@router.get("/admin/price-profiles", dependencies=[Depends(require_admin_key)])
async def get_price_profiles_handler():
    """
    Admin diagnostic: current in-memory price profiles per canonical server.

    Returns summary stats (server count, total profiles, staleness).
    Used to verify that price validation is active and profiles are populated.
    """
    from service.price_profiles import get_stats
    return get_stats()


@router.post("/admin/register-alias", dependencies=[Depends(require_admin_key)])
async def register_alias(
    alias:     str = Query(..., description="Raw title to register as alias"),
    server_id: int = Query(..., description="Target server ID from servers table"),
    source:    str | None = Query(None, description="Source: 'g2g' | 'funpay' | null"),
):
    """
    Register a new alias in server_aliases table.
    After registration, the alias cache is updated in-process.

    Use together with /admin/unresolved-servers to manually map unknown titles.
    """
    from db.server_resolver import register_alias as _register
    from db.writer import get_pool

    pool = await get_pool()
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — cannot register alias",
        )
    await _register(alias, server_id, source, pool)
    return {"registered": True, "alias": alias, "server_id": server_id}


@router.post("/admin/cache-reset", dependencies=[Depends(require_admin_key)])
async def admin_cache_reset():
    """
    Reset the alias cache circuit-breaker and force a fresh reload attempt.

    Use this after a failed deploy where DB tables were missing and the
    circuit-breaker tripped.  After calling this endpoint the next resolve
    call will attempt to reload the alias cache from the DB.

    Also calls invalidate_cache() so the reload happens immediately on the
    next resolve, not after the normal 60 s TTL.
    """
    from db.server_resolver import (
        invalidate_cache,
        reset_alias_cache_circuit_breaker,
    )

    reset_alias_cache_circuit_breaker()
    await invalidate_cache()
    return {"reset": True, "message": "Alias cache circuit-breaker cleared — will reload on next resolve"}
