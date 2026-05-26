-- open_quant metadata schema
CREATE SCHEMA IF NOT EXISTS open_quant;
SET search_path TO open_quant, public;

CREATE TABLE IF NOT EXISTS data_sync_state (
    dataset      TEXT PRIMARY KEY,
    last_synced  TIMESTAMP WITH TIME ZONE NOT NULL,
    rows_total   BIGINT NOT NULL DEFAULT 0,
    meta         JSONB
);

CREATE TABLE IF NOT EXISTS strategy_registry (
    name         TEXT PRIMARY KEY,
    version      TEXT NOT NULL,
    config       JSONB NOT NULL,
    enabled      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS orders (
    id           BIGSERIAL PRIMARY KEY,
    client_id    TEXT UNIQUE NOT NULL,
    broker_id    TEXT,
    strategy     TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    side         TEXT NOT NULL,
    qty          BIGINT NOT NULL,
    price        NUMERIC(18, 4),
    order_type   TEXT NOT NULL,
    status       TEXT NOT NULL,
    filled_qty   BIGINT NOT NULL DEFAULT 0,
    avg_price    NUMERIC(18, 4),
    created_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    meta         JSONB
);
CREATE INDEX IF NOT EXISTS idx_orders_strategy_status ON orders (strategy, status);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders (created_at DESC);

CREATE TABLE IF NOT EXISTS positions (
    snapshot_date DATE NOT NULL,
    account       TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    qty           BIGINT NOT NULL,
    avg_cost      NUMERIC(18, 4),
    market_value  NUMERIC(18, 2),
    PRIMARY KEY (snapshot_date, account, symbol)
);

CREATE TABLE IF NOT EXISTS pnl_daily (
    trade_date    DATE NOT NULL,
    strategy      TEXT NOT NULL,
    nav           NUMERIC(18, 6) NOT NULL,
    daily_return  NUMERIC(10, 6),
    turnover      NUMERIC(10, 6),
    drawdown      NUMERIC(10, 6),
    meta          JSONB,
    PRIMARY KEY (trade_date, strategy)
);

CREATE TABLE IF NOT EXISTS risk_events (
    id           BIGSERIAL PRIMARY KEY,
    event_time   TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    severity     TEXT NOT NULL,
    rule         TEXT NOT NULL,
    detail       JSONB NOT NULL,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_risk_events_time ON risk_events (event_time DESC);
