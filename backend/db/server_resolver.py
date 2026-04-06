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
"""
from __future__ import annotations

import asyncio
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
    """Reload alias cache from DB into memory."""
    global _alias_cache, _cache_loaded_at
    try:
        rows = await pool.fetch(
            "SELECT alias, server_id FROM server_aliases"
        )
        _alias_cache = {row["alias"].lower(): row["server_id"] for row in rows}
        _cache_loaded_at = time.monotonic()
        logger.debug("server_resolver: loaded %d aliases into cache", len(_alias_cache))
    except Exception:
        logger.exception("server_resolver: failed to load alias cache")


async def _ensure_cache(pool) -> None:
    now = time.monotonic()
    if now - _cache_loaded_at > _CACHE_TTL:
        await _load_alias_cache(pool)


# ── Public API ────────────────────────────────────────────────────────────────

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
    """Query servers table for (name, region, version) → server_id."""
    if not (name and region and version):
        return None
    try:
        row = await pool.fetchrow(
            """
            SELECT id FROM servers
            WHERE LOWER(name)    = LOWER($1)
              AND region         = $2
              AND LOWER(version) = LOWER($3)
              AND is_active      = TRUE
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
