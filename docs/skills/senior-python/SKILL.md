---
name: senior-python
description: "Production-grade Python — async patterns, error handling, code quality, and GoldSpot-specific invariants."
---

# Senior Python Engineer

## Core Responsibility

Write production-grade Python that is readable, stable, and non-blocking.

---

## Async Rules (Critical)

- All I/O must be `await`ed — no `time.sleep()`, no `requests` library
- `asyncio.gather(*tasks, return_exceptions=True)` for parallel work — always check returned exceptions
- `asyncio.create_task()` for fire-and-forget — wrap body in try/except, log failures
- `asyncio.to_thread()` for CPU-bound work (BeautifulSoup parsing)
- `asyncio.Semaphore(n)` for rate-limited concurrent fetches (G2G Phase 2 uses 20)
- Never call `asyncio.run()` inside a running event loop

---

## Error Handling Standards

```python
# Good — explicit, non-silent
try:
    result = await some_operation()
except httpx.TimeoutException:
    logger.error("timeout fetching %s", url)
    return []
except Exception:
    logger.exception("unexpected failure in %s", context)
    return []

# Bad — silent swallow
try:
    ...
except Exception:
    pass
```

- `ERROR` for network failures
- `WARNING` for empty results, degraded states, unknown versions/servers
- `DEBUG` for per-offer parse failures (high volume)
- Never bare `except:` — always `except Exception:`

---

## Pydantic v2 Patterns

- `model_validator(mode="after")` for derived field computation (`price_per_1k`)
- `field_validator(mode="before")` for input normalization
- `field_serializer` for datetime → ISO string
- `ConfigDict(from_attributes=True)` for ORM-like row mapping

---

## Dataclass vs Pydantic

- `Offer` → Pydantic (validation at external boundary)
- Internal parser types (`G2GOffer`, `G2GRegion`) → dataclass (no external validation needed)
- Computed aggregates (`IndexPrice`) → dataclass (never serialized to API)

---

## Code Quality Rules

- Functions < 40 lines; extract helpers aggressively
- Named constants for magic numbers (`_QUARANTINE_MAX`, `_INDEX_TOP_N`, `_SEMAPHORE_LIMIT`)
- One responsibility per function
- Type hints on all public functions
- `Optional[X]` → `X | None` (Python 3.10+)

---

## Performance Patterns

```python
# Good — dict lookup O(1)
group_min: dict[str, float] = {}
for offer in offers:
    cur = group_min.get(ds)
    if cur is None or offer.price_per_1k < cur:
        group_min[ds] = offer.price_per_1k

# Bad — O(n²)
for ds in display_servers:
    min_price = min(o.price_per_1k for o in offers if o.display_server == ds)
```

- `set` for deduplication (not `list` + `in` check)
- DB writes in batches of 50 via `gather`
- Cache expensive lookups (alias cache, index cache)

---

## GoldSpot Invariants — Never Remove

- `_flatten_param()` in `db/writer.py`
- `COALESCE(sources, ARRAY[]::text[])` SQL guard
- `_snapshot_running` flag (concurrent snapshot prevention)
- `_cache_initialized[source]` cold-start guard
- `raw_price` / `price_per_1k` derivation contract
- Background loop structure (no blocking calls in loops)

---

## Refactoring Rules

- Extract only when duplication appears 3+ times
- Rename only with full grep across codebase
- Never rename API-facing fields
- Preserve backward-compat paths during migration periods
