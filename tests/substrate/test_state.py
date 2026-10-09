"""SUB1 exam: working, planning and goal state, written from the spec alone.

Spec interface:
    TaskState(task_id, working: dict, plan: Plan, goal_ids: list[str], hypotheses: list[dict])
    save(state) / load(task_id) -> TaskState
    Plan(steps: list[Step]); Step(id, description, status, model, result_ref)
Acceptance:
    1. Killing the process mid-task and calling load() resumes from the last completed step.
    2. State round-trips through save and load unchanged (property test).

The spec does not fix the status vocabulary. These tests use "pending" and "done" (see
build/memory/open-problems/sub1-step-status.md). Nothing else is assumed about statuses.
"""
import json
import os
import random
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tanishi.substrate.state import Plan, Step, TaskState, load, save

REPO_ROOT = Path(__file__).resolve().parents[2]
PENDING = "pending"
DONE = "done"


def make_state(task_id="t1", n_steps=3, done=0, **over):
    steps = [
        Step(
            id=f"s{i}",
            description=f"step {i}",
            status=DONE if i < done else PENDING,
            model="model-a",
            result_ref=f"res/{i}" if i < done else None,
        )
        for i in range(n_steps)
    ]
    fields = {
        "task_id": task_id,
        "working": {"scratch": "x", "n": done},
        "plan": Plan(steps=steps),
        "goal_ids": ["g1", "g2"],
        "hypotheses": [{"id": "h1", "claim": "a", "confidence": 0.5}],
    }
    fields.update(over)
    return TaskState(**fields)


# ---------------------------------------------------------------- shape


def test_fields_are_exposed():
    s = make_state()
    assert s.task_id == "t1"
    assert s.working == {"scratch": "x", "n": 0}
    assert [st.id for st in s.plan.steps] == ["s0", "s1", "s2"]
    st = s.plan.steps[0]
    assert (st.description, st.status, st.model, st.result_ref) == ("step 0", PENDING, "model-a", None)
    assert s.goal_ids == ["g1", "g2"]
    assert s.hypotheses[0]["id"] == "h1"


# ---------------------------------------------------------------- round trip


def test_round_trip_simple():
    s = make_state()
    save(s)
    assert load("t1") == s


def test_load_returns_task_state_with_typed_parts():
    save(make_state())
    got = load("t1")
    assert isinstance(got, TaskState)
    assert isinstance(got.plan, Plan)
    assert all(isinstance(st, Step) for st in got.plan.steps)


def test_round_trip_empty_state():
    s = TaskState(task_id="empty", working={}, plan=Plan(steps=[]), goal_ids=[], hypotheses=[])
    save(s)
    got = load("empty")
    assert got == s
    assert got.working == {} and got.plan.steps == [] and got.goal_ids == [] and got.hypotheses == []


def test_round_trip_awkward_values():
    s = make_state(
        task_id="awkward ✓/../'\"; DROP TABLE x;--",
        working={
            "float_one": 1.0,
            "int_one": 1,
            "big": 2**62,
            "neg": -7,
            "pi": 3.141592653589793,
            "t": True,
            "f": False,
            "none": None,
            "uni": "héllo 世界 🙂",
            "json_like": '{"a": 1}',
            "empty_str": "",
            "nested": {"a": [1, 2, {"b": [None, 1.5, "x"]}], "z": {}},
            "list": [],
        },
        goal_ids=["goal with space", "ü", ""],
        hypotheses=[{"k": [1.0, 2, True]}, {}],
    )
    save(s)
    got = load(s.task_id)
    assert got == s
    # == treats 1.0 and 1 and True alike, so check the types survived (decision 0006).
    assert type(got.working["float_one"]) is float
    assert type(got.working["int_one"]) is int
    assert got.working["t"] is True and got.working["f"] is False
    assert got.working["none"] is None
    assert type(got.hypotheses[0]["k"][0]) is float
    assert type(got.hypotheses[0]["k"][1]) is int
    assert got.hypotheses[0]["k"][2] is True


