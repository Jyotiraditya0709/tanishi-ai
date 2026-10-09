"""SUB1 red-team round 2: attacks on the repaired code. See build/memory/runs/redteam-SUB1-20261009-r2.md."""
import json
import time
from contextlib import closing

import pytest

from tanishi.core_state.db import open_db
from tanishi.substrate.state import DONE, Plan, Step, TaskState, load, save

KEY = "sk-ant-api03-" + "A" * 40


def _state(task_id="t", **over):
    f = {"task_id": task_id, "working": {}, "plan": Plan(steps=[Step(id="a", description="d")]),
         "goal_ids": [], "hypotheses": []}
    f.update(over)
    return TaskState(**f)


def _raw():
    with closing(open_db()) as conn:
        return json.dumps(conn.execute("SELECT * FROM substrate_state").fetchall())


def test_r2_secret_in_task_id_is_stored_in_plaintext():
    """R1: the B1 fix redacts every column except task_id, which is stored as given."""
    save(_state(task_id=f"job-{KEY}"))
    assert KEY not in _raw()


def test_r2_container_under_secret_key_keeps_its_secret():
    """R2: events.redact() hides ANY value under a secret-named key; state only hides text values."""
    save(_state(working={"password": {"value": "hunter2"}, "auth": ["bearer-material-1234"], "token": 987654321}))
    raw = _raw()
    assert "hunter2" not in raw and "bearer-material-1234" not in raw and "987654321" not in raw


def test_r2_secret_key_name_leaks_into_error_message():
    """R3: a TypeError for a bad value quotes the dict key path, and the key can itself be the secret."""
    with pytest.raises(TypeError) as e:
        save(_state(working={f"API_KEY=hunter2hunter2 {KEY}": object()}))
    assert KEY not in str(e.value) and "hunter2hunter2" not in str(e.value)


@pytest.mark.parametrize("status", ["done\u200b", "dнne"])
def test_r2_lookalike_done_silently_reruns_step(status):
    """R4: B4 fix only strips ASCII-ish blanks and lowercases; zero-width or look-alike 'done' still re-runs."""
    plan = Plan(steps=[Step(id="a", description="x", status=status)])
    with pytest.raises(ValueError):
        save(_state(plan=plan))


def test_r2_load_of_unencodable_task_id_is_lookup_error():
    """R5: load() documents LookupError for an unknown task."""
    with pytest.raises(LookupError):
        load("a\ud800")


def test_r2_redaction_does_not_hang_on_adversarial_text():
    """R6: ReDoS probe over the regexes save() now runs on every string."""
    # "password" * 5000 is 40 kB and takes ~2 s; the cost is quadratic, so 320 kB takes over two minutes.
    # Cause: events._ASSIGNED_SECRETS[0], `password[\w-]*` is retried at every "password" and eats the rest each time.
    for text in ("password" * 5000, "secret_" * 5000, "token" * 8000):
        t0 = time.perf_counter()
        save(_state(working={"s": text}))
        assert time.perf_counter() - t0 < 1.0, text[:12]


def test_r2_save_cost_is_flat_in_small_states():
    """R7: perf. Every save() reopens the db and runs migrate(); the loop will save once per step."""
    save(_state())
    t0 = time.perf_counter()
    for i in range(40):
        save(_state(working={"i": i}))
    per_save = (time.perf_counter() - t0) / 40
    assert per_save < 0.02, f"{per_save * 1000:.1f} ms per save"


def test_r2_large_state_save_is_bounded():
    big = {f"k{i}": "x" * 20 for i in range(20000)}
    t0 = time.perf_counter()
    save(_state(working=big))
    assert time.perf_counter() - t0 < 2.0


def test_r2_redaction_false_positive_changes_ordinary_text():
    """Documented trade-off, not asserted as a break: shows what a user sees."""
    w = {"note": "rotate the password: monthly"}
    save(_state(working=w))
    assert load("t").working != w  # prose is rewritten; round-trip is not identity for such text


def test_r2_redacted_key_collisions_stay_distinct():
    w = {"sk-ant-api03-" + "A" * 30: 1, "sk-ant-api03-" + "B" * 30: 2, "[REDACTED:anthropic]#": 3}
    save(_state(working=w))
    assert sorted(load("t").working.values()) == [1, 2, 3]


def test_r2_resave_of_loaded_state_is_stable():
    save(_state(working={"k": f"use {KEY}", "password": "x"}))
    s1 = load("t")
    save(s1)
    assert load("t") == s1


def test_r2_done_steps_after_a_failed_step_are_rerun_or_trusted():
    """Documented: next_step() is positional; a done step after a non-done one is re-run on resume."""
    plan = Plan(steps=[Step("a", "x", DONE), Step("b", "y", "failed"), Step("c", "z", DONE)])
    save(_state(plan=plan))
    assert load("t").plan.next_step().id == "b"
