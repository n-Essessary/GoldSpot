"""
service/normalize_pipeline.py — Deterministic offer normalization pipeline.

Pipeline for every raw offer coming out of a parser:

    RAW OFFER → [1. validate] → [2. resolve] → [3. canonicalize]
              → [4. is_active check] → [5. dedup] → NORMALIZED OFFER or QUARANTINE

Guarantees:
  1. Deterministic: same input + same DB state → same output, always.
  2. Version, region, and realm_type come from the canonical server registry
     (servers table), NEVER from the raw source title.
  3. Missing faction defaults to "Horde". Unknown faction is quarantined.
  4. Broken title (empty server, pipeline exception) → quarantine.
  5. Unresolvable server → quarantine with reason="unresolved_server".
     No implicit fallbacks, no guessing, no default assignments.
  6. Inactive server (is_active=False, e.g. Season of Mastery) → quarantine
     with reason="deprecated_version".
  7. Source region override: if the raw offer's region (from display_server)
     differs from the canonical region, the canonical wins and the event is
     logged with reason="wrong_region_overridden" (informational, not quarantine).
  8. Alias conflicts (two server_ids for same alias) → quarantine with
     reason="alias_conflict". Not auto-resolved.
  9. Dedup by (source, offer_id) within each batch.

What this pipeline does NOT touch:
  • raw_price, raw_price_unit, lot_size, price_per_1k (parser contract)
  • Parser output beyond display_server / server_name / faction defaults
  • /offers response contract (OfferRow / OffersResponse)
  • Frontend-facing field names

Quarantine reasons (canonical set):
  "empty_server_title"    — server_name and display_server both empty
  "unknown_faction:…"     — unrecognised faction string
  "zero_price"            — price_per_1k ≤ 0
  "unresolved_server"     — no canonical server found for the alias
  "deprecated_version"    — server is_active=False (e.g. Season of Mastery)
  "alias_conflict"        — alias maps to multiple canonical servers
  "pipeline_exception"    — unexpected exception during processing
  (informational, not quarantine): "wrong_region_overridden"
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from api.schemas import Offer

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

_DEFAULT_FACTION  = "Horde"
_VALID_FACTIONS   = frozenset({"horde", "alliance"})

# Source priority for breaking ties when two sources produce the same
# (server_name, region, version) after canonicalisation.
# Lower number = higher priority.
_SOURCE_PRIORITY: dict[str, int] = {
    "funpay": 0,
    "g2g":    1,
}

# ── G2G raw_title parser for degraded mode ───────────────────────────────────
# When the alias cache is unavailable we cannot resolve server_id, but we still
# need a valid display_server for the sidebar.  Reconstruct it from the G2G
# raw_title format: "ServerName [REGION - VERSION] - Faction"
_DEGRADED_G2G_RE = re.compile(
    r"^(?P<server>.+?)\s*\[(?P<region>[A-Za-z]{2,})\s*-\s*(?P<version>[^\]]+?)\]"
    r"\s*(?:-\s*(?:Alliance|Horde))?$",
    re.IGNORECASE,
)


def _reconstruct_display_server_from_raw_title(offer: "Offer") -> bool:
    """Set display_server from raw_title for G2G offers in degraded mode.

    Returns True if display_server was successfully reconstructed.
    Does NOT touch non-G2G offers (FunPay already sets display_server from HTML).

    Canonicalizes the version string via _canonicalize_version so that
    degraded-mode offers produce valid sidebar group keys:
      "Spineshatter [EU - Seasonal] - Horde"
        → display_server = "(EU) Season of Discovery"  (not "(EU) Seasonal")
    """
    from utils.version_utils import _canonicalize_version

    if offer.source != "g2g":
        return bool((offer.display_server or "").strip())

    raw = (getattr(offer, "raw_title", "") or "").strip()
    if not raw:
        return False

    m = _DEGRADED_G2G_RE.match(raw)
    if not m:
        return False

    region      = m.group("region").upper()
    version_raw = m.group("version").strip()
    server      = m.group("server").strip()

    # Normalise region: NA → US (matches server_resolver._REGION_MAP)
    if region == "NA":
        region = "US"

    # Canonicalize version so the sidebar shows a stable group label.
    # "Seasonal" → "Season of Discovery", "anniversary" → "Anniversary", etc.
    version = _canonicalize_version(version_raw)

    display = f"({region}) {version}"
    offer.display_server = display
    offer.server         = display.lower()
    if server:
        offer.server_name = server

    return True


# ── Quarantine record ─────────────────────────────────────────────────────────

@dataclass
class QuarantinedOffer:
    """
    An offer that failed validation and was rejected from the live cache.
    Stored in the quarantine log (bounded ring buffer) for admin inspection.
    """
    raw_id:    str
    source:    str
    reason:    str           # e.g. "empty_server_title", "unknown_faction:..."
    raw_title: str = ""      # display_server as received from parser
    price:     float = 0.0   # price_per_1k at time of quarantine
    details:   str = ""      # free-form extra context
    ts:        float = field(default_factory=time.time)


# ── Step 1: Field validation ──────────────────────────────────────────────────

def _validate_and_default(offer: "Offer") -> tuple[Optional["Offer"], Optional[str]]:
    """
    Apply defaults and validate required fields.

    Returns (offer, None)        on success.
    Returns (offer, reason_str)  when the offer must be quarantined.

    Mutations (applied in-place on mutable Pydantic model):
      • Empty / missing faction → defaulted to "Horde"
    """
    # ── Faction ───────────────────────────────────────────────────────────────
    faction_lower = (offer.faction or "").strip().lower()
    if not faction_lower:
        offer.faction = _DEFAULT_FACTION
        logger.debug(
            "normalize: defaulted empty faction → Horde  offer_id=%s", offer.id
        )
    elif faction_lower not in _VALID_FACTIONS:
        return offer, f"unknown_faction:{offer.faction!r}"

    # ── Server title must be non-empty ────────────────────────────────────────
    if not (offer.server_name or "").strip() and not (offer.display_server or "").strip():
        return offer, "empty_server_title"

    # ── Price sanity (should already be validated by Offer model_validator) ──
    if offer.price_per_1k <= 0:
        return offer, "zero_price"

    return offer, None


# ── Step 3: Canonicalization ──────────────────────────────────────────────────

_DISPLAY_REGION_RE = re.compile(r"^\(([A-Z]{2,})\)", re.IGNORECASE)


def _apply_canonical(offer: "Offer", server_data: dict) -> None:
    """
    Overwrite offer fields with canonical values from the servers table.

    server_data: {
      "id": int, "name": str, "region": str, "version": str,
      "realm_type": str, "is_active": bool
    }

    Canonical assignment rules (enforced here):
      • region  — always from canonical, never from source
      • version — always from canonical, never from source title
      • realm_type — always from canonical ("Normal" | "Hardcore")
      • display_server — derived: "(REGION) VERSION" + " · Hardcore" for HC realms

    If the source region (extracted from the current display_server) differs
    from the canonical region, the canonical wins and a "wrong_region_overridden"
    event is logged (informational — offer is NOT quarantined).

    Mutates offer in-place.
    """
    canonical_region    = server_data["region"]
    canonical_version   = server_data["version"]
    canonical_name      = server_data["name"]
    canonical_realm_type= server_data.get("realm_type", "Normal")

    # ── Region override detection (informational) ─────────────────────────────
    source_region: str = ""
    ds = (offer.display_server or "").strip()
    m = _DISPLAY_REGION_RE.match(ds)
    if m:
        source_region = m.group(1).upper()

    if source_region and source_region != canonical_region:
        logger.info(
            "normalize: wrong_region_overridden  offer_id=%s  "
            "source_region=%r  canonical_region=%r  server=%r",
            offer.id, source_region, canonical_region, canonical_name,
        )

    # ── Build display_server ──────────────────────────────────────────────────
    # Format: "(REGION) VERSION" for Normal realms
    #         "(REGION) VERSION · Hardcore" for Hardcore realms
    base_display = f"({canonical_region}) {canonical_version}"
    if canonical_realm_type == "Hardcore":
        new_display = f"{base_display} · Hardcore"
    else:
        new_display = base_display

    offer.display_server = new_display
    offer.server         = new_display.lower()   # Offer.server is always lowercase
    offer.server_name    = canonical_name
    offer.server_id      = server_data["id"]
    offer.realm_type     = canonical_realm_type


# ── Alias key builders ────────────────────────────────────────────────────────

_DISPLAY_RE = re.compile(r"^\((?P<region>[A-Z]{2,})\)\s*(?P<version>.+)$")


def _build_alias_key(offer: "Offer") -> Optional[str]:
    """
    Construct the raw alias string used for server_aliases lookup.

    G2G:    The raw title from the G2G API IS the alias format.
            Example: "Spineshatter [EU - Anniversary] - Horde"
            We use offer.raw_title directly (set by g2g_parser._to_offer()).
            This is accurate and eliminates version-guessing in the parser.

    FunPay: Alias format is "(EU) Version - ServerName".
            Reconstructed from display_server (already normalised by
            _normalize_funpay_offer) + server_name.

    Returns None if the key cannot be constructed (offer passes to fuzzy
    resolve in server_resolver, or is quarantined as unresolved_server).
    """
    # ── G2G: use raw title directly ───────────────────────────────────────────
    if offer.source == "g2g":
        if offer.raw_title:
            return offer.raw_title  # exact alias format as seeded in DB
        # Fallback for legacy offers in-flight without raw_title:
        # reconstruct from parsed fields if display_server has "(Region) Version" format.
        ds = (offer.display_server or "").strip()
        m = _DISPLAY_RE.match(ds.upper()) or _DISPLAY_RE.match(ds)
        if m and offer.server_name:
            region  = m.group("region").upper()
            version = ds[m.start("version"):].strip()
            # Normalize Unicode apostrophes to ASCII (FunPay uses U+2019 right single quote)
            if offer.server_name:
                offer.server_name = offer.server_name.replace("\u2019", "'").replace("\u2018", "'")
            version = version.replace("\u2019", "'").replace("\u2018", "'")
            return f"{offer.server_name} [{region} - {version}] - {offer.faction}"
        return None

    # ── FunPay: reconstruct from display_server + server_name ─────────────────
    if offer.source == "funpay":
        ds = (offer.display_server or "").strip()
        m = _DISPLAY_RE.match(ds.upper()) or _DISPLAY_RE.match(ds)
        if not m:
            return None
        region  = m.group("region").upper()
        version = ds[m.start("version"):].strip()
        # Normalize Unicode apostrophes to ASCII (FunPay uses U+2019 right single quote)
        if offer.server_name:
            offer.server_name = offer.server_name.replace("\u2019", "'").replace("\u2018", "'")
        version = version.replace("\u2019", "'").replace("\u2018", "'")
        if offer.server_name:
            return f"({region}) {version} - {offer.server_name}"
        # No realm suffix — use group label as alias key
        # e.g. "(US) Ashkandi" or "(EU) Flamelash"
        return f"({region}) {version}"

    return None


def _collect_resolve_keys(
    offers: list["Offer"],
) -> list[tuple[str, str]]:
    """
    Build deduplicated (alias, source) pairs for batch alias lookup.
    Only includes offers that have not yet been resolved (server_id is None)
    and have a usable alias key.
    """
    seen: set[tuple[str, str]] = set()
    out:  list[tuple[str, str]] = []

    for offer in offers:
        if offer.server_id is not None:
            continue
        key = _build_alias_key(offer)
        if key is None:
            continue
        t = (key, offer.source)
        if t not in seen:
            seen.add(t)
            out.append(t)

    return out


# ── Public entry point ────────────────────────────────────────────────────────

async def normalize_offer_batch(
    offers: list["Offer"],
    pool,
) -> tuple[list["Offer"], list[QuarantinedOffer]]:
    """
    Run the full normalization pipeline over a list of raw parser offers.

    Returns:
      normalized   — offers ready for the in-memory cache and DB writes
      quarantined  — offers that were rejected, for admin inspection

    The caller (offers_service) is responsible for:
      • Pre-normalizing display_server format (_normalize_funpay_offer /
        _normalize_g2g_offer) BEFORE calling this function.
      • Storing quarantined offers in the quarantine log.
    """
    from db.server_resolver import (
        get_server_data,
        is_cache_loaded,
        resolve_server,
        resolve_server_batch,
    )

    normalized:  list["Offer"]          = []
    quarantined: list[QuarantinedOffer] = []
    seen_ids:    set[str]               = set()   # dedup: "source:offer_id"

    # ── Degraded mode detection ──────────────────────────────────────────────
    # When the alias cache was never loaded (DB unavailable on startup), the
    # resolver cannot map ANY title → server_id.  In strict mode this means
    # every single offer gets quarantined → empty cache → empty sidebar.
    #
    # Degraded mode: skip quarantine for unresolved offers and pass them
    # through with parser-provided display_server (reconstructed from
    # raw_title for G2G).  Offers get server_id=None — they won't appear in
    # per-server indexes but WILL appear in the sidebar via display_server.
    cache_available = is_cache_loaded()
    if not cache_available:
        logger.warning(
            "normalize_pipeline: alias cache not loaded — running in DEGRADED mode "
            "(offers pass through without server_id resolution)"
        )

    # ── Batch alias resolution (one DB round-trip for all offers) ─────────────
    resolve_keys = _collect_resolve_keys(offers)
    alias_map: dict[str, int] = {}
    if resolve_keys and pool is not None and cache_available:
        alias_map = await resolve_server_batch(pool, resolve_keys)

    # ── Per-offer pipeline ────────────────────────────────────────────────────
    for offer in offers:
        offer_id_for_log = getattr(offer, "id", "?")
        try:
            # ── Step 1: validate & default ────────────────────────────────────
            offer, quarantine_reason = _validate_and_default(offer)
            if quarantine_reason is not None:
                quarantined.append(QuarantinedOffer(
                    raw_id=offer_id_for_log,
                    source=getattr(offer, "source", "unknown"),
                    reason=quarantine_reason,
                    raw_title=(
                        getattr(offer, "raw_title", "")
                        or getattr(offer, "display_server", "")
                    ),
                    price=getattr(offer, "price_per_1k", 0.0),
                ))
                continue

            # ── Step 2: resolve server_id ─────────────────────────────────────
            if offer.server_id is None and pool is not None:
                alias_key = _build_alias_key(offer)
                if alias_key is not None:
                    lk = alias_key.lower().strip()
                    server_id = alias_map.get(lk)

                    if server_id is None:
                        _offer_game_version = getattr(offer, "game_version", "")
                        _versioned_games = {"MoP Classic", "Retail"}
                        server_id = await resolve_server(
                            alias_key,
                            offer.source,
                            pool,
                            game_version=_offer_game_version if _offer_game_version in _versioned_games else "",
                        )
                    if server_id is not None:
                        offer.server_id = server_id

            # ── Unresolved server handling ────────────────────────────────
            if offer.server_id is None:
                if cache_available:
                    # ── Strict mode: cache is healthy → genuinely unknown server
                    # Per spec: "No implicit fallbacks — if canonical server
                    # cannot be resolved, send to quarantine."
                    quarantined.append(QuarantinedOffer(
                        raw_id=offer_id_for_log,
                        source=getattr(offer, "source", "unknown"),
                        reason="unresolved_server",
                        raw_title=getattr(offer, "raw_title", "") or getattr(offer, "display_server", ""),
                        price=getattr(offer, "price_per_1k", 0.0),
                        details=(
                            f"server_name={offer.server_name!r} "
                            f"display_server={offer.display_server!r}"
                        ),
                    ))
                    continue

                # ── Degraded mode: cache unavailable → pass through ──────────
                # Reconstruct display_server from raw_title (G2G) so the offer
                # is visible in the sidebar.  FunPay already has display_server
                # from HTML parsing.  If reconstruction fails → quarantine even
                # in degraded mode (we have nothing useful to show).
                if not _reconstruct_display_server_from_raw_title(offer):
                    quarantined.append(QuarantinedOffer(
                        raw_id=offer_id_for_log,
                        source=getattr(offer, "source", "unknown"),
                        reason="unresolved_server",
                        raw_title=getattr(offer, "raw_title", "") or getattr(offer, "display_server", ""),
                        price=getattr(offer, "price_per_1k", 0.0),
                        details=(
                            f"degraded_mode=True "
                            f"server_name={offer.server_name!r} "
                            f"display_server={offer.display_server!r}"
                        ),
                    ))
                    continue
                # Offer passes through with server_id=None but valid
                # display_server — skip canonicalization and is_active check
                # (both require server_id).

            # ── Step 3–4: canonicalize / is_active (only with server_id)
            if offer.server_id is not None:
                # Step 3: canonicalize from registry
                # Version, region, realm_type, name — ALWAYS from canonical
                # servers table, NEVER from the raw source title.
                server_data = get_server_data(offer.server_id)
                if server_data is not None:
                    _apply_canonical(offer, server_data)

                # Step 4: is_active check → quarantine deprecated versions
                # Inactive servers (e.g. Season of Mastery) are resolved and
                # canonicalised (so the quarantine log shows the correct name)
                # but then quarantined so they never reach the live cache.
                if server_data is not None and not server_data.get("is_active", True):
                    quarantined.append(QuarantinedOffer(
                        raw_id=offer_id_for_log,
                        source=getattr(offer, "source", "unknown"),
                        reason="deprecated_version",
                        raw_title=getattr(offer, "raw_title", "") or getattr(offer, "display_server", ""),
                        price=getattr(offer, "price_per_1k", 0.0),
                        details=(
                            f"server={offer.server_name!r} "
                            f"version={server_data.get('version')!r}"
                        ),
                    ))
                    continue

            # ── Step 5: dedup by (source, offer_id) ──────────────────────────
            dedup_key = f"{offer.source}:{offer.id}"
            if dedup_key in seen_ids:
                logger.debug(
                    "normalize: duplicate offer dropped source=%s offer_id=%s",
                    offer.source, offer.id,
                )
                continue
            seen_ids.add(dedup_key)

            normalized.append(offer)

        except Exception:
            logger.exception(
                "normalize_pipeline: unexpected error offer_id=%s", offer_id_for_log
            )
            quarantined.append(QuarantinedOffer(
                raw_id=offer_id_for_log,
                source=getattr(offer, "source", "unknown"),
                reason="pipeline_exception",
                raw_title=(
                    getattr(offer, "raw_title", "")
                    or getattr(offer, "display_server", "")
                ),
                price=getattr(offer, "price_per_1k", 0.0),
            ))

    logger.debug(
        "normalize_pipeline: in=%d  ok=%d  quarantined=%d",
        len(offers), len(normalized), len(quarantined),
    )
    return normalized, quarantined