def test_step_fields_round_trip_including_none_and_text_refs():
    steps = [
        Step(id="a", description="", status=PENDING, model=None, result_ref=None),
        Step(id="b", description="d ü 🙂", status=DONE, model="m/x:1", result_ref="events/42"),
    ]
    s = make_state(plan=Plan(steps=steps))
    save(s)
    assert load("t1").plan.steps == steps


def test_step_order_is_preserved():
    ids = [f"s{i}" for i in random.Random(1).sample(range(100), 30)]
    steps = [Step(id=i, description=i, status=PENDING, model="m", result_ref=None) for i in ids]
    save(make_state(plan=Plan(steps=steps)))
    assert [st.id for st in load("t1").plan.steps] == ids


def test_goal_and_hypothesis_order_is_preserved():
    s = make_state(goal_ids=["z", "a", "m", "a"], hypotheses=[{"i": 3}, {"i": 1}, {"i": 2}])
    save(s)
    got = load("t1")
    assert got.goal_ids == ["z", "a", "m", "a"]
    assert [h["i"] for h in got.hypotheses] == [3, 1, 2]


def test_large_state_round_trips():
    s = make_state(
        n_steps=500,
        working={"blob": "x" * 200_000, "rows": [{"i": i} for i in range(2000)]},
        hypotheses=[{"i": i} for i in range(300)],
    )
    save(s)
    assert load("t1") == s


# ---------------------------------------------------------------- semantics of save / load


def test_save_replaces_previous_state_for_same_task():
    save(make_state(n_steps=5, done=0))
    newer = make_state(n_steps=2, done=2, goal_ids=[], hypotheses=[], working={"only": 1})
    save(newer)
    got = load("t1")
    assert got == newer
    assert len(got.plan.steps) == 2
    assert got.goal_ids == [] and got.hypotheses == []
    assert "scratch" not in got.working


def test_save_is_idempotent():
    s = make_state(done=1)
    save(s)
    save(s)
    save(s)
    assert load("t1") == s


def test_tasks_are_independent():
    a = make_state("a", done=1, working={"who": "a"})
    b = make_state("b", done=2, working={"who": "b"})
    save(a)
    save(b)
    assert load("a") == a and load("b") == b
    save(make_state("a", done=3, working={"who": "a2"}))
    assert load("b") == b


def test_task_ids_are_case_and_whitespace_exact():
    x, y, z = make_state("Task", working={"v": 1}), make_state("task", working={"v": 2}), make_state("task ", working={"v": 3})
    for s in (x, y, z):
        save(s)
    assert [load(t).working["v"] for t in ("Task", "task", "task ")] == [1, 2, 3]


def test_load_unknown_task_raises_lookup_error():
    with pytest.raises(LookupError):
        load("never-saved")


def test_load_unknown_task_after_other_saves_still_raises():
    save(make_state("known"))
    with pytest.raises(LookupError):
        load("unknown")


def test_load_returns_independent_copy():
    save(make_state())
    first = load("t1")
    first.working["scratch"] = "mutated"
    first.plan.steps[0].status = DONE
    first.goal_ids.append("g9")
    first.hypotheses[0]["claim"] = "mutated"
    assert load("t1") == make_state()


def test_save_snapshots_state_at_call_time():
    s = make_state()
    save(s)
    s.working["scratch"] = "later"
    s.plan.steps[0].status = DONE
    s.goal_ids.append("g9")
    s.hypotheses.append({"id": "h2"})
    assert load("t1") == make_state()


def test_model_swap_loses_nothing():
    """A step may be started by one model and the task resumed by another: the record keeps who did what."""
    s = make_state(done=2)
    s.plan.steps[0].model = "local/llama"
    s.plan.steps[1].model = "cloud/other"
    save(s)
    got = load("t1")
    assert [st.model for st in got.plan.steps] == ["local/llama", "cloud/other", "model-a"]
    assert [st.result_ref for st in got.plan.steps] == ["res/0", "res/1", None]


# ---------------------------------------------------------------- where it lives


