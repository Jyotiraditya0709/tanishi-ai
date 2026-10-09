-- 0001: initial Core State schema (architecture v1.0, node CS1, hardened per decision 0007).
-- migrate() runs this file inside one transaction and records the version row and checksum itself.
-- Never edit this file once a real database exists: migrate() refuses a changed applied migration.
-- JSON lives in TEXT columns (decision 0006) and must be valid JSON or NULL.
-- Every confidence is a number in [0, 1]; NOT NULL because SQLite stores a NaN as NULL.

CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT
);

-- Append-only, hash-chained. AUTOINCREMENT so an id is never handed out twice.
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    kind TEXT,
    actor TEXT,
    session_id TEXT,
    payload TEXT CHECK (payload IS NULL OR json_valid(payload)),
    prev_hash TEXT UNIQUE,  -- one child per link: the chain cannot fork
    hash TEXT UNIQUE
);

CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events is append-only');
END;

-- Rows in the chain (hash set) can never be deleted. Unhashed rows stay deletable because the
-- red-team id-reuse test deletes one; see decision 0007.
CREATE TRIGGER events_no_delete BEFORE DELETE ON events WHEN OLD.hash IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'events is append-only');
END;

CREATE TABLE beliefs (
    id TEXT PRIMARY KEY NOT NULL,
    subject TEXT,
    predicate TEXT,
    object TEXT,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    source TEXT,
    created_at TEXT,
    updated_at TEXT,
    status TEXT
);

CREATE TABLE evidence (
    id TEXT PRIMARY KEY NOT NULL,
    belief_id TEXT REFERENCES beliefs(id),
    event_id INTEGER REFERENCES events(id),
    kind TEXT,
    note TEXT
);

CREATE TABLE goals (
    id TEXT PRIMARY KEY NOT NULL,
    parent_id TEXT REFERENCES goals(id),
    owner TEXT,
    title TEXT,
    rank REAL,
    status TEXT,
    created_at TEXT
);

CREATE TABLE predictions (
    id TEXT PRIMARY KEY NOT NULL,
    ts TEXT,
    about TEXT,
    expected TEXT CHECK (expected IS NULL OR json_valid(expected)),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    actual TEXT CHECK (actual IS NULL OR json_valid(actual)),
    resolved_at TEXT,
    score REAL
);

CREATE TABLE rules (
    id TEXT PRIMARY KEY NOT NULL,
    domain TEXT,
    statement TEXT,
    support INTEGER,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1)
);

CREATE TABLE capabilities (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT,
    family TEXT,
    state TEXT,
    ability REAL,
    calibration_error REAL,
    updated_at TEXT
);

CREATE TABLE experiments (
    id TEXT PRIMARY KEY NOT NULL,
    ts TEXT,
    candidate TEXT,
    baseline TEXT,
    task_set TEXT,
    seed INTEGER,
    score REAL,
    cost REAL,
    meta TEXT CHECK (meta IS NULL OR json_valid(meta))
);

CREATE TABLE portfolio (
    id TEXT PRIMARY KEY NOT NULL,
    item TEXT,
    sleeve TEXT,
    cost_estimate REAL,
    expected_gain REAL,
    status TEXT
);

CREATE TABLE genome (
    version TEXT PRIMARY KEY NOT NULL,
    parent TEXT REFERENCES genome(version),
    created_at TEXT,
    record TEXT CHECK (record IS NULL OR json_valid(record))
);

CREATE TABLE genes (
    id TEXT PRIMARY KEY NOT NULL,
    description TEXT,
    introduced_in TEXT
);

CREATE TABLE substrate_state (
    task_id TEXT PRIMARY KEY NOT NULL,
    working TEXT CHECK (working IS NULL OR json_valid(working)),
    plan TEXT CHECK (plan IS NULL OR json_valid(plan)),
    goal TEXT CHECK (goal IS NULL OR json_valid(goal)),
    hypotheses TEXT CHECK (hypotheses IS NULL OR json_valid(hypotheses)),
    updated_at TEXT
);

-- Hot lookups, plus the child side of each foreign key (an FK check on the parent scans it).
CREATE INDEX evidence_belief_id ON evidence(belief_id);
CREATE INDEX evidence_event_id ON evidence(event_id);
CREATE INDEX beliefs_subject_predicate ON beliefs(subject, predicate);
CREATE INDEX events_session_id ON events(session_id);
CREATE INDEX goals_parent_id ON goals(parent_id);
CREATE INDEX genome_parent ON genome(parent);
