-- storage/schema.sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS room (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    premise        TEXT NOT NULL DEFAULT '',
    archetype      TEXT NOT NULL,
    phase_index    INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'lobby',
    rng_seed       INTEGER NOT NULL,
    created_at     REAL NOT NULL,
    last_active    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS party (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    budget      INTEGER NOT NULL DEFAULT 100,
    schedule    INTEGER NOT NULL DEFAULT 100,
    tech_debt   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS players (
    player_id    TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    token        TEXT NOT NULL UNIQUE,
    class_id     TEXT NOT NULL,
    joined_at    REAL NOT NULL,
    last_seen    REAL NOT NULL,
    seat         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS characters (
    player_id    TEXT PRIMARY KEY REFERENCES players(player_id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    class_id     TEXT NOT NULL,
    stats        TEXT NOT NULL,
    level        INTEGER NOT NULL,
    stamina      INTEGER NOT NULL,
    max_stamina  INTEGER NOT NULL,
    focus        INTEGER NOT NULL,
    max_focus    INTEGER NOT NULL,
    unlocked     TEXT NOT NULL,
    used         TEXT NOT NULL,
    appearance   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS hazards (
    id            TEXT PRIMARY KEY,
    phase_index   INTEGER NOT NULL,
    ordinal       INTEGER NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL,
    severity      INTEGER NOT NULL,
    max_severity  INTEGER NOT NULL,
    dc            INTEGER NOT NULL,
    attack_type   TEXT NOT NULL,
    weakness      TEXT NOT NULL,
    revealed      TEXT NOT NULL DEFAULT '[]',
    defeated      INTEGER NOT NULL DEFAULT 0,
    is_boss       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS turn_state (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    round             INTEGER NOT NULL DEFAULT 1,
    turn_index        INTEGER NOT NULL DEFAULT 0,
    turn_order        TEXT NOT NULL DEFAULT '[]',
    active_hazard_id  TEXT,
    conditions        TEXT NOT NULL DEFAULT '[]',
    last_ability_id   TEXT
);

CREATE TABLE IF NOT EXISTS events (
    seq      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    kind     TEXT NOT NULL,
    actor    TEXT,
    payload  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS narrations (
    event_seq INTEGER PRIMARY KEY REFERENCES events(seq) ON DELETE CASCADE,
    status    TEXT NOT NULL DEFAULT 'pending',
    text      TEXT NOT NULL DEFAULT '',
    source    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_hazards_phase ON hazards(phase_index, ordinal);
