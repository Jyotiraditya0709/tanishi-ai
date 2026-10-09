"""SUB1 builder tests: what save() refuses, next_step(), and a raw-row check. The exam is test_state.py."""
import enum
import json
import sqlite3
from contextlib import closing

import pytest

from tanishi.core_state.db import open_db
from tanishi.substrate.state import (
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    Plan,
    Step,
    TaskState,
    load,
    save,
)


def _state(task_id="t", **over):
    fields = {
        "task_id": task_id,
        "working": {},
        "plan": Plan(steps=[Step(id="a", description="first")]),
        "goal_ids": [],
        "hypotheses": [],
    }
    fields.update(over)
    return TaskState(**fields)


class _Level(enum.IntEnum):
    ONE = 1


def _cyclic():
    d = {}
    d["self"] = d
    return d


@pytest.mark.parametrize(
    ("over", "error"),
    [
        ({"task_id": ""}, ValueError),
        ({"task_id": 7}, TypeError),
        ({"working": []}, TypeError),
        ({"working": {"x": float("nan")}}, ValueError),
        ({"working": {"x": float("inf")}}, ValueError),
        ({"working": {"x": (1, 2)}}, TypeError),
        ({"working": {1: "int key"}}, TypeError),
        ({"working": {"x": {1, 2}}}, TypeError),
        ({"working": {"x": b"bytes"}}, TypeError),
        ({"working": {"x": _Level.ONE}}, TypeError),
        ({"working": _cyclic()}, ValueError),
        ({"plan": [Step(id="a", description="d")]}, TypeError),
        ({"plan": Plan(steps=[{"id": "a"}])}, TypeError),
        ({"plan": Plan(steps=[Step(id=1, description="d")])}, TypeError),
        ({"plan": Plan(steps=[Step(id="a", description="d", status=None)])}, TypeError),
        ({"plan": Plan(steps=[Step(id="a", description="d", result_ref=3)])}, TypeError),
        ({"goal_ids": ("g",)}, TypeError),
        ({"goal_ids": [None]}, TypeError),
        ({"hypotheses": [["not", "a", "dict"]]}, TypeError),
        ({"hypotheses": [{"x": float("nan")}]}, ValueError),
    ],
)
def test_save_refuses_what_would_not_round_trip(over, error):
    with pytest.raises(error):
        save(_state(**over))
    with pytest.raises(LookupError):  # refused before the db was touched
        load("t")


def test_a_refused_save_keeps_the_previous_state():
    good = _state(working={"v": 1})
    save(good)
    with pytest.raises(ValueError):
        save(_state(working={"v": float("nan")}))
    assert load("t") == good


def test_save_refuses_a_non_task_state():
    with pytest.raises(TypeError):
        save({"task_id": "t"})


def test_next_step_is_the_first_step_not_done():
    steps = [Step(id=i, description=i, status=s) for i, s in
             [("a", DONE), ("b", DONE), ("c", FAILED), ("d", PENDING)]]
    assert Plan(steps=steps).next_step().id == "c"
    assert Plan(steps=steps[:2]).next_step() is None
    assert Plan().next_step() is None


def test_resume_after_load_uses_next_step():
    steps = [Step(id="a", description="a", status=DONE, result_ref="r/a"),
             Step(id="b", description="b", status=RUNNING, model="local/m")]
    save(_state(plan=Plan(steps=steps)))
    resumed = load("t").plan.next_step()
    assert (resumed.id, resumed.status, resumed.model) == ("b", RUNNING, "local/m")


def test_step_defaults():
    st = Step(id="a", description="d")
    assert (st.status, st.model, st.result_ref) == (PENDING, None, None)


def test_one_row_per_task_with_valid_json_columns():
    save(_state(working={"v": 1}, goal_ids=["g"]))
    save(_state(working={"v": 2}, goal_ids=["g", "h"]))
    with closing(open_db()) as conn:
        rows = conn.execute("SELECT task_id, working, plan, goal, hypotheses, updated_at FROM substrate_state").fetchall()
    assert len(rows) == 1
    task_id, working, plan, goal, hypotheses, updated_at = rows[0]
    assert task_id == "t" and updated_at
    assert json.loads(working) == {"v": 2}
    assert json.loads(goal) == ["g", "h"]
    assert json.loads(hypotheses) == []
    assert json.loads(plan)["steps"][0]["id"] == "a"


def test_load_of_a_row_with_a_null_column_is_a_value_error():
    save(_state())
    with closing(open_db()) as conn, conn:
        conn.execute("UPDATE substrate_state SET plan = NULL WHERE task_id = 't'")
    with pytest.raises(ValueError):
        load("t")


