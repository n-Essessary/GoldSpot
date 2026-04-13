"""
service/tiered_snapshot_loop.py — background asyncio loop for tiered snapshots.

Schedule (all intervals measured from the *start* of the previous iteration):
  Every 60 s  — write_snapshot_1m for all servers with active offers in cache
  Every 5 min — downsample_1m→5m, 5m→1h, 1h→1d (parallel, gather)
  Every 6 h   — cleanup old rows from each tier (parallel, gather)

Graceful degradation:
  - Any DB error inside a helper is already caught and logged at WARNING by the
    helper itself.  The loop catches any remaining exceptions at the top level
    so a single bad iteration never terminates the task.
  - asyncio.gather(return_exceptions=True) is used for all parallel fan-outs so
    one slow or failing coroutine never blocks the others.
  - If DATABASE_URL is not set, get_pool() returns None and all helpers return
    immediately — the loop continues without writing.

Called once from main.py lifespan:
    asyncio.create_task(start_tiered_snapshot_loop())
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_WRITE_INTERVAL      = 60        # seconds between 1m snapshot writes
_DOWNSAMPLE_INTERVAL = 300       # 5 minutes between downsample runs
_CLEANUP_INTERVAL    = 21_600    # 6 hours between cleanup runs


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _write_1m_snapshots(now: datetime) -> None:
    """Compute per-server index prices from live cache and write to snapshots_1m.

    Iterates all unique (server_id, faction) pairs present in the in-memory
    offer cache, reuses compute_server_index (same algorithm as the sidebar),
    and fires all writes in parallel with gather(return_exceptions=True).
    """
    from db.tiered_snapshots import write_snapshot_1m
    from service.offers_service import compute_server_index, get_all_offers

    all_offers = get_all_offers()
    if not all_offers:
        return

    # Collect unique (server_id, faction) pairs — include synthetic "All" faction
    server_faction_pairs: set[tuple[int, str]] = set()
    for o in all_offers:
        if o.server_id is not None:
            server_faction_pairs.add((o.server_id, o.faction))
            server_faction_pairs.add((o.server_id, "All"))

    write_tasks = []
    for server_id, faction in server_faction_pairs:
        result = compute_server_index(server_id, faction, all_offers)
        if result is not None:
            write_tasks.append(
                write_snapshot_1m(
                    server_id=server_id,
                    faction=faction,
                    index_price=result["index_price"],
                    best_ask=result["best_ask"],
                    sample_size=result["sample_size"],
                    recorded_at=now,
                )
            )

    if not write_tasks:
        return

    results = await asyncio.gather(*write_tasks, return_exceptions=True)
    errors  = [r for r in results if isinstance(r, Exception)]
    if errors:
        logger.warning(
            "_write_1m_snapshots: %d/%d writes raised exceptions",
            len(errors), len(write_tasks),
        )
    else:
        logger.debug(
            "_write_1m_snapshots: wrote %d snapshots at %s",
            len(write_tasks), now.isoformat(),
        )


async def _run_downsampling() -> None:
    """Downsample 1m→5m, 5m→1h, 1h→1d in parallel."""
    from db.tiered_snapshots import (
        downsample_1h_to_1d,
        downsample_1m_to_5m,
        downsample_5m_to_1h,
    )

    results = await asyncio.gather(
        downsample_1m_to_5m(),
        downsample_5m_to_1h(),
        downsample_1h_to_1d(),
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, Exception)]
    if errors:
        logger.warning("_run_downsampling: %d step(s) raised exceptions: %s", len(errors), errors)
    else:
        logger.debug("_run_downsampling: all steps completed")


async def _run_cleanup() -> None:
    """Delete expired rows from all rolling-window tiers in parallel."""
    from db.tiered_snapshots import (
        cleanup_snapshots_1h,
        cleanup_snapshots_1m,
        cleanup_snapshots_5m,
    )

    results = await asyncio.gather(
        cleanup_snapshots_1m(),
        cleanup_snapshots_5m(),
        cleanup_snapshots_1h(),
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, Exception)]
    if errors:
        logger.warning("_run_cleanup: %d step(s) raised exceptions: %s", len(errors), errors)
    else:
        logger.debug("_run_cleanup: all tiers cleaned up")


# ── Entry point ───────────────────────────────────────────────────────────────

async def start_tiered_snapshot_loop() -> None:
    """Background coroutine — runs forever, never raises.

    Designed to be launched via asyncio.create_task() in the FastAPI lifespan
    after start_background_parsers() so the offer cache has time to warm up
    before the first write attempt.
    """
    logger.info("Tiered snapshot loop started (write=%ds, downsample=%ds, cleanup=%ds)",
                _WRITE_INTERVAL, _DOWNSAMPLE_INTERVAL, _CLEANUP_INTERVAL)

    last_downsample_at: datetime | None = None
    last_cleanup_at:    datetime | None = None

    while True:
        try:
            now = datetime.now(timezone.utc)

            # ── Every 60 s: write 1-minute snapshots ─────────────────────────
            await _write_1m_snapshots(now)

            # ── Every 5 min: downsample all tiers ────────────────────────────
            if (
                last_downsample_at is None
                or (now - last_downsample_at).total_seconds() >= _DOWNSAMPLE_INTERVAL
            ):
                await _run_downsampling()
                last_downsample_at = now

            # ── Every 6 h: purge expired rows ────────────────────────────────
            if (
                last_cleanup_at is None
                or (now - last_cleanup_at).total_seconds() >= _CLEANUP_INTERVAL
            ):
                await _run_cleanup()
                last_cleanup_at = now

        except Exception:
            # Belt-and-suspenders: individual helpers already swallow their own
            # exceptions, but we guard here too so nothing can kill the loop.
            logger.warning("Tiered snapshot loop: unexpected error in iteration", exc_info=True)

        await asyncio.sleep(_WRITE_INTERVAL)
