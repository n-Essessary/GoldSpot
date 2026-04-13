"""Implement 4-tier rolling snapshot storage.

Replaces server_price_history_short and server_price_history with:
  snapshots_1m — 1-min resolution, 24h rolling  (live writes every parser cycle)
  snapshots_5m — 5-min resolution, 30d rolling  (downsampled from 1m)
  snapshots_1h — 1-hour resolution, 2y rolling  (downsampled from 5m)
  snapshots_1d — 1-day resolution, forever      (downsampled from 1h)

Steady-state storage: ~360 MB vs previous unbounded growth (~35 MB/day).

Migration path:
  server_price_history_short → snapshots_1m (last 24h), snapshots_5m (all, 5-min buckets),
                               snapshots_1h (all, 1h buckets)
  server_price_history       → snapshots_1h (merge, dedup by UNIQUE constraint)

After verifying row counts, old tables are dropped.
downgrade() recreates the old schema from tiered data.

Revision ID: 012
Revises: 011
Create Date: 2026-04-13
"""
import sqlalchemy as sa
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None

# All four tier tables share this identical schema (only the table name differs)
_TIER_TABLES = ("snapshots_1m", "snapshots_5m", "snapshots_1h", "snapshots_1d")


def _table_exists(conn, table_name: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables"
                "  WHERE table_schema = 'public' AND table_name = :t"
                ")"
            ),
            {"t": table_name},
        ).scalar()
    )


def upgrade() -> None:
    # ── 1. Create tier tables ─────────────────────────────────────────────────
    for tbl in _TIER_TABLES:
        op.execute(
            sa.text(
                f"""
                CREATE TABLE IF NOT EXISTS {tbl} (
                    id          BIGSERIAL PRIMARY KEY,
                    server_id   INTEGER NOT NULL REFERENCES servers(id),
                    faction     TEXT NOT NULL,
                    recorded_at TIMESTAMPTZ NOT NULL,
                    index_price DOUBLE PRECISION NOT NULL,
                    best_ask    DOUBLE PRECISION,
                    sample_size INTEGER,
                    UNIQUE (server_id, faction, recorded_at)
                )
                """
            )
        )
        op.execute(
            sa.text(
                f"""
                CREATE INDEX IF NOT EXISTS {tbl}_srv_fac_time_idx
                ON {tbl} (server_id, faction, recorded_at DESC)
                """
            )
        )

    conn = op.get_bind()

    has_short = _table_exists(conn, "server_price_history_short")
    has_long  = _table_exists(conn, "server_price_history")

    # ── 2. Migrate from server_price_history_short ───────────────────────────
    if has_short:
        # snapshots_1m: last 24h only (matches retention window)
        op.execute(
            sa.text(
                """
                INSERT INTO snapshots_1m
                    (server_id, faction, recorded_at, index_price, best_ask, sample_size)
                SELECT server_id, faction, recorded_at, index_price, best_ask, sample_size
                FROM server_price_history_short
                WHERE recorded_at > NOW() - INTERVAL '24 hours'
                ON CONFLICT (server_id, faction, recorded_at) DO NOTHING
                """
            )
        )

        # snapshots_5m: all historical data bucketed to 5-min averages
        op.execute(
            sa.text(
                """
                INSERT INTO snapshots_5m
                    (server_id, faction, recorded_at, index_price, best_ask, sample_size)
                SELECT
                    server_id,
                    faction,
                    date_trunc('hour', recorded_at)
                        + (EXTRACT(MINUTE FROM recorded_at)::int / 5)
                          * INTERVAL '5 minutes'         AS recorded_at,
                    AVG(index_price)                     AS index_price,
                    MIN(best_ask)                        AS best_ask,
                    MAX(sample_size)                     AS sample_size
                FROM server_price_history_short
                GROUP BY
                    server_id, faction,
                    date_trunc('hour', recorded_at)
                        + (EXTRACT(MINUTE FROM recorded_at)::int / 5)
                          * INTERVAL '5 minutes'
                ON CONFLICT (server_id, faction, recorded_at) DO NOTHING
                """
            )
        )

        # snapshots_1h: all historical data bucketed to 1-hour averages
        op.execute(
            sa.text(
                """
                INSERT INTO snapshots_1h
                    (server_id, faction, recorded_at, index_price, best_ask, sample_size)
                SELECT
                    server_id,
                    faction,
                    date_trunc('hour', recorded_at)      AS recorded_at,
                    AVG(index_price)                     AS index_price,
                    MIN(best_ask)                        AS best_ask,
                    MAX(sample_size)                     AS sample_size
                FROM server_price_history_short
                GROUP BY server_id, faction, date_trunc('hour', recorded_at)
                ON CONFLICT (server_id, faction, recorded_at) DO NOTHING
                """
            )
        )

    # ── 3. Migrate from server_price_history (long-term) ─────────────────────
    if has_long:
        # Merge into snapshots_1h; UNIQUE constraint handles dedup automatically
        op.execute(
            sa.text(
                """
                INSERT INTO snapshots_1h
                    (server_id, faction, recorded_at, index_price, best_ask, sample_size)
                SELECT
                    server_id,
                    faction,
                    date_trunc('hour', recorded_at)      AS recorded_at,
                    AVG(index_price)                     AS index_price,
                    MIN(best_ask)                        AS best_ask,
                    MAX(sample_size)                     AS sample_size
                FROM server_price_history
                GROUP BY server_id, faction, date_trunc('hour', recorded_at)
                ON CONFLICT (server_id, faction, recorded_at) DO NOTHING
                """
            )
        )

    # ── 4. Verify counts (assertion only fires if source had data) ────────────
    if has_short:
        src_count = conn.execute(
            sa.text("SELECT COUNT(*) FROM server_price_history_short")
        ).scalar() or 0

        if src_count > 0:
            for tbl in ("snapshots_1m", "snapshots_5m", "snapshots_1h"):
                tbl_count = conn.execute(
                    sa.text(f"SELECT COUNT(*) FROM {tbl}")
                ).scalar() or 0
                assert tbl_count > 0, (
                    f"{tbl} is empty after migration "
                    f"(source server_price_history_short had {src_count} rows)"
                )

    if has_long:
        src_long = conn.execute(
            sa.text("SELECT COUNT(*) FROM server_price_history")
        ).scalar() or 0
        if src_long > 0:
            h1_count = conn.execute(
                sa.text("SELECT COUNT(*) FROM snapshots_1h")
            ).scalar() or 0
            assert h1_count > 0, (
                f"snapshots_1h is empty after migration "
                f"(source server_price_history had {src_long} rows)"
            )

    # ── 5. Drop old tables ────────────────────────────────────────────────────
    if has_short:
        op.execute(sa.text("DROP TABLE server_price_history_short CASCADE"))
    if has_long:
        op.execute(sa.text("DROP TABLE server_price_history CASCADE"))


