-- Price history snapshots table.
-- One row per offer per cache update cycle (~every 30-60 seconds).
-- Designed to hold at least 1 year of data with automatic cleanup.

CREATE TABLE IF NOT EXISTS price_snapshots (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    source       VARCHAR(20)  NOT NULL,   -- 'funpay' | 'g2g'
    server       VARCHAR(100) NOT NULL,   -- display_server: "(EU) Anniversary"
    server_name  VARCHAR(100) NOT NULL DEFAULT '',  -- realm: "Spineshatter"
    faction      VARCHAR(20)  NOT NULL,   -- 'Alliance' | 'Horde'
    price_per_1k NUMERIC(10,4) NOT NULL,
    amount_gold  BIGINT        NOT NULL,
    seller       VARCHAR(100) NOT NULL,
    offer_url    TEXT
);

-- Primary query pattern: get history for a specific server, ordered by time
CREATE INDEX IF NOT EXISTS idx_snapshots_server_ts
    ON price_snapshots (server, ts DESC);

-- Secondary pattern: recent data across all servers (cleanup, admin)
CREATE INDEX IF NOT EXISTS idx_snapshots_ts
    ON price_snapshots (ts DESC);

-- Partial index for Alliance/Horde faction queries
CREATE INDEX IF NOT EXISTS idx_snapshots_server_faction_ts
    ON price_snapshots (server, faction, ts DESC);
