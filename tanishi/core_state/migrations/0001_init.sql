-- 0001: initial Core State schema (architecture v1.0, node CS1).
-- migrate() runs this file inside one transaction and records the version row itself.
-- Columns typed JSON hold JSON text (see build/memory/open-problems/CS1.md on their affinity).

CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    ts TEXT,
    kind TEXT,
    actor TEXT,
    session_id TEXT,
    payload JSON,
    prev_hash TEXT,
    hash TEXT
);

CREATE TABLE beliefs (
    id TEXT PRIMARY KEY,
    subject TEXT,
    predicate TEXT,
    object TEXT,
    confidence REAL,
    source TEXT,
    created_at TEXT,
    updated_at TEXT,
    status TEXT
);

CREATE TABLE evidence (
    id TEXT PRIMARY KEY,
    belief_id TEXT,
    event_id INTEGER,
    kind TEXT,
    note TEXT
);

CREATE TABLE goals (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    owner TEXT,
    title TEXT,
    rank REAL,
    status TEXT,
    created_at TEXT
);

CREATE TABLE predictions (
    id TEXT PRIMARY KEY,
    ts TEXT,
    about TEXT,
    expected JSON,
    confidence REAL,
    actual JSON,
    resolved_at TEXT,
    score REAL
);

CREATE TABLE rules (
    id TEXT PRIMARY KEY,
    domain TEXT,
    statement TEXT,
    support INTEGER,
    confidence REAL
);

CREATE TABLE capabilities (
    id TEXT PRIMARY KEY,
    name TEXT,
    family TEXT,
    state TEXT,
    ability REAL,
    calibration_error REAL,
    updated_at TEXT
);

CREATE TABLE experiments (
    id TEXT PRIMARY KEY,
    ts TEXT,
    candidate TEXT,
    baseline TEXT,
    task_set TEXT,
    seed INTEGER,
    score REAL,
    cost REAL,
    meta JSON
);

CREATE TABLE portfolio (
    id TEXT PRIMARY KEY,
    item TEXT,
    sleeve TEXT,
    cost_estimate REAL,
    expected_gain REAL,
    status TEXT
);

CREATE TABLE genome (
    version TEXT PRIMARY KEY,
    parent TEXT,
    created_at TEXT,
    record JSON
);

CREATE TABLE genes (
    id TEXT PRIMARY KEY,
    description TEXT,
    introduced_in TEXT
);

CREATE TABLE substrate_state (
    task_id TEXT PRIMARY KEY,
    working JSON,
    plan JSON,
    goal JSON,
    hypotheses JSON,
    updated_at TEXT
);