def test_state_lives_in_the_core_state_db_not_the_legacy_ones(_temporary_tanishi_home):
    save(make_state())
    db = Path(os.environ["TANISHI_CORE_STATE_DB"])
    assert db.exists() and db.stat().st_size > 0
    home = _temporary_tanishi_home / ".tanishi"
    assert not (home / "tanishi.db").exists()
    assert not (home / "finance.db").exists()


def test_state_survives_a_fresh_process(tmp_path):
    save(make_state(done=2))
    out = _run_child("import json; from tanishi.substrate.state import load\n"
                     "s = load('t1'); print(json.dumps([st.status for st in s.plan.steps]))")
    assert json.loads(out.stdout) == [DONE, DONE, PENDING]


def test_different_db_path_means_different_store(monkeypatch, tmp_path):
    save(make_state())
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(tmp_path / "other.db"))
    with pytest.raises(LookupError):
        load("t1")


# ---------------------------------------------------------------- crash recovery (acceptance 1)


def _run_child(code, **kw):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60, check=False, **kw,
    )


_WORKER = """
import os, signal, sys
from tanishi.substrate.state import Plan, Step, TaskState, save

N = 6
KILL_AT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ["KILL_AT"])
steps = [Step(id=f"s{i}", description=f"d{i}", status="pending", model="m", result_ref=None) for i in range(N)]
state = TaskState(task_id="job", working={"completed": 0}, plan=Plan(steps=steps), goal_ids=["g"], hypotheses=[])
save(state)
for i in range(N):
    if i == KILL_AT:
        # mid-step: the work for step i is half done, nothing about it has been saved.
        state.working["half_done"] = i
        os.kill(os.getpid(), signal.SIGKILL)
    state.plan.steps[i].status = "done"
    state.plan.steps[i].result_ref = f"r{i}"
    state.working["completed"] = i + 1
    save(state)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="needs SIGKILL")
@pytest.mark.parametrize("kill_at", [0, 1, 3, 5])
def test_kill_mid_task_resumes_from_last_completed_step(kill_at):
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), KILL_AT=str(kill_at))
    p = subprocess.run([sys.executable, "-c", _WORKER], cwd=REPO_ROOT, env=env, timeout=60, check=False)
    assert p.returncode == -signal.SIGKILL

    got = load("job")
    statuses = [st.status for st in got.plan.steps]
    assert statuses == [DONE] * kill_at + [PENDING] * (6 - kill_at)
    assert [st.result_ref for st in got.plan.steps] == [f"r{i}" for i in range(kill_at)] + [None] * (6 - kill_at)
    assert got.working == {"completed": kill_at}  # the unsaved half-done work is not there
    first_pending = next(i for i, st in enumerate(got.plan.steps) if st.status != DONE)
    assert first_pending == kill_at


@pytest.mark.skipif(sys.platform == "win32", reason="needs SIGKILL")
def test_task_finishes_after_resume():
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), KILL_AT="2")
    subprocess.run([sys.executable, "-c", _WORKER], cwd=REPO_ROOT, env=env, timeout=60, check=False)
    got = load("job")
    for st in got.plan.steps:
        if st.status != DONE:
            st.status, st.result_ref = DONE, "resumed"
    got.working["completed"] = 6
    save(got)
    again = load("job")
    assert all(st.status == DONE for st in again.plan.steps)
    assert [st.result_ref for st in again.plan.steps][:2] == ["r0", "r1"]


_HAMMER = """
import sys
from tanishi.substrate.state import Plan, Step, TaskState, save

print("ready", flush=True)
i = 0
while True:
    i += 1
    steps = [Step(id=f"s{k}", description="x" * 2000, status="done" if k < i % 40 else "pending",
                  model="m", result_ref=None) for k in range(40)]
    save(TaskState(task_id="hammer", working={"n": i % 40, "pad": "p" * 5000}, plan=Plan(steps=steps),
                   goal_ids=[f"g{k}" for k in range(50)], hypotheses=[{"i": i}]))
