"""
service/normalize_pipeline.py — Deterministic offer normalization pipeline.

Pipeline for every raw offer coming out of a parser:

    RAW OFFER → [1. validate] → [2. resolve] → [3. canonicalize]
              → [4. price validate / reroute] → [5. dedup] → NORMALIZED OFFER
                                                             or QUARANTINE

Guarantees:
  1. Deterministic: same input + same DB state → same output, always.
  2. Version is taken from the canonical server registry (servers table),
     NEVER from the raw source title. If resolution fails, display_server
     keeps its parser-derived value (no silent corruption).
  3. Missing faction defaults to "Horde". Unknown faction is quarantined.
  4. Broken title (empty server, pipeline exception) → quarantine.
  5. Price validation is advisory only — never creates new server records.
     If an offer's price doesn't fit the resolved server's profile, we attempt
     to reroute to the same server_name + region with a different version.
     If no better match → offer is kept with the original resolution.
  6. Dedup by (source, offer_id) within each batch.

What this pipeline does NOT touch:
  • raw_price, raw_price_unit, lot_size, price_per_1k (parser contract)
  • Parser output beyond display_server / server_name / faction defaults
  • /offers response contract (OfferRow / OffersResponse)
  • Frontend-facing field names
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

# Price-rerouting bounds relative to profile median.
# Only used when a price profile exists; otherwise validation is skipped.
_PRICE_LOW_FACTOR  = 0.20   # < 20 % of median  → suspiciously cheap
_PRICE_HIGH_FACTOR = 5.00   # > 500 % of median → suspiciously expensive

# Source priority for breaking ties when two sources produce the same
# (server_name, region, version) after canonicalisation.
# Lower number = higher priority.
_SOURCE_PRIORITY: dict[str, int] = {
    "funpay": 0,
    "g2g":    1,
}


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

def _apply_canonical(offer: "Offer", server_data: dict) -> None:
    """
    Overwrite offer fields with canonical values from the servers table.

    server_data: {"id": int, "name": str, "region": str, "version": str}

    After this call the offer's display_server, server, and server_name
    are authoritative and sourced from the canonical registry.
    Mutates offer in-place (consistent with existing normalize helpers).
    """
    canonical_region  = server_data["region"]
    canonical_version = server_data["version"]
    canonical_name    = server_data["name"]

    new_display = f"({canonical_region}) {canonical_version}"

    offer.display_server = new_display
    offer.server         = new_display.lower()    # Offer.server is always lowercase
    offer.server_name    = canonical_name
    offer.server_id      = server_data["id"]


# ── Step 4: Price validation & rerouting ─────────────────────────────────────

def _price_fits_profile(price_per_1k: float, profile) -> bool:
    """
    True if price_per_1k falls within the acceptable range of the profile.
    True (skip) if profile is None — no data means no rejection.
    """
    if profile is None:
        return True
    low  = profile.median * _PRICE_LOW_FACTOR
    high = profile.median * _PRICE_HIGH_FACTOR
    return low <= price_per_1k <= high


async def _try_reroute(offer: "Offer", pool) -> bool:
    """
    Attempt price-assisted rerouting.

    If the offer's price doesn't fit the resolved server's profile, look for
    another server with the same name + region but a different version whose
    profile the price DOES fit. If found, canonicalize the offer to that
    server and return True. Otherwise return False (offer unchanged).

    Constraint: never creates new server records — only routes among existing ones.
    """
    from db.server_resolver import find_server_versions, get_server_data
    from service.price_profiles import get_profile

    if not offer.server_name or pool is None:
        return False

    # Extract region from current display_server
    m = re.match(r"^\(([A-Z]{2,})\)", (offer.display_server or "").upper())
    if not m:
        return False
    region = m.group(1)

    alternatives = await find_server_versions(offer.server_name, region, pool)

    for alt in alternatives:
        if alt["id"] == offer.server_id:
            continue  # skip current assignment

        profile = get_profile(alt["id"], "All")
        if profile is None:
            continue   # no profile data for this version yet

        if _price_fits_profile(offer.price_per_1k, profile):
            logger.debug(
                "normalize: price reroute offer_id=%s  %r→%r  server_id %s→%d",
                offer.id,
                offer.display_server,
                f"({alt['region']}) {alt['version']}",
                offer.server_id,
                alt["id"],
            )
            _apply_canonical(offer, alt)
            return True

    return False


# ── Alias key builders ────────────────────────────────────────────────────────

_DISPLAY_RE = re.compile(r"^\((?P<region>[A-Z]{2,})\)\s*(?P<version>.+)$")


def _build_alias_key(offer: "Offer") -> Optional[str]:
    """
    Construct the raw alias string used for server_aliases lookup.

    Format mirrors what is seeded into server_aliases by the admin / migration:
      G2G:    "Spineshatter [EU - Anniversary] - Horde"
      FunPay: "(EU) Anniversary - Spineshatter"

    Returns None if display_server cannot be parsed (offer will fall through
    to fuzzy resolve in server_resolver).
    """
    ds = (offer.display_server or "").strip()
    m = _DISPLAY_RE.match(ds.upper()) or _DISPLAY_RE.match(ds)
    if not m:
        return None

    region  = m.group("region").upper()
    # Preserve original casing of version from display_server (e.g. "Anniversary")
    version = ds[m.start("version"):].strip()

    if offer.source == "g2g" and offer.server_name:
        return f"{offer.server_name} [{region} - {version}] - {offer.faction}"

    if offer.source == "funpay" and offer.server_name:
        return f"({region}) {version} - {offer.server_name}"

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
        resolve_server,
        resolve_server_batch,
    )
    from service.price_profiles import get_profile

    normalized:  list["Offer"]          = []
    quarantined: list[QuarantinedOffer] = []
    seen_ids:    set[str]               = set()   # dedup: "source:offer_id"

    # ── Batch alias resolution (one DB round-trip for all offers) ─────────────
    resolve_keys = _collect_resolve_keys(offers)
    alias_map: dict[str, int] = {}
    if resolve_keys and pool is not None:
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
                    raw_title=getattr(offer, "display_server", ""),
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
                        # Fallback: single resolve (fuzzy parse → DB lookup)
                        server_id = await resolve_server(
                            alias_key, offer.source, pool
                        )
                    if server_id is not None:
                        offer.server_id = server_id

            # ── Step 3: canonicalize from registry ────────────────────────────
            # Version, region, name come from canonical servers table — NEVER
            # from the raw source title. Priority: registry > FunPay > G2G.
            if offer.server_id is not None:
                server_data = get_server_data(offer.server_id)
                if server_data is not None:
                    _apply_canonical(offer, server_data)

            # ── Step 4: price validation + optional rerouting ─────────────────
            # Only runs if server_id resolved — otherwise no profile to compare.
            # Price never creates servers; it only selects among existing ones.
            if offer.server_id is not None:
                profile = get_profile(offer.server_id, "All")
                if not _price_fits_profile(offer.price_per_1k, profile):
                    await _try_reroute(offer, pool)
                    # If reroute fails, offer keeps its original server — not quarantined.

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
                raw_title=getattr(offer, "display_server", ""),
                price=getattr(offer, "price_per_1k", 0.0),
            ))

    logger.debug(
        "normalize_pipeline: in=%d  ok=%d  quarantined=%d",
        len(offers), len(normalized), len(quarantined),
    )
    return normalized, quarantined
