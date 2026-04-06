-- ─────────────────────────────────────────────────────────────────────────────
-- GoldSpot DB schema — CANONICAL REFERENCE (do NOT run manually)
-- Apply via: alembic upgrade head
-- ─────────────────────────────────────────────────────────────────────────────

-- ── servers — canonical server registry ──────────────────────────────────────
-- Source of truth for all realm names, region, and version.
-- Seeded by migration 002_seed_servers.
CREATE TABLE IF NOT EXISTS servers (
    id        SERIAL  PRIMARY KEY,
    name      TEXT    NOT NULL,
    region    TEXT    NOT NULL,   -- 'EU' | 'US' | 'OCE' | 'KR' | 'TW'
    version   TEXT    NOT NULL,   -- 'Anniversary' | 'Season of Discovery' | 'Classic Era' | 'Classic'
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (name, region, version)
);

CREATE INDEX IF NOT EXISTS idx_servers_region_version ON servers (region, version);

-- ── server_aliases — raw-string → server_id mapping ──────────────────────────
-- Populated by: migration seed + /admin/register-alias endpoint.
-- Used by server_resolver.py for exact alias lookups.
CREATE TABLE IF NOT EXISTS server_aliases (
    id         SERIAL  PRIMARY KEY,
    server_id  INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    alias      TEXT    NOT NULL UNIQUE,   -- raw string as seen in parser output
    source     TEXT,                      -- 'g2g' | 'funpay' | NULL (both)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_server_aliases_alias     ON server_aliases (alias);
CREATE INDEX IF NOT EXISTS idx_server_aliases_server_id ON server_aliases (server_id);

-- ── price_snapshots — raw offer prices (Task 1) ───────────────────────────────
-- NEVER stores computed values (price_per_1k, index_price, etc.).
-- All computations happen at read-time.
--
-- raw_price meaning:
--   G2G:    unit_price_in_usd  (price per 1 gold unit)   raw_price_unit='per_unit'
--   FunPay: price for the lot  (e.g. 1000 gold for $3)   raw_price_unit='per_lot'
--
-- price_per_1k derivation at read-time:
--   per_unit: raw_price * 1000
--   per_lot:  (raw_price / lot_size) * 1000
CREATE TABLE IF NOT EXISTS price_snapshots (
    id             BIGSERIAL   PRIMARY KEY,
    source         TEXT        NOT NULL,           -- 'g2g' | 'funpay'
    offer_id       TEXT        NOT NULL,           -- source offer id
    server_id      INTEGER     REFERENCES servers(id),
    faction        TEXT        NOT NULL,           -- 'Alliance' | 'Horde'
    raw_price      NUMERIC(18,8) NOT NULL,
    raw_price_unit TEXT        NOT NULL,           -- 'per_unit' | 'per_lot'
    lot_size       INTEGER     NOT NULL DEFAULT 1,
    currency       TEXT        NOT NULL DEFAULT 'USD',
    seller         TEXT,
    offer_url      TEXT,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_psnap_server_faction_fetched ON price_snapshots (server_id, faction, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_psnap_source_fetched         ON price_snapshots (source, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_psnap_fetched                ON price_snapshots (fetched_at DESC);

-- ── price_index_snapshots — group-level OHLC history (legacy) ────────────────
-- Used by /price-history/ohlc endpoint (grouped by display_server, e.g. "(EU) Anniversary").
-- index_price is VW-Median price per 1k gold (NOT per unit).
-- Kept for backward compat — new per-server index lives in server_price_index.
CREATE TABLE IF NOT EXISTS price_index_snapshots (
    id           BIGSERIAL     PRIMARY KEY,
    ts           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    server       VARCHAR(100)  NOT NULL,   -- display_server: "(EU) Anniversary"
    faction      VARCHAR(20)   NOT NULL,   -- 'Alliance' | 'Horde' | 'All'
    server_id    INTEGER       REFERENCES servers(id),
    index_price  NUMERIC(12,6) NOT NULL,
    vwap         NUMERIC(12,6),
    best_ask     NUMERIC(12,6),
    price_min    NUMERIC(12,6),
    price_max    NUMERIC(12,6),
    offer_count  SMALLINT      NOT NULL DEFAULT 0,
    total_volume BIGINT        NOT NULL DEFAULT 0,
    sources      TEXT[]        NOT NULL DEFAULT '{}',
    source_count SMALLINT      NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pis_server_faction_ts    ON price_index_snapshots (server, faction, ts DESC);
CREATE INDEX IF NOT EXISTS idx_pis_server_id_faction_ts ON price_index_snapshots (server_id, faction, ts DESC);
CREATE INDEX IF NOT EXISTS idx_pis_ts                   ON price_index_snapshots (ts DESC);

-- ── server_price_index — current per-server index (Task 4) ───────────────────
-- One row per (server_id, faction). UPSERT on each compute cycle.
-- index_price is price per unit (per 1 gold), NOT per 1k.
CREATE TABLE IF NOT EXISTS server_price_index (
    id          BIGSERIAL PRIMARY KEY,
    server_id   INTEGER   NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    faction     TEXT      NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    index_price NUMERIC(12,6) NOT NULL,   -- mean of top-10 cheapest, price per unit
    sample_size INTEGER,
    min_price   NUMERIC(12,6),
    max_price   NUMERIC(12,6),
    UNIQUE (server_id, faction)
);

CREATE INDEX IF NOT EXISTS idx_spi_server_faction ON server_price_index (server_id, faction);

-- ── server_price_history — per-server price history (Task 4) ─────────────────
-- Appended each compute cycle. Pruned to last 1000 rows per server+faction.
-- index_price is price per unit (per 1 gold).
CREATE TABLE IF NOT EXISTS server_price_history (
    id          BIGSERIAL PRIMARY KEY,
    server_id   INTEGER   NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    faction     TEXT      NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    index_price NUMERIC(12,6) NOT NULL,
    sample_size INTEGER
);

CREATE INDEX IF NOT EXISTS idx_sph_server_faction_ts ON server_price_history (server_id, faction, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_sph_server_faction_id ON server_price_history (server_id, faction, id DESC);

-- ── Legacy tables ─────────────────────────────────────────────────────────────
-- After migration 001, old tables renamed to *_legacy.
-- Do NOT write new data here. Remove after data migration is confirmed complete.
--
--   price_index_snapshots_legacy  — old group-level snapshots
--   price_snapshots_legacy        — old computed-price snapshots (contains price_per_1k)
