"""
service/price_profiles.py — In-memory price profiles per canonical server.

Each profile stores (p25, median, p75) of price_per_1k computed from the
current live offers for a given server_id × faction pair.

Design contract:
  • Profiles are purely advisory — they never create or mutate server records.
  • Recomputed from the in-memory offer cache after every parse cycle.
  • On cold start the profile store is empty; price validation is skipped
    (returns True) for any offer without a profile → safe degradation.
  • Deterministic: same input offers → same profiles → same validation results.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from api.schemas import Offer

logger = logging.getLogger(__name__)

# Profile is considered stale after this many seconds with no update.
# In practice update_profiles() is called on every parse cycle (~30–60 s),
# so staleness only matters on startup before the first cycle completes.
_STALE_AFTER = 600.0  # 10 minutes

# Minimum number of offers required to build a useful profile.
_MIN_SAMPLE = 3


@dataclass(frozen=True)
class PriceProfile:
    """Price distribution for a server_id + faction combination."""

    server_id: int
    faction: str          # "Horde" | "Alliance" | "All"
    p25: float            # 25th percentile of price_per_1k
    median: float         # 50th percentile
    p75: float            # 75th percentile
    sample_size: int
    computed_at: float    # time.monotonic() timestamp


# ── Module-level state ────────────────────────────────────────────────────────
# _profiles[server_id][faction] = PriceProfile
_profiles: dict[int, dict[str, PriceProfile]] = {}
_last_refreshed: float = 0.0


# ── Internal helpers ──────────────────────────────────────────────────────────

def _percentile(sorted_values: list[float], pct: float) -> float:
    """Return the pct-th percentile (0.0–1.0) of a pre-sorted list."""
    if not sorted_values:
        return 0.0
    idx = max(0, min(len(sorted_values) - 1, int(len(sorted_values) * pct)))
    return sorted_values[idx]


# ── Public API ────────────────────────────────────────────────────────────────

def update_profiles(offers: list["Offer"]) -> None:
    """
    Recompute all price profiles from the current offer list.

    Groups offers by (server_id, faction) — including an "All" aggregate.
    Only considers offers with a resolved server_id and positive price.
    Called by offers_service after each successful parse cycle.
    """
    global _profiles, _last_refreshed

    # Accumulate price_per_1k per (server_id, faction)
    groups: dict[tuple[int, str], list[float]] = {}

    for offer in offers:
        if offer.server_id is None or offer.price_per_1k <= 0:
            continue
        for faction in (offer.faction, "All"):
            key = (offer.server_id, faction)
            groups.setdefault(key, []).append(offer.price_per_1k)

    new_profiles: dict[int, dict[str, PriceProfile]] = {}
    now = time.monotonic()

    for (server_id, faction), prices in groups.items():
        if len(prices) < _MIN_SAMPLE:
            continue
        sorted_p = sorted(prices)
        profile = PriceProfile(
            server_id=server_id,
            faction=faction,
            p25=_percentile(sorted_p, 0.25),
            median=_percentile(sorted_p, 0.50),
            p75=_percentile(sorted_p, 0.75),
            sample_size=len(sorted_p),
            computed_at=now,
        )
        new_profiles.setdefault(server_id, {})[faction] = profile

    _profiles = new_profiles
    _last_refreshed = now
    logger.debug(
        "price_profiles: updated %d servers, %d (server, faction) pairs",
        len(new_profiles),
        sum(len(v) for v in new_profiles.values()),
    )


def get_profile(server_id: int, faction: str = "All") -> Optional[PriceProfile]:
    """
    Return the price profile for server_id + faction.

    Falls back to "All" if the specific faction has no profile.
    Returns None if no profile exists (e.g. cold start or too few offers).
    """
    server_profiles = _profiles.get(server_id)
    if server_profiles is None:
        return None
    profile = server_profiles.get(faction)
    if profile is None and faction != "All":
        profile = server_profiles.get("All")
    return profile


def is_stale() -> bool:
    """True if profiles haven't been updated recently."""
    return time.monotonic() - _last_refreshed > _STALE_AFTER


def get_stats() -> dict:
    """Return summary stats for the /admin/price-profiles diagnostic endpoint."""
    return {
        "server_count": len(_profiles),
        "total_profiles": sum(len(v) for v in _profiles.values()),
        "is_stale": is_stale(),
        "last_refreshed_ago_s": round(time.monotonic() - _last_refreshed, 1),
    }
