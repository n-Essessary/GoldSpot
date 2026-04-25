---
name: price-profiles
description: "In-memory price profiles per canonical entity — used for validation, anomaly detection, and price-assisted entity rerouting."
---

# Price Profiles

## Location

`service/price_profiles.py` — pure in-memory, rebuilt from cache after every parse cycle.

---

## Purpose

1. **Price validation** — reject offers with prices outside plausible range (parser bugs, currency errors)
2. **Anomaly flagging** — identify suspicious offers without quarantining them
3. **Price-assisted rerouting** — help resolve ambiguous entity titles by price proximity

---

## Profile Structure

```python
@dataclass
class PriceProfile:
    entity_id:   int      # canonical registry ID (server_id for WoW)
    faction:     str      # "All" | "Horde" | "Alliance" (WoW); extend for other games
    p10:         float    # 10th percentile unit_price (in comparable unit)
    p90:         float    # 90th percentile unit_price
    median:      float    # median unit_price
    sample_size: int
    updated_at:  datetime
```

Key: `(entity_id, faction)`. Requires minimum 3 offers per key to build a profile.

For currency assets, profile values are in `price_per_1k` space.
For other asset types (items, accounts), profile values are in `unit_price` space.

---

## Update Cycle

Profiles are rebuilt from the offer cache after every parse cycle:

```python
# Called in _do_snapshot_all_servers()
from service.price_profiles import update_profiles
update_profiles(all_offers)
```

- Groups offers by `(entity_id, faction)` — requires `entity_id` not None
- Atomic per-key replacement — no partial updates
- Pure in-memory — not persisted to DB
- Profiles lag by one cycle — intentional and acceptable

---

## Price Validation Logic

```python
def is_price_valid(offer: Offer, profiles: dict) -> tuple[bool, str]:
    if offer.server_id is None:
        return True, ""   # no entity_id → no profile possible
    
    key = (offer.server_id, offer.faction)
    profile = profiles.get(key) or profiles.get((offer.server_id, "All"))
    
    if profile is None:
        return True, ""   # cold start — no profile yet
    
    lower_bound = profile.p10 * 0.1   # 10× below p10
    upper_bound = profile.p90 * 10.0  # 10× above p90
    
    if not (lower_bound <= offer.price_per_1k <= upper_bound):
        return False, f"price_out_of_range:{offer.price_per_1k:.4f}"
    
    return True, ""
```

Bounds are intentionally wide (10×) — catches only extreme errors (parser bugs, currency failures), not legitimate price spikes.

---

## Price-Assisted Rerouting

When `entity_id=None` but multiple candidate entities match the title, use price proximity:

```python
# Candidates for "Firemaw [EU]":
#   A: entity_id=42, profile.median=3.2
#   B: entity_id=71, profile.median=3.1
# offer.price_per_1k=3.15 → closer to B → reroute to entity_id=71
```

Apply only when:
- Exactly 2–3 candidates exist
- Price difference between candidates > 20%
- Offer price is within 30% of one candidate's median

Best-effort heuristic — do not apply when candidates are too close in price.

---

## Cold Start Behavior

- First cycle: no profiles → all offers pass validation
- After first cycle: profiles built → second cycle validates against them
- First cycle may accept some bad offers — they are caught on the second cycle
- Acceptable: data accuracy > startup speed

---

## `get_stats()` — Admin Diagnostic

```python
# GET /admin/price-profiles
{
    "entity_count": 47,
    "total_profiles": 94,       # entity × faction combinations
    "cold_start_ratio": 0.12,   # fraction with < 3 offers
    "oldest_profile_age_s": 62  # seconds since oldest update
}
```

---

## Rules

- NEVER store profiles in DB — always derived from live cache
- Validation bounds must be wide enough to avoid false positives on real price spikes
- Profile absence → accept offer, never reject
- Profile keys use `entity_id` (int), never `display_server` string (unstable)
- `update_profiles()` must be called BEFORE `normalize_offer_batch()` on the next cycle

---

## What Must NOT Happen

- Tight bounds (e.g. ±20%) — causes mass false quarantine on price spikes
- Blocking profile update (synchronous in-memory, no DB calls)
- Keys based on string labels instead of `entity_id`
- Price validation running before first profile update (cold start guard is mandatory)
