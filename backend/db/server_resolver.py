"""
db/server_resolver.py — Canonical server lookup.

Resolves raw parser titles to server_id in the canonical servers table.

Two-level strategy:
  Level 1 — Exact alias match in server_aliases table (fast, indexed).
  Level 2 — Fuzzy match: normalize title → extract (name, region, version)
             → look up servers table directly.

Returns Optional[int] — server_id, or None if unresolvable.
Unresolved titles are logged as WARNING and may be reviewed via:
  GET /admin/unresolved-servers

Thread-safety: in-process cache is populated on first call per process.
Cache is intentionally short-lived (60 s) to pick up newly seeded aliases
without restarting the server.

Server data cache (added for normalize_pipeline):
  _server_data_cache maps server_id → {"id", "name", "region", "version"}.
  Loaded alongside alias cache. Used by normalize_pipeline to canonicalize
  offer fields after resolution — version always comes from this registry.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── In-process alias cache ────────────────────────────────────────────────────
# {alias_text_lower: server_id}
_alias_cache:     dict[str, int] = {}
_cache_loaded_at: float = 0.0
_CACHE_TTL        = 60.0   # seconds

# ── Server data cache (id → canonical fields) ─────────────────────────────────
# Populated alongside alias cache. Gives O(1) lookup of canonical
# (name, region, version) for a resolved server_id without extra DB calls.
_server_data_cache: dict[int, dict] = {}
# {(name_lower, region_upper): [{"id", "name", "region", "version"}, ...]}
# Used by find_server_versions() for price-rerouting lookups.
_server_versions_index: dict[tuple[str, str], list[dict]] = {}

# Batch alias lookup cache: per-entry TTL (no global flush — safe under concurrent batches)
_BATCH_MISS = object()
_batch_ttl_cache: dict[str, tuple[int, float]] = {}  # lower(alias) -> (server_id, expires_at mono)
_BATCH_ENTRY_TTL = 300.0
_BATCH_ENTRY_MAX = 500


def _batch_cache_get(lo: str):
    entry = _batch_ttl_cache.get(lo)
    if entry is None:
        return _BATCH_MISS
    server_id, expires_at = entry
    if time.monotonic() > expires_at:
        del _batch_ttl_cache[lo]
        return _BATCH_MISS
    return server_id


def _batch_cache_set(lo: str, server_id: int) -> None:
    if len(_batch_ttl_cache) >= _BATCH_ENTRY_MAX:
        n = max(1, _BATCH_ENTRY_MAX // 10)
        oldest = sorted(
            _batch_ttl_cache.items(),
            key=lambda x: x[1][1],
        )[:n]
        for k, _ in oldest:
            del _batch_ttl_cache[k]
    _batch_ttl_cache[lo] = (server_id, time.monotonic() + _BATCH_ENTRY_TTL)

# ── Unresolved registry (for /admin/unresolved-servers) ──────────────────────
# {raw_title: {"source": str, "first_seen": float, "count": int}}
_unresolved: dict[str, dict] = {}


# ── Version normalisation (mirrors g2g_parser._VERSION_PATTERNS) ─────────────
_VERSION_NORMALISE: list[tuple[re.Pattern, str]] = [
    (re.compile(r"season\s+of\s+discovery|\bseasonal\b|\bsod\b", re.I), "Season of Discovery"),
    (re.compile(r"anniversary",                                    re.I), "Anniversary"),
    (re.compile(r"classic\s+era",                                  re.I), "Classic Era"),
    (re.compile(r"\bclassic\b",                                    re.I), "Classic"),
]

_REGION_RE = re.compile(
    r"\b(?P<region>EU|US|NA|OCE|KR|TW|SEA|RU)\b", re.IGNORECASE
)
_BRACKET_TITLE_RE = re.compile(
    r"^(?P<server>.+?)\s*\[(?P<region>[A-Za-z]{2,})\s*-\s*(?P<version>[^\]]+?)\]\s*"
    r"(?:-\s*(?:Alliance|Horde))?$",
    re.IGNORECASE,
)

# NA is treated as US in the canonical registry
_REGION_MAP = {"NA": "US"}


def _normalise_version(raw: str) -> str:
    for pattern, canonical in _VERSION_NORMALISE:
        if pattern.search(raw):
            return canonical
    return raw.strip()


def _normalise_region(raw: str) -> str:
    r = raw.strip().upper()
    return _REGION_MAP.get(r, r)


async def _load_alias_cache(pool) -> None:
    """Reload alias cache and server data cache from DB into memory.

    Alias conflict detection:
      Each alias MUST map to exactly one server. If the DB contains a duplicate
      (two server_aliases rows pointing different server_ids for the same alias
      text), both are logged as reason="alias_conflict" and the alias is omitted
      from the in-process cache so it cannot silently resolve to the wrong server.
      This is a configuration error requiring manual review.
    """
    global _alias_cache, _cache_loaded_at
    global _server_data_cache, _server_versions_index
    try:
        # ── Alias cache (with conflict detection) ─────────────────────────────
        alias_rows = await pool.fetch(
            "SELECT alias, server_id FROM server_aliases"
        )

        # Build with conflict detection: alias (lower) → server_id
        new_alias_cache: dict[str, int] = {}
        conflicted_aliases: set[str] = set()

        for row in alias_rows:
            lo = row["alias"].lower()
            sid = row["server_id"]
            if lo in new_alias_cache:
                if new_alias_cache[lo] != sid:
                    # Two different server_ids for the same alias text → conflict
                    logger.warning(
                        "server_resolver: alias_conflict alias=%r "
                        "server_id_a=%d server_id_b=%d — alias excluded from cache",
                        row["alias"], new_alias_cache[lo], sid,
                    )
                    conflicted_aliases.add(lo)
            else:
                new_alias_cache[lo] = sid

        # Remove conflicted aliases so they never silently resolve
        for lo in conflicted_aliases:
            del new_alias_cache[lo]

        _alias_cache = new_alias_cache

        # ── Server data cache ─────────────────────────────────────────────────
        # Load ALL servers (active AND inactive) so normalize_pipeline can check
        # is_active and quarantine deprecated-version offers correctly.
        server_rows = await pool.fetch(
            """
            SELECT id, name, region, version, realm_type, is_active
              FROM servers
            """
        )
        new_data: dict[int, dict] = {}
        new_versions: dict[tuple[str, str], list[dict]] = {}
        for row in server_rows:
            entry = {
                "id":         row["id"],
                "name":       row["name"],
                "region":     row["region"],
                "version":    row["version"],
                "realm_type": row["realm_type"] if "realm_type" in row.keys() else "Normal",
                "is_active":  row["is_active"],
            }
            new_data[row["id"]] = entry
            # Version index only includes active servers (used for price rerouting)
            if row["is_active"]:
                key = (row["name"].lower(), row["region"].upper())
                new_versions.setdefault(key, []).append(entry)

        _server_data_cache     = new_data
        _server_versions_index = new_versions

        _cache_loaded_at = time.monotonic()
        logger.debug(
            "server_resolver: loaded %d aliases (%d conflicts excluded), "
            "%d servers into cache",
            len(_alias_cache),
            len(conflicted_aliases),
            len(new_data),
        )
    except Exception:
        logger.exception("server_resolver: failed to load alias cache")


async def _ensure_cache(pool) -> None:
    now = time.monotonic()
    if now - _cache_loaded_at > _CACHE_TTL:
        await _load_alias_cache(pool)


async def resolve_server_batch(
    pool,
    keys: list[tuple[str, str]],
) -> dict[str, int]:
    """
    Resolve many aliases with one DB round-trip (WHERE LOWER(alias) = ANY($1)).

    keys: list of (raw_alias, source) — source is ignored for SQL; kept for API symmetry.
    Returns: mapping lower(alias) -> server_id for hits only.

    In-process cache: per-entry TTL (_BATCH_ENTRY_TTL), max _BATCH_ENTRY_MAX keys.
    Merges hits into _alias_cache for resolve_server().
    """
    if not pool or not keys:
        return {}

    lowers_unique: list[str] = []
    seen_lo: set[str] = set()
    for raw, _src in keys:
        lo = (raw or "").lower().strip()
        if not lo or lo in seen_lo:
            continue
        seen_lo.add(lo)
        lowers_unique.append(lo)

    need_fetch = [lo for lo in lowers_unique if _batch_cache_get(lo) is _BATCH_MISS]
    if need_fetch:
        try:
            rows = await pool.fetch(
                """
                SELECT LOWER(alias) AS la, server_id
                FROM server_aliases
                WHERE LOWER(alias) = ANY($1::text[])
                """,
                need_fetch,
            )
            for r in rows:
                la = r["la"]
                sid = r["server_id"]
                _batch_cache_set(la, sid)
                _alias_cache[la] = sid
            # Aliases queried but absent in DB: short TTL negative cache would go here;
            # omitted — uncached misses go to DB each batch until alias is added.
        except Exception:
            logger.exception("server_resolver: resolve_server_batch DB query failed")

    out: dict[str, int] = {}
    for lo in lowers_unique:
        hit = _batch_cache_get(lo)
        if hit is not _BATCH_MISS:
            out[lo] = hit
    return out


# ── Public API ────────────────────────────────────────────────────────────────

def get_server_data(server_id: int) -> Optional[dict]:
    """
    Return canonical data for a resolved server_id.

    Synchronous — reads in-process cache populated by _load_alias_cache().
    Returns {"id", "name", "region", "version"} or None if not cached.

    Used by normalize_pipeline to canonicalize offer fields after resolution.
    Version comes from this registry, NEVER from the raw source title.
    """
    return _server_data_cache.get(server_id)


async def find_server_versions(
    name: str,
    region: str,
    pool,
) -> list[dict]:
    """
    Return all active servers with the given name and region.

    Each entry: {"id", "name", "region", "version"}.

    Used by normalize_pipeline for price-assisted rerouting: when an offer's
    price doesn't fit the resolved server's profile, we check whether it fits
    another version (e.g. "Classic" vs "Anniversary") of the same realm.

    Tries in-process cache first; falls back to DB on cache miss.
    """
    key = (name.lower(), region.upper())
    cached = _server_versions_index.get(key)
    if cached is not None:
        return cached

    # Cache miss: query DB directly (rare — only on cold start or new servers)
    try:
        rows = await pool.fetch(
            """
            SELECT id, name, region, version
            FROM servers
            WHERE LOWER(name) = LOWER($1)
              AND region = $2
              AND is_active = TRUE
            """,
            name, region.upper(),
        )
        result = [
            {"id": r["id"], "name": r["name"], "region": r["region"], "version": r["version"]}
            for r in rows
        ]
        # Store in index for future lookups
        if result:
            _server_versions_index[key] = result
        return result
    except Exception:
        logger.exception(
            "server_resolver: find_server_versions failed name=%r region=%r", name, region
        )
        return []


async def resolve_server(
    raw_title: str,
    source: str,
    pool,
) -> Optional[int]:
    """
    Resolve raw_title → server_id.

    Level 1: exact alias match (case-insensitive) in server_aliases.
    Level 2: parse title → look up servers(name, region, version).

    If unresolvable → log warning, add to _unresolved registry, return None.
    """
    if not raw_title:
        return None

    await _ensure_cache(pool)

    # ── Level 1: exact alias ──────────────────────────────────────────────────
    lower = raw_title.lower().strip()
    server_id = _alias_cache.get(lower)
    if server_id is not None:
        return server_id

    # ── Level 2: fuzzy parse ──────────────────────────────────────────────────
    server_id = await _fuzzy_resolve(raw_title, source, pool)
    if server_id is not None:
        # Cache the new mapping in memory (DB is updated by the caller
        # or admin via /admin/unresolved-servers + manual seed)
        _alias_cache[lower] = server_id
        return server_id

    # ── Unresolved ────────────────────────────────────────────────────────────
    _record_unresolved(raw_title, source)
    return None


async def _fuzzy_resolve(
    raw_title: str,
    source: str,
    pool,
) -> Optional[int]:
    """
    Parse title into (server_name, region, version) and look up DB.

    For G2G titles like "Spineshatter [EU - Anniversary] - Alliance":
      server_name = "Spineshatter", region = "EU", version = "Anniversary"

    For FunPay titles like "(EU) Classic Era":
      These are GROUP labels — fuzzy resolve won't work here, they need
      expansion logic (handled separately in offers_service).
    """
    # G2G strict format
    m = _BRACKET_TITLE_RE.match(raw_title.strip())
    if m:
        server_name = m.group("server").strip()
        region      = _normalise_region(m.group("region"))
        version     = _normalise_version(m.group("version"))
        return await _lookup_server(server_name, region, version, pool)

    # FunPay group format: "(EU) Anniversary", "(US) Classic Era - Firemaw"
    fp_m = re.match(
        r"^\((?P<region>[A-Za-z]{2,})\)\s*(?P<rest>.+)$",
        raw_title.strip(),
    )
    if fp_m:
        region = _normalise_region(fp_m.group("region"))
        rest   = fp_m.group("rest").strip()
        # Check if " - ServerName" suffix is present
        parts = rest.rsplit(" - ", 1)
        if len(parts) == 2:
            version     = _normalise_version(parts[0])
            server_name = parts[1].strip()
            return await _lookup_server(server_name, region, version, pool)
        # Just a version label → can't resolve to single server
        return None

    # Plain server name search (last resort)
    rm = _REGION_RE.search(raw_title)
    if rm:
        region = _normalise_region(rm.group("region"))
        # Try to find server_name as the part before the region token
        before = raw_title[:rm.start()].strip().rstrip("-").strip()
        if before:
            for pattern, version in _VERSION_NORMALISE:
                if pattern.search(raw_title):
                    return await _lookup_server(before, region, version, pool)

    return None


async def _lookup_server(
    name: str,
    region: str,
    version: str,
    pool,
) -> Optional[int]:
    """Query servers table for (name, region, version) → server_id.

    Intentionally returns BOTH active and inactive servers: normalize_pipeline
    checks is_active separately and quarantines deprecated-version offers.
    Resolving the server_id is always correct; the active check is policy.
    """
    if not (name and region and version):
        return None
    try:
        row = await pool.fetchrow(
            """
            SELECT id FROM servers
            WHERE LOWER(name)    = LOWER($1)
              AND region         = $2
              AND LOWER(version) = LOWER($3)
            """,
            name, region, version,
        )
        if row:
            return row["id"]
    except Exception:
        logger.exception(
            "server_resolver: DB lookup failed for (%s, %s, %s)",
            name, region, version,
        )
    return None


# ── Unresolved registry ───────────────────────────────────────────────────────

def _record_unresolved(raw_title: str, source: str) -> None:
    entry = _unresolved.get(raw_title)
    if entry is None:
        logger.warning(
            "server_resolver: unresolved server title=%r source=%s",
            raw_title, source,
        )
        _unresolved[raw_title] = {
            "source":     source,
            "first_seen": time.time(),
            "count":      1,
        }
    else:
        entry["count"] += 1


def get_unresolved() -> list[dict]:
    """Return list of unresolved titles for /admin/unresolved-servers."""
    return [
        {
            "raw_title":   title,
            "source":      info["source"],
            "first_seen":  info["first_seen"],
            "count":       info["count"],
        }
        for title, info in sorted(
            _unresolved.items(),
            key=lambda kv: kv[1]["count"],
            reverse=True,
        )
    ]


async def register_alias(
    alias: str,
    server_id: int,
    source: str | None,
    pool,
) -> None:
    """
    Persist a new alias to server_aliases and add to in-process cache.
    Called by admin endpoints or auto-learning code.
    """
    try:
        await pool.execute(
            """
            INSERT INTO server_aliases (server_id, alias, source)
            VALUES ($1, $2, $3)
            ON CONFLICT (alias) DO NOTHING
            """,
            server_id, alias, source,
        )
        _alias_cache[alias.lower()] = server_id
        _unresolved.pop(alias, None)
        logger.info(
            "server_resolver: registered alias=%r → server_id=%d", alias, server_id
        )
    except Exception:
        logger.exception(
            "server_resolver: failed to register alias=%r", alias
        )


async def invalidate_cache() -> None:
    """Force alias cache reload on next resolve call."""
    global _cache_loaded_at
    _cache_loaded_at = 0.0