"""


@pytest.mark.skipif(sys.platform == "win32", reason="needs SIGKILL")
@pytest.mark.parametrize("delay", [0.05, 0.13, 0.31, 0.5])
def test_kill_during_save_leaves_a_whole_state_never_a_torn_one(delay):
    """Killed at an arbitrary instant, possibly inside save(): load() sees one complete saved state."""
    import time

    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    p = subprocess.Popen([sys.executable, "-c", _HAMMER], cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "ready"
        time.sleep(delay)
    finally:
        p.send_signal(signal.SIGKILL)
        p.wait(timeout=30)
    try:
        got = load("hammer")
    except LookupError:
        return  # killed before the first save committed: nothing, not garbage
    n = got.working["n"]
    assert len(got.plan.steps) == 40
    assert [st.status for st in got.plan.steps] == [DONE] * n + [PENDING] * (40 - n)
    assert len(got.goal_ids) == 50 and len(got.hypotheses) == 1
    assert got.working["pad"] == "p" * 5000


# ---------------------------------------------------------------- property test (acceptance 2)


def _rand_json(rng, depth=0):
    kinds = ["int", "float", "str", "bool", "none"] + (["list", "dict"] if depth < 3 else [])
    k = rng.choice(kinds)
    if k == "int":
        return rng.choice([0, 1, -1, rng.randint(-10**6, 10**6), rng.randint(-2**62, 2**62)])
    if k == "float":
        return rng.choice([0.0, 1.0, -2.5, 1e-9, 1e22, rng.uniform(-1e6, 1e6), float(rng.randint(-5, 5))])
    if k == "str":
        return "".join(rng.choice("abc xyz\n\t\"'\\é世🙂{}[],:0") for _ in range(rng.randint(0, 12)))
    if k == "bool":
        return rng.random() < 0.5
    if k == "none":
        return None
    if k == "list":
        return [_rand_json(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {_rand_json_key(rng): _rand_json(rng, depth + 1) for _ in range(rng.randint(0, 4))}


def _rand_json_key(rng):
    return "".join(rng.choice("abcé世 _\"1") for _ in range(rng.randint(0, 6)))


def _rand_text(rng):
    return "".join(rng.choice("abc XYZé世🙂-_/:") for _ in range(rng.randint(0, 15)))


def _rand_state(rng, task_id):
    steps = [
        Step(
            id=_rand_text(rng),
            description=_rand_text(rng),
            status=rng.choice([PENDING, DONE, "running", "failed", "skipped"]),
            model=rng.choice([None, "m1", "local/x", _rand_text(rng)]),
            result_ref=rng.choice([None, _rand_text(rng)]),
        )
        for _ in range(rng.randint(0, 8))
    ]
    return TaskState(
        task_id=task_id,
        working={_rand_json_key(rng): _rand_json(rng) for _ in range(rng.randint(0, 5))},
        plan=Plan(steps=steps),
        goal_ids=[_rand_text(rng) for _ in range(rng.randint(0, 5))],
        hypotheses=[{_rand_json_key(rng): _rand_json(rng) for _ in range(rng.randint(0, 3))} for _ in range(rng.randint(0, 4))],
    )


def _exact(a):
    """Type-exact fingerprint: == alone would confuse 1, 1.0 and True."""
    return json.dumps(a, sort_keys=True, default=lambda o: o.__dict__)


@pytest.mark.parametrize("seed", range(150))
def test_property_round_trip_is_identity(seed):
    rng = random.Random(seed)
    s = _rand_state(rng, f"task-{seed}-{_rand_text(rng)}")
    before = _exact(s)
    save(s)
    got = load(s.task_id)
    assert got == s
    assert _exact(got) == before
    assert _exact(s) == before  # save did not alter its argument


@pytest.mark.parametrize("seed", range(20))
def test_property_many_tasks_resaved_in_any_order(seed):
    rng = random.Random(1000 + seed)
    latest = {}
    for _ in range(25):
        tid = f"t{rng.randint(0, 5)}"
        latest[tid] = _rand_state(rng, tid)
        save(latest[tid])
    for tid, s in latest.items():
        assert load(tid) == s
        assert _exact(load(tid)) == _exact(s)
