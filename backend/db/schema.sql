-- ─────────────────────────────────────────────────────────────────────────────
-- price_index_snapshots
-- Агрегированные индексные снимки: один ряд на server+faction за момент времени.
-- Записывается только при изменении цены > 0.5% — плотность точек пропорциональна
-- волатильности. Хранит IndexPrice (VW-Median, VWAP, best_ask).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS price_index_snapshots (
    id            BIGSERIAL     PRIMARY KEY,
    ts            TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    server        VARCHAR(100)  NOT NULL,   -- "(EU) Anniversary"
    faction       VARCHAR(20)   NOT NULL,   -- 'Alliance' | 'Horde' | 'All'
    index_price   NUMERIC(10,4) NOT NULL,   -- VW-Median — основная линия
    vwap          NUMERIC(10,4),            -- Volume-Weighted Avg Price
    best_ask      NUMERIC(10,4),            -- реальная цена покупки прямо сейчас
    price_min     NUMERIC(10,4),
    price_max     NUMERIC(10,4),
    offer_count   SMALLINT      NOT NULL DEFAULT 0,
    total_volume  BIGINT        NOT NULL DEFAULT 0,
    sources       TEXT[]        NOT NULL DEFAULT '{}',
    source_count  SMALLINT      NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pis_server_faction_ts
    ON price_index_snapshots (server, faction, ts DESC);

CREATE INDEX IF NOT EXISTS idx_pis_ts
    ON price_index_snapshots (ts DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- price_snapshots (устаревшая таблица, оставлена для совместимости)
-- Если она уже существует в Railway — не трогаем. Новые данные туда не пишутся.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS price_snapshots (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    source       VARCHAR(20)   NOT NULL,
    server       VARCHAR(100)  NOT NULL,
    server_name  VARCHAR(100)  NOT NULL DEFAULT '',
    faction      VARCHAR(20)   NOT NULL,
    price_per_1k NUMERIC(10,4) NOT NULL,
    amount_gold  BIGINT        NOT NULL,
    seller       VARCHAR(100)  NOT NULL,
    offer_url    TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_server_ts
    ON price_snapshots (server, ts DESC);

CREATE INDEX IF NOT EXISTS idx_snapshots_ts
    ON price_snapshots (ts DESC);
