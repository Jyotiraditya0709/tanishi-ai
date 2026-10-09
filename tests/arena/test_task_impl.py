"""AR1 implementer tests: task file strictness, the timeout cap, the brief and the task set id."""
import dataclasses

import pytest

from tanishi.arena.runner import task_set_id
from tanishi.arena.task import MAX_TIMEOUT_S, Task, TaskBrief, load_task
from tanishi.arena.verifiers import exact, number

YAML = """\
id: t-1
family: math
tier: practice
prompt: What is 2+2?
verifier: some.module.check
timeout_s: 10
tags: []
"""


def _task(**over):
    fields = {"id": "t-1", "family": "f", "tier": "practice", "prompt": "p", "verifier": "a.b", "timeout_s": 5}
    fields.update(over)
    return Task(**fields)


@pytest.mark.parametrize("dup", ["timeout_s: 9999", "verifier: other.module.always_one", "id: t-2"])
def test_duplicate_yaml_keys_are_refused(tmp_path, dup):
    p = tmp_path / "t.yaml"
    p.write_text(YAML + dup + "\n")
    with pytest.raises(ValueError, match="duplicate key"):
        load_task(p)


def test_duplicate_keys_in_nested_mappings_are_refused_too(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text(YAML.replace("tags: []", "tags: [{a: 1, a: 2}]"))
    with pytest.raises(ValueError, match="duplicate key"):
        load_task(p)


def test_a_file_without_duplicates_still_loads(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text(YAML)
    assert load_task(p).id == "t-1"


@pytest.mark.parametrize("t,expected", [(1e300, MAX_TIMEOUT_S), (3601, MAX_TIMEOUT_S), (3600, 3600), (0.5, 0.5)])
def test_timeout_is_capped_at_an_hour(t, expected):
    assert _task(timeout_s=t).timeout_s == expected
    assert dataclasses.replace(_task(), timeout_s=t).timeout_s == expected


def test_brief_carries_everything_but_verifier_and_setup():
    t = _task(tags=["x"], setup="echo hi")
    b = t.brief()
    assert isinstance(b, TaskBrief)
    assert (b.id, b.family, b.tier, b.prompt, b.timeout_s, b.tags) == (t.id, t.family, t.tier, t.prompt, 5, ("x",))
    assert not hasattr(b, "verifier") and not hasattr(b, "setup")


def test_task_set_id_ignores_order_and_follows_every_field():
    a, b = _task(id="a"), _task(id="b")
    assert task_set_id([a, b]) == task_set_id([b, a])
    base = task_set_id([a])
    for change in ({"family": "g"}, {"tier": "frontier"}, {"prompt": "q"}, {"verifier": "a.c"}, {"timeout_s": 6},
                   {"tags": ["new"]}, {"setup": "true"}):
        assert task_set_id([dataclasses.replace(a, **change)]) != base, change


def test_reasons_never_carry_the_expected_answer():
    assert exact("x", "secret-word").reason == "does not match"
    assert "secret-word" not in exact("secret-word", "secret-word").reason
    assert number("1", 98765).reason == "wrong number"
    assert "98765" not in number("98765", 98765).reason
