"""Red-team exam for AR1. Each test proves one break (see build/memory/runs/redteam-AR1-20261009.md).

They are xfail(strict=True): they fail today because the break is real. When a fix lands the test XPASSes,
strict mode turns that into an error, and the fixer removes the marker. Do not delete or weaken them.
"""
import dataclasses
import os
import subprocess
import sys
import tempfile
import time

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


def test_candidate_cannot_read_the_verifier_answer(outer_db):
    res = run([_task()], "cfg", seeds=1, executor=_ex("reads_verifier_module"))
    assert res.results[0].score == 0.0


def test_candidate_cannot_patch_the_verifier(outer_db):
    res = run([_task()], "cfg", seeds=1, executor=_ex("patches_verifier"))
    assert res.results[0].score == 0.0


def test_executor_does_not_see_the_verifier_path(outer_db):
    res = run([_task("verifier_visible")], "cfg", seeds=1, executor=_ex("sees_task_verifier"))
    assert res.results[0].score == 1.0


def test_candidate_that_does_not_exist_cannot_score_without_an_executor(outer_db):
    # decision 0012 item 1: no executor means the candidate is never used, so run() refuses and writes nothing
    with pytest.raises(ValueError):
        run([_task("always_one")], "refs/heads/does-not-exist-anywhere", seeds=1)
    assert _rows(outer_db) == []


def test_verifier_reasons_do_not_leak_the_expected_answer():
    assert "hidden-42" not in exact("nope", "hidden-42").reason
    assert "31337" not in number("1", 31337).reason


def test_task_set_id_changes_when_a_task_changes():
    a = _task()
    easier = dataclasses.replace(a, verifier=f"{M}.always_one", prompt="easy now", timeout_s=999)
    assert task_set_id([a]) != task_set_id([easier])


def test_task_set_id_has_no_separator_collision():
    assert task_set_id([_task(id="a\nb")]) != task_set_id([_task(id="a", n=1), _task(id="b", n=2)])


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


def test_huge_timeout_does_not_crash_the_run(outer_db):
    ok = _task("always_one", n=1)
    huge = _task("always_one", n=2, timeout_s=1e300)
    res = run([ok, huge], "cfg", seeds=1, executor=_ex("reads_verifier_module"))
    assert len(res.results) == 2


def test_crash_reason_does_not_store_prompt_or_env(outer_db, monkeypatch):
    monkeypatch.setenv("RT_FAKE_KEY", "sk-test-NOT-A-REAL-KEY")
    run([_task(prompt="my private diary entry")], "cfg", seeds=1, executor=_ex("raises_with_prompt"))
    stored = " ".join(r[4] for r in _rows(outer_db))
    assert "my private diary entry" not in stored
    assert "sk-test-NOT-A-REAL-KEY" not in stored


def test_partial_run_leaves_no_rows(outer_db, tmp_path):
    # decision 0012 item 7: rows are written in one transaction at the end, so a run killed after its first
    # attempt finished leaves nothing behind. The second attempt waits until the parent kills the whole run.
    (tmp_path / "hang_second.py").write_text(
        "import pathlib, time\n"
        "def ex(brief, candidate, seed):\n"
        "    if brief.id == 'rt-2':\n"
        f"        pathlib.Path({str(tmp_path / 'second_started')!r}).write_text('x')\n"
        "        time.sleep(20)\n"
        "    return 'an answer'\n"
    )
    script = tmp_path / "child.py"
    script.write_text(
        "import sys\n"
        f"sys.path[:0] = {sys.path!r}\n"
        f"sys.path[:0] = [{str(tmp_path)!r}, {os.path.dirname(__file__)!r}]\n"
        "from hang_second import ex\n"
        "from test_redteam_ar1 import _task\n"
        "from tanishi.arena.runner import run\n"
        "run([_task('always_one', n=1), _task('always_one', n=2)], 'cfg', seeds=1, executor=ex)\n"
    )
    proc = subprocess.Popen([sys.executable, str(script)], env=os.environ.copy())
    try:
        deadline = time.monotonic() + 60
        while not (tmp_path / "second_started").exists():
            assert proc.poll() is None and time.monotonic() < deadline, "the run ended before the second attempt began"
            time.sleep(0.05)
        proc.kill()  # the first attempt has finished; the run dies before it can write
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert _rows(outer_db) == []
