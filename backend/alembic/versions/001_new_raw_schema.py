"""New raw-price schema: servers, server_aliases, price_snapshots (raw),
server_price_index, server_price_history.
Legacy tables renamed to *_legacy suffix.

Revision ID: 001
Revises:
Create Date: 2026-04-06
"""
from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Rename legacy tables (keep data, just archive them) ───────────────
    # Only rename if they exist — idempotent-safe via DO block.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_name = 'price_index_snapshots'
                         AND table_schema = 'public') THEN
                ALTER TABLE price_index_snapshots
                    RENAME TO price_index_snapshots_legacy;
            END IF;

            IF EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_name = 'price_snapshots'
                         AND table_schema = 'public') THEN
                ALTER TABLE price_snapshots
                    RENAME TO price_snapshots_legacy;
            END IF;
        END
        $$;
    """)

    # ── 2. Canonical server registry ─────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS servers (
            id         SERIAL PRIMARY KEY,
            name       TEXT   NOT NULL,
            region     TEXT   NOT NULL,   -- 'EU' | 'US' | 'OCE' | 'KR' | 'TW'
            version    TEXT   NOT NULL,   -- 'Anniversary' | 'Seasonal' | 'Classic Era' | 'Classic'
            is_active  BOOLEAN NOT NULL DEFAULT TRUE,
            UNIQUE (name, region, version)
        );

        CREATE INDEX IF NOT EXISTS idx_servers_region_version
            ON servers (region, version);
    """)

    # ── 3. Server aliases ─────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS server_aliases (
            id         SERIAL PRIMARY KEY,
            server_id  INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
            alias      TEXT    NOT NULL UNIQUE,
            source     TEXT,              -- 'g2g' | 'funpay' | NULL (both)
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_server_aliases_alias
            ON server_aliases (alias);

        CREATE INDEX IF NOT EXISTS idx_server_aliases_server_id
            ON server_aliases (server_id);
    """)

    # ── 4. Raw price snapshots (new, no computed fields) ─────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS price_snapshots (
            id              BIGSERIAL PRIMARY KEY,
            source          TEXT        NOT NULL,           -- 'g2g' | 'funpay'
            offer_id        TEXT        NOT NULL,           -- original source offer id
            server_id       INTEGER     REFERENCES servers(id),
            faction         TEXT        NOT NULL,           -- 'Alliance' | 'Horde'
            raw_price       NUMERIC(18,8) NOT NULL,         -- exact price as received
            raw_price_unit  TEXT        NOT NULL,           -- 'per_unit' | 'per_lot'
            lot_size        INTEGER     NOT NULL DEFAULT 1, -- gold amount in lot (FunPay)
            currency        TEXT        NOT NULL DEFAULT 'USD',
            seller          TEXT,
            offer_url       TEXT,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_psnap_server_faction_fetched
            ON price_snapshots (server_id, faction, fetched_at DESC);

        CREATE INDEX IF NOT EXISTS idx_psnap_source_fetched
            ON price_snapshots (source, fetched_at DESC);

        CREATE INDEX IF NOT EXISTS idx_psnap_fetched
            ON price_snapshots (fetched_at DESC);
    """)

    # ── 5. Price index snapshots (replaces price_index_snapshots) ─────────────
    #       Stores the computed index per server+faction at a point in time.
    op.execute("""
        CREATE TABLE IF NOT EXISTS price_index_snapshots (
            id            BIGSERIAL     PRIMARY KEY,
            ts            TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            server        VARCHAR(100)  NOT NULL,   -- display_server: "(EU) Anniversary"
            faction       VARCHAR(20)   NOT NULL,   -- 'Alliance' | 'Horde' | 'All'
            server_id     INTEGER       REFERENCES servers(id),
            index_price   NUMERIC(12,6) NOT NULL,   -- VW-Median per unit (price per 1 gold)
            vwap          NUMERIC(12,6),
            best_ask      NUMERIC(12,6),
            price_min     NUMERIC(12,6),
            price_max     NUMERIC(12,6),
            offer_count   SMALLINT      NOT NULL DEFAULT 0,
            total_volume  BIGINT        NOT NULL DEFAULT 0,
            sources       TEXT[]        NOT NULL DEFAULT '{}',
            source_count  SMALLINT      NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_pis_server_faction_ts
            ON price_index_snapshots (server, faction, ts DESC);

        CREATE INDEX IF NOT EXISTS idx_pis_server_id_faction_ts
            ON price_index_snapshots (server_id, faction, ts DESC);

        CREATE INDEX IF NOT EXISTS idx_pis_ts
            ON price_index_snapshots (ts DESC);
    """)

    # ── 6. Server price index (current per-server index) ─────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS server_price_index (
            id          BIGSERIAL PRIMARY KEY,
            server_id   INTEGER   NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
            faction     TEXT      NOT NULL,           -- 'Alliance' | 'Horde'
            computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            index_price NUMERIC(12,6) NOT NULL,       -- simple mean of top-N, price per unit
            sample_size INTEGER,
            min_price   NUMERIC(12,6),
            max_price   NUMERIC(12,6),
            UNIQUE (server_id, faction)               -- upsert on update
        );

        CREATE INDEX IF NOT EXISTS idx_spi_server_faction
            ON server_price_index (server_id, faction);
    """)

    # ── 7. Server price history ───────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS server_price_history (
            id          BIGSERIAL PRIMARY KEY,
            server_id   INTEGER   NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
            faction     TEXT      NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            index_price NUMERIC(12,6) NOT NULL,
            sample_size INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_sph_server_faction_ts
            ON server_price_history (server_id, faction, recorded_at DESC);

        -- Partial index to quickly fetch latest 1000 rows per server+faction
        CREATE INDEX IF NOT EXISTS idx_sph_server_faction_id
            ON server_price_history (server_id, faction, id DESC);
    """)


def downgrade() -> None:
    # Drop new tables
    op.execute("""
        DROP TABLE IF EXISTS server_price_history CASCADE;
        DROP TABLE IF EXISTS server_price_index CASCADE;
        DROP TABLE IF EXISTS price_index_snapshots CASCADE;
        DROP TABLE IF EXISTS price_snapshots CASCADE;
        DROP TABLE IF EXISTS server_aliases CASCADE;
        DROP TABLE IF EXISTS servers CASCADE;
    """)
    # Restore legacy tables
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_name = 'price_index_snapshots_legacy'
                         AND table_schema = 'public') THEN
                ALTER TABLE price_index_snapshots_legacy
                    RENAME TO price_index_snapshots;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_name = 'price_snapshots_legacy'
                         AND table_schema = 'public') THEN
                ALTER TABLE price_snapshots_legacy
                    RENAME TO price_snapshots;
            END IF;
        END
        $$;
    """)