def downgrade() -> None:
    """Recreate old schema from tiered data, then drop tier tables.

    Note: downsampling means the 1m granularity in server_price_history_short
    cannot be fully reconstructed from snapshots_5m — only approximate data
    is restored. This is a best-effort rollback.
    """
    # ── Recreate server_price_history_short from snapshots_1m ────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS server_price_history_short (
                id          BIGSERIAL PRIMARY KEY,
                server_id   INTEGER NOT NULL REFERENCES servers(id),
                faction     TEXT NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL,
                index_price DOUBLE PRECISION NOT NULL,
                best_ask    DOUBLE PRECISION,
                vwap        DOUBLE PRECISION,
                sample_size INTEGER
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO server_price_history_short
                (server_id, faction, recorded_at, index_price, best_ask, sample_size)
            SELECT server_id, faction, recorded_at, index_price, best_ask, sample_size
            FROM snapshots_1m
            ON CONFLICT DO NOTHING
            """
        )
    )

    # ── Recreate server_price_history from snapshots_1h ──────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS server_price_history (
                id          BIGSERIAL PRIMARY KEY,
                server_id   INTEGER NOT NULL REFERENCES servers(id),
                faction     TEXT NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL,
                index_price DOUBLE PRECISION NOT NULL,
                best_ask    DOUBLE PRECISION,
                vwap        DOUBLE PRECISION,
                sample_size INTEGER
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO server_price_history
                (server_id, faction, recorded_at, index_price, best_ask, sample_size)
            SELECT server_id, faction, recorded_at, index_price, best_ask, sample_size
            FROM snapshots_1h
            ON CONFLICT DO NOTHING
            """
        )
    )

    # ── Drop all tier tables ──────────────────────────────────────────────────
    for tbl in reversed(_TIER_TABLES):   # reverse to avoid FK-related issues
        op.execute(sa.text(f"DROP TABLE IF EXISTS {tbl} CASCADE"))
