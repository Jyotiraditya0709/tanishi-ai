"""Exam fixtures for AR1, written from the spec only (build/graph.yaml node AR1).

The spec names: Task (fields id, family, tier, prompt, setup, verifier, timeout_s, tags), run(task_set, candidate,
seeds=3) -> RunResult, verifiers as plain functions returning (float in [0,1], reason), one experiments row per seed
with score and cost, fresh TANISHI_HOME and fresh core_state db per task. Nothing else is assumed here. Where the spec
is silent, see build/memory/open-problems/AR1-exam-assumptions.md.
"""
import json
import os
import sys
import textwrap

import pytest


@pytest.fixture
def probe_module(tmp_path, monkeypatch):
    """A throwaway module of verifiers, importable by dotted path. Probes log what they saw to a JSONL file."""
    log = tmp_path / "probe_log.jsonl"
    src = textwrap.dedent(
        '''
        import json, os

        LOG = __LOG__

        def _log(kind):
            home = os.environ.get("TANISHI_HOME")
            db = os.environ.get("TANISHI_CORE_STATE_DB")
            with open(LOG, "a") as f:
                f.write(json.dumps({
                    "kind": kind,
                    "home": home,
                    "db": db,
                    "home_is_dir": bool(home) and os.path.isdir(home),
                }) + chr(10))

        def full(*a, **k):
            _log("full")
            return 1.0, "ok"

        def half(*a, **k):
            return 0.5, "half"

        def zero(*a, **k):
            return 0.0, "zero"

        def boom(*a, **k):
            raise RuntimeError("verifier exploded")

        def too_high(*a, **k):
            return 7.0, "out of range"

        def negative(*a, **k):
            return -3.0, "out of range"

        def nan(*a, **k):
            return float("nan"), "nan"

        def inf(*a, **k):
            return float("inf"), "inf"

        def wrong_shape(*a, **k):
            return "great"

        def slow(*a, **k):
            import time
            time.sleep(30)
            return 1.0, "too late"

        def pollute(*a, **k):
            _log("pollute")
            home = os.environ["TANISHI_HOME"]
            os.makedirs(os.path.join(home, "skills"), exist_ok=True)
            with open(os.path.join(home, "skills", "leaked.md"), "w") as f:
                f.write("x")
            return 1.0, "polluted"
        '''
    ).replace("__LOG__", repr(str(log)))
    (tmp_path / "exam_probes.py").write_text(src)
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("exam_probes", None)

    def read_log():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]

    class Probe:
        path = "exam_probes"
        read = staticmethod(read_log)

    return Probe


@pytest.fixture
def make_task(probe_module):
    from tanishi.arena.task import Task

    counter = {"n": 0}

    def _make(verifier="full", **over):
        counter["n"] += 1
        fields = {
            "id": f"exam-task-{counter['n']}",
            "family": "exam",
            "tier": "practice",
            "prompt": "say hello",
            "verifier": f"{probe_module.path}.{verifier}",
            "timeout_s": 5,
            "tags": ["exam"],
        }
        fields.update(over)
        return Task(**fields)

    return _make


@pytest.fixture
def outer_db():
    """The caller's own Core State db (tests/conftest.py points it at a temp path)."""
    from tanishi.core_state import migrate, open_db

    conn = open_db(os.environ["TANISHI_CORE_STATE_DB"])
    migrate(conn)
    yield conn
    conn.close()

