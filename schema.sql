-- CypCut Monitor SQLite Schema
-- Bu dosya uygulama ilk açılışında idempotent olarak uygulanır.

CREATE TABLE IF NOT EXISTS state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    current_state   TEXT    NOT NULL,
    last_event      TEXT,
    last_event_at   TEXT,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS alarm_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alarm_text      TEXT    NOT NULL,
    raw_line        TEXT    NOT NULL,
    occurred_at     TEXT    NOT NULL,
    telegram_sent   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_alarm_occurred ON alarm_events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_alarm_text     ON alarm_events(alarm_text);

CREATE TABLE IF NOT EXISTS state_transitions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_state      TEXT,
    to_state        TEXT    NOT NULL,
    reason          TEXT,
    occurred_at     TEXT    NOT NULL,
    telegram_sent   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_transitions_occurred ON state_transitions(occurred_at);

CREATE TABLE IF NOT EXISTS cooldowns (
    key             TEXT    PRIMARY KEY,
    last_sent_at    TEXT    NOT NULL
);
