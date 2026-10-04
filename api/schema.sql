-- RASAD SQLite schema. Dates are ISO strings (YYYY-MM-DD); report timestamps are ISO-8601 UTC
-- (YYYY-MM-DDTHH:MMZ). The contract tables are posts, passes, reports, gate_verdicts, forecasts,
-- pass_status, plans, plan_items and audit; the others are supporting data.

PRAGMA foreign_keys = ON;

-- Build provenance: seed, weather source, row counts. Deterministic (no wall-clock values).
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE depots (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    altitude_m INTEGER NOT NULL
);

CREATE TABLE passes (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    altitude_m INTEGER NOT NULL
);

CREATE TABLE posts (
    id         TEXT PRIMARY KEY,
    formation  TEXT NOT NULL,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    altitude_m INTEGER NOT NULL,
    troops     INTEGER NOT NULL,
    depot      TEXT NOT NULL REFERENCES depots (id),
    via_pass   TEXT NOT NULL REFERENCES passes (id),
    access     TEXT NOT NULL            -- JSON array: truck | mule | heli
);

-- Daily weather per post and per pass. `source` is open-meteo or synthetic-climatology.
CREATE TABLE weather_daily (
    location_id TEXT NOT NULL,
    date        TEXT NOT NULL,
    t_mean_c    REAL NOT NULL,
    t_min_c     REAL NOT NULL,
    snowfall_cm REAL NOT NULL,
    precip_mm   REAL NOT NULL,
    source      TEXT NOT NULL,
    PRIMARY KEY (location_id, date)
) WITHOUT ROWID;

CREATE TABLE deliveries (
    delivery_id  TEXT PRIMARY KEY,
    post_id      TEXT NOT NULL REFERENCES posts (id),
    supply_class TEXT NOT NULL,
    date         TEXT NOT NULL,
    qty          INTEGER NOT NULL,
    mode         TEXT NOT NULL CHECK (mode IN ('truck', 'mule', 'heli'))
);
CREATE INDEX idx_deliveries_date ON deliveries (date);

-- Field reports. The JSON contract calls supply_class "class". `report_date` is the date part of
-- `ts`, kept as a plain column for indexing. `sig` is NULL until signing lands (Day 3).
CREATE TABLE reports (
    report_id    TEXT PRIMARY KEY,
    post_id      TEXT NOT NULL REFERENCES posts (id),
    ts           TEXT NOT NULL,
    report_date  TEXT NOT NULL,
    supply_class TEXT NOT NULL,
    opening      INTEGER NOT NULL,
    received     INTEGER NOT NULL,
    consumed     INTEGER NOT NULL,
    closing      INTEGER NOT NULL,
    nonce        TEXT NOT NULL,
    sig          TEXT
);
CREATE INDEX idx_reports_post_class_date ON reports (post_id, supply_class, report_date);
CREATE INDEX idx_reports_date ON reports (report_date);

CREATE TABLE gate_verdicts (
    report_id TEXT PRIMARY KEY REFERENCES reports (report_id),
    verdict   TEXT NOT NULL CHECK (verdict IN ('VERIFIED', 'REJECTED', 'FLAGGED')),
    reasons   TEXT NOT NULL DEFAULT '[]',   -- JSON array of strings
    scored_at TEXT NOT NULL
);

CREATE TABLE forecasts (
    post_id      TEXT NOT NULL REFERENCES posts (id),
    supply_class TEXT NOT NULL,
    date         TEXT NOT NULL,
    p10          REAL NOT NULL,
    p50          REAL NOT NULL,
    p90          REAL NOT NULL,
    model        TEXT NOT NULL,             -- local | federated | central
    made_on      TEXT NOT NULL,             -- the as-of date the forecast was issued
    PRIMARY KEY (post_id, supply_class, date, model, made_on)
);

-- Daily pass status history. p_close and days-to-closure stay NULL until the Day 7 model; until
-- then `status` comes from the closure rule labels and `days` counts days closed so far.
CREATE TABLE pass_status (
    pass_id    TEXT NOT NULL REFERENCES passes (id),
    as_of      TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('OPEN', 'AT_RISK', 'CLOSED')),
    p_close    REAL,
    days       INTEGER,
    snow_3d_cm REAL,
    temp_14d_c REAL,
    source     TEXT NOT NULL,               -- rule-label | model
    PRIMARY KEY (pass_id, as_of)
);

CREATE TABLE plans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    as_of        TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'APPROVED', 'CANCELLED')),
    created_by   TEXT
);

CREATE TABLE plan_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id      INTEGER NOT NULL REFERENCES plans (id),
    post_id      TEXT NOT NULL REFERENCES posts (id),
    supply_class TEXT NOT NULL,
    mode         TEXT NOT NULL CHECK (mode IN ('truck', 'mule', 'heli')),
    qty_t        REAL NOT NULL,
    depart_date  TEXT NOT NULL,
    cost         REAL NOT NULL,
    time_days    REAL NOT NULL,
    risk         REAL NOT NULL,
    reason       TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'PROPOSED'
);

-- Hash-chained audit log: hash = sha256(prev_hash + canonical(entry)). The first entry's prev_hash
-- is 64 zeros. Written by audit/chain.py (Day 8).
CREATE TABLE audit (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    event     TEXT NOT NULL,
    actor     TEXT,
    payload   TEXT NOT NULL,                -- canonical JSON
    prev_hash TEXT NOT NULL,
    hash      TEXT NOT NULL UNIQUE
);
