"""Red-team exam for AR1. Each test proves one break (see build/memory/runs/redteam-AR1-20261009.md).

They are xfail(strict=True): they fail today because the break is real. When a fix lands the test XPASSes,
strict mode turns that into an error, and the fixer removes the marker. Do not delete or weaken them.
"""
import contextlib
import dataclasses
import json
import os
import tempfile

import pytest

from tanishi.arena.runner import run, task_set_id
from tanishi.arena.task import Task
from tanishi.arena.verifiers import exact, number

M = "redteam_modules"


def _task(verifier="check_secret", n=1, **over):
    fields = {"id": f"rt-{n}", "family": "rt", "tier": "practice", "prompt": "what is the answer?",
              "verifier": f"{M}.{verifier}", "timeout_s": 10, "tags": []}
    fields.update(over)
    return Task(**fields)


def _ex(name):
    import redteam_modules

    return getattr(redteam_modules, name)


def _rows(conn):
    return conn.execute("SELECT task_set, seed, score, cost, meta FROM experiments ORDER BY ts").fetchall()


@pytest.mark.xfail(strict=True, reason="RT-1 high: candidate reads the answer out of the verifier module")
def test_candidate_cannot_read_the_verifier_answer(outer_db):
    res = run([_task()], "cfg", seeds=1, executor=_ex("reads_verifier_module"))
    assert res.results[0].score == 0.0


@pytest.mark.xfail(strict=True, reason="RT-1 high: candidate patches the verifier in the shared process")
def test_candidate_cannot_patch_the_verifier(outer_db):
    res = run([_task()], "cfg", seeds=1, executor=_ex("patches_verifier"))
    assert res.results[0].score == 0.0


@pytest.mark.xfail(strict=True, reason="RT-1 high: executor receives the Task including its verifier path")
def test_executor_does_not_see_the_verifier_path(outer_db):
    res = run([_task("verifier_visible")], "cfg", seeds=1, executor=_ex("sees_task_verifier"))
    assert res.results[0].score == 1.0


@pytest.mark.xfail(strict=True, reason="RT-2 high: with no executor the candidate is never used, any ref scores 1.0")
def test_candidate_that_does_not_exist_cannot_score_without_an_executor(outer_db):
    res = run([_task("always_one")], "refs/heads/does-not-exist-anywhere", seeds=1)
    assert res.results[0].score == 0.0


@pytest.mark.xfail(strict=True, reason="RT-3 medium: verifier reason echoes the expected answer into rows and the loop")
def test_verifier_reasons_do_not_leak_the_expected_answer():
    assert "hidden-42" not in exact("nope", "hidden-42").reason
    assert "31337" not in number("1", 31337).reason


@pytest.mark.xfail(strict=True, reason="RT-4 medium: task_set id ignores everything but task ids")
def test_task_set_id_changes_when_a_task_changes():
    a = _task()
    easier = dataclasses.replace(a, verifier=f"{M}.always_one", prompt="easy now", timeout_s=999)
    assert task_set_id([a]) != task_set_id([easier])


@pytest.mark.xfail(strict=True, reason="RT-4 low: ids joined with newline collide")
def test_task_set_id_has_no_separator_collision():
    assert task_set_id([_task(id="a\nb")]) != task_set_id([_task(id="a", n=1), _task(id="b", n=2)])


@pytest.mark.xfail(strict=True, reason="RT-5 medium: attempts share the system tmp dir, so seeds are not independent")
def test_attempts_do_not_share_tmp(outer_db):
    marker = os.path.join(tempfile.gettempdir(), "rt-marker")
    if os.path.exists(marker):
        os.remove(marker)
    try:
        res = run([_task("seen_marker")], "cfg", seeds=2, executor=_ex("leaves_marker_in_tmp"))
    finally:
        if os.path.exists(marker):
            os.remove(marker)
    assert [r.score for r in res.results] == [1.0, 1.0]


@pytest.mark.xfail(strict=True, reason="RT-6 high: huge timeout_s raises OverflowError out of run(), run dies mid-way")
def test_huge_timeout_does_not_crash_the_run(outer_db):
    ok = _task("always_one", n=1)
    huge = _task("always_one", n=2, timeout_s=1e300)
    res = run([ok, huge], "cfg", seeds=1, executor=_ex("reads_verifier_module"))
    assert len(res.results) == 2


@pytest.mark.xfail(strict=True, reason="RT-7 medium: exception text (prompt, env values) is stored in experiments.meta")
def test_crash_reason_does_not_store_prompt_or_env(outer_db, monkeypatch):
    monkeypatch.setenv("RT_FAKE_KEY", "sk-test-NOT-A-REAL-KEY")
    run([_task(prompt="my private diary entry")], "cfg", seeds=1, executor=_ex("raises_with_prompt"))
    stored = " ".join(r[4] for r in _rows(outer_db))
    assert "my private diary entry" not in stored
    assert "sk-test-NOT-A-REAL-KEY" not in stored


@pytest.mark.xfail(strict=True, reason="RT-8 medium: a run that dies half way leaves rows indistinguishable from a full run")
def test_partial_run_is_marked_incomplete(outer_db):
    first = _task("always_one", n=1)
    bad = _task("always_one", n=2, timeout_s=1e300)  # RT-6 is the easiest way to kill run() half way
    with contextlib.suppress(OverflowError):  # the break itself (RT-6)
        run([first, bad], "cfg", seeds=1, executor=_ex("reads_verifier_module"))
    metas = [json.loads(r[4]) for r in _rows(outer_db)]
    assert metas and all(m.get("complete") is False or "incomplete" in m for m in metas)
