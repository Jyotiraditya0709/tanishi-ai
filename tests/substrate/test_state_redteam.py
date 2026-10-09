"""SUB1 red-team tests. Each test proves one break; see build/memory/runs/redteam-SUB1-20261009.md."""
import json
import multiprocessing as mp
import os
from contextlib import closing

import pytest

from tanishi.core_state.db import open_db
from tanishi.substrate.state import DONE, Plan, Step, TaskState, load, save


def _state(task_id="t", **over):
    f = {"task_id": task_id, "working": {}, "plan": Plan(steps=[Step(id="a", description="d")]),
         "goal_ids": [], "hypotheses": []}
    f.update(over)
    return TaskState(**f)


def _fresh_save(args):
    db, i = args
    os.environ["TANISHI_CORE_STATE_DB"] = db
    try:
        save(_state(f"t{i}"))
        return None
    except Exception as e:  # noqa: BLE001
        return repr(e)


def test_secret_in_working_state_is_stored_in_plaintext():
    """B1: events redact secrets before storage; substrate state does not."""
    key = "sk-ant-api03-" + "A" * 40
    save(_state(working={"note": f"use key {key}", "env": "API_KEY=hunter2hunter2"}))
    with closing(open_db()) as conn:
        raw = conn.execute("SELECT working FROM substrate_state").fetchone()[0]
    assert key not in raw and "hunter2hunter2" not in raw


def test_duplicate_step_ids_are_accepted():
    """B2: result_ref/resume bookkeeping keys on Step.id; duplicates make it ambiguous."""
    plan = Plan(steps=[Step(id="s", description="a", status=DONE), Step(id="s", description="b")])
    with pytest.raises(ValueError):
        save(_state(plan=plan))


def test_empty_step_id_is_accepted():
    """B3: an empty step id can never be referred to."""
    with pytest.raises(ValueError):
        save(_state(plan=Plan(steps=[Step(id="", description="x")])))


def test_status_case_variant_silently_reruns_step():
    """B4: 'DONE' is opaque text, so next_step() re-runs a finished step with no warning."""
    plan = Plan(steps=[Step(id="a", description="x", status="DONE")])
    with pytest.raises(ValueError):
        save(_state(plan=plan))


def test_corrupt_plan_row_raises_raw_keyerror_not_valueerror():
    """B5: load() documents ValueError for a bad row, but a plan missing a key leaks KeyError."""
    save(_state())
    with closing(open_db()) as conn, conn:
        conn.execute("UPDATE substrate_state SET plan = ?", (json.dumps({"steps": [{"id": "a"}]}),))
    with pytest.raises(ValueError):
        load("t")


def test_plan_json_null_is_not_a_clean_error():
    """B6: a valid-JSON but wrong-shaped plan leaks TypeError."""
    save(_state())
    with closing(open_db()) as conn, conn:
        conn.execute("UPDATE substrate_state SET plan = 'null'")
    with pytest.raises(ValueError):
        load("t")


def test_concurrent_first_saves_on_fresh_db_do_not_fail(tmp_path):
    """B7: many processes migrating a brand-new db at once."""
    db = str(tmp_path / "fresh.db")
    with mp.get_context("spawn").Pool(8) as pool:
        errs = [e for e in pool.map(_fresh_save, [(db, i) for i in range(16)]) if e]
    assert not errs, errs


def test_lone_surrogate_roundtrips():
    s = _state(working={"k": "a\ud800b"})
    save(s)
    assert load("t") == s


def test_whitespace_only_task_id_is_refused():
    """B8: a whitespace-only id is a distinct, invisible task."""
    with pytest.raises(ValueError):
        save(_state(task_id="   "))


def test_huge_int_beyond_json_digit_limit_fails_cleanly():
    with pytest.raises((ValueError, TypeError)):
        save(_state(working={"n": 10 ** 5000}))
    with pytest.raises(LookupError):
        load("t")


def test_running_step_is_replayed_after_crash():
    """Documented limit, not a bug: a RUNNING step is re-executed on resume."""
    plan = Plan(steps=[Step(id="a", description="x", status=DONE), Step(id="b", description="y", status="running")])
    save(_state(plan=plan))
    assert load("t").plan.next_step().id == "b"
