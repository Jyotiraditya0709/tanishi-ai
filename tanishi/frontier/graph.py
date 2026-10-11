"""The Capability Graph (E3): a living map of what she can and cannot do.

A node is one capability: a task family, a verifier (dotted path to a `verifier(task, output) -> (score, reason)`), its
sealed instances (task ids), a state, an ability estimate and her confidence in that estimate. An edge says one
capability requires another, fails together with it, or composes into it.

    g = CapabilityGraph()                       # tables in the Core State db ($TANISHI_CORE_STATE_DB)
    g.seed()                                    # the 12 seed capabilities, idempotent
    g.record_outcome("code.fix_bug", "practice.fix_bug_fn", passed=True)
    g.set_predicted_rate("code.fix_bug", 0.8)   # what she expects to score
    g.node("code.fix_bug").state                # mastered | weak | missing | correlated | unexplored

Mastered: at least 90% on the sealed instances, and her predicted rate within 10 points of the real one (decision 0024).
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tanishi.core_state.db import open_db, resolve_path

STATES = ("mastered", "weak", "missing", "correlated", "unexplored")
EDGE_KINDS = ("requires", "fails_together", "composes_into")

MASTERY_RATE = 0.9  # observed pass rate on sealed instances
CALIBRATION_TOLERANCE = 0.10  # |predicted - observed|, inclusive
PRIOR_WEIGHT = 10  # confidence = n / (n + PRIOR_WEIGHT): half-confident at 10 outcomes
_EPS = 1e-9  # so 0.9 - 0.8 (= 0.09999999999999998 or 0.10000000000000009) sits inside the tolerance

NO_VERIFIER = "tanishi.frontier.graph.no_verifier"  # placeholder for a `missing` candidate until one is written
MAX_ID_CHARS = 200

_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+")
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f  ]")

_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS capability_nodes (
        id TEXT PRIMARY KEY NOT NULL,
        task_family TEXT NOT NULL,
        verifier TEXT NOT NULL,
        instances TEXT NOT NULL CHECK (json_valid(instances)),
        state TEXT NOT NULL CHECK (state IN ({", ".join(f"'{s}'" for s in STATES)})),
        ability REAL NOT NULL CHECK (ability BETWEEN 0 AND 1),
        confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        predicted_rate REAL CHECK (predicted_rate IS NULL OR predicted_rate BETWEEN 0 AND 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS capability_outcomes (
        node_id TEXT NOT NULL REFERENCES capability_nodes(id),
        instance_id TEXT NOT NULL,
        passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
        ts TEXT NOT NULL,
        PRIMARY KEY (node_id, instance_id))""",
    f"""CREATE TABLE IF NOT EXISTS capability_edges (
        src TEXT NOT NULL REFERENCES capability_nodes(id),
        dst TEXT NOT NULL REFERENCES capability_nodes(id),
        kind TEXT NOT NULL CHECK (kind IN ({", ".join(f"'{k}'" for k in EDGE_KINDS)})),
        PRIMARY KEY (src, dst, kind),
        CHECK (src <> dst))""",
)


@dataclass(frozen=True)
class Node:
    id: str
    task_family: str
    verifier: str
    instances: tuple[str, ...]
    state: str
    ability: float  # posterior mean pass rate, Beta(1, 1) prior: 0.5 with no evidence
    confidence: float  # n / (n + PRIOR_WEIGHT): 0 with no evidence, never falls as outcomes accumulate
    predicted_rate: float | None  # her own forecast of her pass rate
    observed_rate: float | None  # passes / n over sealed instances, None with no evidence
    evidence: int  # n: sealed instances with a recorded outcome


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    kind: str


def no_verifier(task: object, output: object) -> tuple[float, str]:
    """A `missing` capability has no verifier yet; it never scores."""
    return 0.0, "no verifier yet"


# ---------------------------------------------------------------- the rules, pure