def test_json_columns_are_checked_by_the_schema():
    save(_state())
    with closing(open_db()) as conn, conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE substrate_state SET working = 'not json' WHERE task_id = 't'")


def test_negative_zero_and_big_ints_round_trip_exactly():
    s = _state(working={"nz": -0.0, "big": 10**30, "neg_big": -(10**30)})
    save(s)
    got = load("t").working
    assert str(got["nz"]) == "-0.0"
    assert got["big"] == 10**30 and type(got["big"]) is int
    assert got["neg_big"] == -(10**30)


def _raw(column):
    with closing(open_db()) as conn:
        return conn.execute(f"SELECT {column} FROM substrate_state WHERE task_id = 't'").fetchone()[0]


def test_secrets_are_redacted_everywhere_but_plain_text_is_kept_exactly():
    key = "ghp_" + "b" * 36
    s = _state(
        working={"password": "hunter2", "max_tokens": 5, "note": f"key {key} here", "plain": "a\ud800b"},
        plan=Plan(steps=[Step(id="a", description=f"call with {key}", result_ref="events/1")]),
        hypotheses=[{"h": "token=abc123"}],
    )
    save(s)
    for column in ("working", "plan", "hypotheses"):
        raw = _raw(column)
        assert key not in raw and "hunter2" not in raw and "abc123" not in raw
    got = load("t")
    assert got.working["max_tokens"] == 5 and got.working["plain"] == "a\ud800b"
    assert got.plan.steps[0].result_ref == "events/1"
    assert s.working["password"] == "hunter2"  # save did not alter its argument


@pytest.mark.parametrize("status", ["DONE", "Done", " done", "done\n", "", "done\u200b", "dоne", "do ne"])
def test_status_must_be_plain_lowercase_ascii(status):
    with pytest.raises(ValueError):
        save(_state(plan=Plan(steps=[Step(id="a", description="x", status=status)])))


@pytest.mark.parametrize("status", ["skipped", "waiting:tool", "retry-2", "in_progress"])
def test_other_plain_statuses_round_trip(status):
    s = _state(plan=Plan(steps=[Step(id="a", description="x", status=status)]))
    save(s)
    assert load("t") == s


@pytest.mark.parametrize("ids", [["a", "a"], [""], [" "], ["a", "b", "a"]])
def test_step_ids_must_be_unique_and_not_blank(ids):
    with pytest.raises(ValueError):
        save(_state(plan=Plan(steps=[Step(id=i, description="x") for i in ids])))


def test_any_value_under_a_secret_key_is_hidden():
    working = {"password": {"value": "hunter2"}, "auth": ["material"], "token": 987654321,
               "api_key": None, "secret": "", "nested": {"credentials": [{"user": "u", "pw": "p4ss"}]}}
    save(_state(working=working))
    raw = _raw("working")
    assert "hunter2" not in raw and "material" not in raw and "987654321" not in raw and "p4ss" not in raw
    got = load("t").working
    assert got["password"] == got["auth"] == got["token"] == got["nested"]["credentials"] == "[REDACTED]"
    assert got["api_key"] is None and got["secret"] == ""  # nothing to hide, kept as given


def test_error_message_does_not_quote_dict_keys():
    with pytest.raises(TypeError) as e:
        save(_state(working={"ok": {"API_KEY=hunter2hunter2": object()}}))
    assert "hunter2" not in str(e.value) and "working[key #0][key #0]" in str(e.value)


def test_load_of_lone_surrogate_id_is_lookup_error():
    with pytest.raises(LookupError):
        load("a\ud800")


@pytest.mark.parametrize("task_id", [" ", "\t\n"])
def test_blank_task_id_is_refused(task_id):
    with pytest.raises(ValueError):
        save(_state(task_id=task_id))


@pytest.mark.parametrize(("column", "value"), [
    ("plan", '{"steps": "x"}'),
    ("plan", '{"steps": [{"id": "a", "description": "d", "status": 1, "model": null, "result_ref": null}]}'),
    ("plan", "[]"),
    ("working", "[]"),
    ("goal", "[1]"),
    ("hypotheses", '["x"]'),
])
def test_wrong_shaped_row_loads_as_value_error(column, value):
    save(_state())
    with closing(open_db()) as conn, conn:
        conn.execute(f"UPDATE substrate_state SET {column} = ? WHERE task_id = 't'", (value,))
    with pytest.raises(ValueError):
        load("t")