def _unit(x: object, name: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise TypeError(f"{name} must be a number in [0, 1]")
    x = float(x)
    if not math.isfinite(x) or not 0.0 <= x <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return x


def _count(n: object) -> int:
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("n must be an int")
    if n < 0:
        raise ValueError("n must be >= 0")
    return n


def state_for(observed: float, predicted: float | None, n: int) -> str:
    """The state that n outcomes at pass rate `observed` and her forecast `predicted` earn.

    n == 0 is `unexplored`. Otherwise `mastered` when observed >= 0.9 and |predicted - observed| <= 0.10 (no
    forecast, no mastery), else `weak`. `missing` and `correlated` never come from pass rates.
    """
    observed = _unit(observed, "observed")
    if predicted is not None:
        predicted = _unit(predicted, "predicted")
    if _count(n) == 0:
        return "unexplored"
    if predicted is None:
        return "weak"
    if observed >= MASTERY_RATE - _EPS and abs(predicted - observed) <= CALIBRATION_TOLERANCE + _EPS:
        return "mastered"
    return "weak"


def ability_for(passes: int, n: int) -> float:
    return (passes + 1) / (n + 2)


def confidence_for(n: int) -> float:
    return n / (n + PRIOR_WEIGHT)


# ---------------------------------------------------------------- validation

def _name(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{what} must be a str")
    if not value.strip() or value != value.strip():
        raise ValueError(f"{what} must be non-blank with no surrounding spaces")
    if len(value) > MAX_ID_CHARS or _CONTROL.search(value):
        raise ValueError(f"{what} must be at most {MAX_ID_CHARS} printable characters")
    return value


def _family(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("task_family must be a str")
    return _name(value.strip(), "task_family")


def _verifier(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("verifier must be a dotted path str")
    if not _DOTTED.fullmatch(value):
        raise ValueError("verifier must be a dotted path such as package.module.function")
    return value


def _instances(values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise TypeError("instances must be a list of instance ids")
    out = tuple(_name(v, "instance id") for v in values)
    if len(set(out)) != len(out):
        raise ValueError("instance ids must be unique")
    return out


def _state(value: object) -> str:
    if not isinstance(value, str) or value not in STATES:
        raise ValueError(f"state must be one of {STATES}")
    return value


def _kind(value: object) -> str:
    if not isinstance(value, str) or value not in EDGE_KINDS:
        raise ValueError(f"edge kind must be one of {EDGE_KINDS}")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------- the store

class CapabilityGraph:
    """The graph, stored in the Core State db (its own tables, created on open; no migration needed)."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = resolve_path(None if db_path is None else str(db_path))
        with self._tx() as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = open_db(self._path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = open_db(self._path)
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------ nodes

    def add_node(self, id: str, task_family: str, verifier: str, instances: Iterable[str] = (),
                 state: str = "unexplored") -> Node:
        """Add a capability. Refuses a duplicate id. Does not import the verifier."""
        values = (_name(id, "node id"), _family(task_family), _verifier(verifier), _instances(instances), _state(state))
        with self._tx() as conn:
            if not self._insert(conn, *values):
                raise ValueError(f"node {id!r} already exists")
            return self._get(conn, id)

    @staticmethod
    def _insert(conn: sqlite3.Connection, id: str, family: str, verifier: str, instances: tuple[str, ...],
                state: str) -> bool:
        now = _now()
        cur = conn.execute(
            "INSERT OR IGNORE INTO capability_nodes (id, task_family, verifier, instances, state, ability, confidence,"
            " predicted_rate, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (id, family, verifier, json.dumps(list(instances)), state, ability_for(0, 0), 0.0, now, now),
        )
        return cur.rowcount == 1

    def node(self, id: str) -> Node:
        with self._read() as conn:
            return self._get(conn, id)

    def nodes(self) -> list[Node]:
        with self._read() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM capability_nodes ORDER BY id")]
            return [self._get(conn, i) for i in ids]

    @staticmethod
    def _get(conn: sqlite3.Connection, id: object) -> Node:
        row = conn.execute(
            "SELECT id, task_family, verifier, instances, state, ability, confidence, predicted_rate"
            " FROM capability_nodes WHERE id = ?", (id,) if isinstance(id, str) else (None,),
        ).fetchone()
        if row is None:
            raise KeyError(id)
        instances = tuple(json.loads(row[3]))
        n, passes = CapabilityGraph._tally(conn, row[0], instances)
        return Node(id=row[0], task_family=row[1], verifier=row[2], instances=instances, state=row[4],
                    ability=row[5], confidence=row[6], predicted_rate=row[7],
                    observed_rate=passes / n if n else None, evidence=n)

    @staticmethod
    def _tally(conn: sqlite3.Connection, id: str, instances: tuple[str, ...]) -> tuple[int, int]:
        """(n, passes) over the node's sealed instances."""
        rows = conn.execute("SELECT instance_id, passed FROM capability_outcomes WHERE node_id = ?", (id,)).fetchall()
        own = set(instances)
        results = [p for i, p in rows if i in own]
        return len(results), sum(results)

    # ------------------------------------------------------------ evidence

    def record_outcome(self, node_id: str, instance_id: str, passed: bool) -> Node:
        """Record a verified attempt on one of the node's sealed instances. A re-run replaces the earlier outcome."""
        if not isinstance(passed, bool):
            raise TypeError("passed must be a bool")
        with self._tx() as conn:
            n = self._get(conn, node_id)
            if instance_id not in n.instances:
                raise KeyError(f"{instance_id!r} is not a sealed instance of {node_id!r}")
            conn.execute(
                "INSERT INTO capability_outcomes (node_id, instance_id, passed, ts) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (node_id, instance_id) DO UPDATE SET passed = excluded.passed, ts = excluded.ts",
                (node_id, instance_id, int(passed), _now()),
            )
            return self._recompute(conn, node_id)

    def set_predicted_rate(self, node_id: str, p: float) -> Node:
        """Her own forecast of her pass rate on this capability's sealed instances."""
        p = _unit(p, "predicted rate")
        with self._tx() as conn:
            self._get(conn, node_id)
            conn.execute("UPDATE capability_nodes SET predicted_rate = ? WHERE id = ?", (p, node_id))
            return self._recompute(conn, node_id)

    def refresh(self, node_id: str) -> Node:
        """Recompute ability, confidence and state from the stored evidence. Idempotent."""
        with self._tx() as conn:
            return self._recompute(conn, node_id)

    def _recompute(self, conn: sqlite3.Connection, node_id: str) -> Node:
        n = self._get(conn, node_id)
        if n.evidence == 0:  # no evidence: keep the stored state (unexplored, or missing/correlated set by others)
            return n
        state = state_for(n.observed_rate, n.predicted_rate, n.evidence)
        passes = round(n.observed_rate * n.evidence)
        conn.execute(
            "UPDATE capability_nodes SET state = ?, ability = ?, confidence = ?, updated_at = ? WHERE id = ?",
            (state, ability_for(passes, n.evidence), confidence_for(n.evidence), _now(), node_id),
        )
        return self._get(conn, node_id)

    # ------------------------------------------------------------ edges

    def add_edge(self, src: str, dst: str, kind: str) -> Edge:
        """`src requires dst`, `src fails_together dst` (undirected) or `src composes_into dst`."""
        kind = _kind(kind)
        with self._tx() as conn:
            return self._add_edge(conn, src, dst, kind)

    def _add_edge(self, conn: sqlite3.Connection, src: str, dst: str, kind: str) -> Edge:
        self._get(conn, src)
        self._get(conn, dst)
        if src == dst:
            raise ValueError("a capability cannot have an edge to itself")
        if kind == "fails_together":
            src, dst = sorted((src, dst))
        if kind == "requires" and src in self._required(conn, dst):
            raise ValueError(f"{src!r} requires {dst!r} would make a requires cycle")
        conn.execute("INSERT OR IGNORE INTO capability_edges (src, dst, kind) VALUES (?, ?, ?)", (src, dst, kind))
        return Edge(src, dst, kind)

    def edges(self, kind: str | None = None) -> list[Edge]:
        sql, args = "SELECT src, dst, kind FROM capability_edges", ()
        if kind is not None:
            sql, args = sql + " WHERE kind = ?", (_kind(kind),)
        with self._read() as conn:
            return [Edge(*r) for r in conn.execute(sql + " ORDER BY src, dst, kind", args)]

    def requirements(self, id: str) -> set[str]:
        """Every capability `id` requires, directly or through others."""
        with self._read() as conn:
            self._get(conn, id)
            return self._required(conn, id) - {id}

    @staticmethod
    def _required(conn: sqlite3.Connection, start: str) -> set[str]:
        seen, todo = {start}, [start]
        while todo:
            for (dst,) in conn.execute(
                "SELECT dst FROM capability_edges WHERE kind = 'requires' AND src = ?", (todo.pop(),)
            ):
                if dst not in seen:
                    seen.add(dst)
                    todo.append(dst)
        return seen

    # ------------------------------------------------------------ seeds

    def seed(self) -> list[Node]:
        """Add the 12 seed capabilities and their edges. Existing nodes and their evidence are left alone."""
        with self._tx() as conn:
            for id, family, verifier, instances in SEEDS:
                self._insert(conn, id, family, verifier, instances, "unexplored")
            for src, dst, kind in SEED_EDGES:
                self._add_edge(conn, src, dst, kind)
            return [self._get(conn, s[0]) for s in SEEDS]


# ---------------------------------------------------------------- the 12 seeds

_P = "tanishi.arena.practice."
# (id, task family, verifier, instances). Instances are the AR2 Practice task ids until AR4 supplies sealed ones; each
# task keeps its own verifier in its YAML, and the node's verifier is the one for its first instance (decision 0024).
SEEDS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("code.write_function", "coding", _P + "checks.slugify_fn", ("practice.slugify_fn", "practice.dedupe_fn")),
    ("code.fix_bug", "coding", _P + "checks.fix_bug_fn", ("practice.fix_bug_fn",)),
    ("code.sql", "coding", _P + "checks.sql_query", ("practice.sql_query",)),
    ("reasoning.arithmetic", "reasoning", _P + "legacy.math", ("practice.math", "practice.unit_convert")),
    ("reasoning.dates", "reasoning", _P + "checks.date_arith", ("practice.date_arith",)),
    ("reasoning.extraction", "reasoning", _P + "checks.json_extract", ("practice.json_extract",)),
    ("instruction.format", "instruction", _P + "checks.json_only_reply",
     ("practice.json_only_reply", "practice.bullet_count")),
    ("tool.file_read", "tool_use", _P + "checks.read_config_value",
     ("practice.read_config_value", "practice.read_two_sum", "practice.latest_report", "practice.find_todo_file",
      "practice.count_matching_files")),
    ("tool.file_write", "tool_use", _P + "checks.write_exact_file",
     ("practice.write_exact_file", "practice.append_line", "practice.log_expense")),
    ("tool.system", "tool_use", _P + "checks.os_python",
     ("practice.os_python", "practice.system_check", "practice.count_lines_shell", "practice.get_time",
      "practice.today_weekday", "practice.flaky_probe")),
    ("memory.recall", "memory", _P + "legacy.memory_recall", ("practice.memory_recall",)),
    ("conversation.voice", "conversation", _P + "legacy.explain_concept",
     ("practice.explain_concept", "practice.greeting", "practice.poem", "practice.personality")),
)

# Starting hypotheses, not measurements: the nightly clustering and later nodes revise them.
SEED_EDGES: tuple[tuple[str, str, str], ...] = (
    ("code.fix_bug", "code.write_function", "requires"),  # you cannot fix a function you could not write
    ("reasoning.dates", "reasoning.arithmetic", "requires"),
    ("tool.file_write", "tool.file_read", "requires"),  # append_line and log_expense read before they write
    ("reasoning.extraction", "instruction.format", "composes_into"),  # extract, then reply in the asked shape
    ("reasoning.arithmetic", "tool.file_read", "composes_into"),  # read_two_sum: read, then add
    ("code.write_function", "code.sql", "fails_together"),  # both run her code in the harness
)
