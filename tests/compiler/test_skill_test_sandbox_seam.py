"""SB1 ↔ L1 seam: pass 4's skill test runs inside `tanishi.sandbox.run_python`, and the SandboxUnavailable fallback
is for template code only (decisions 0025 §9 and 0026, seam round)."""
from __future__ import annotations

import pytest

from tanishi import sandbox
from tanishi.compiler import experience as xp
from tanishi.compiler import passes
from tanishi.core_state import events, predictions

CODE = "def read(p):\n    with open(p) as f:\n        return f.read()\n"
SECRET = "seam-canary-7f3a"


def _isolating_backend_or_skip():
    try:
        with sandbox.Sandbox(trusted=False, network=False, cpu_seconds=5, wall_seconds=5):
            pass
    except sandbox.SandboxUnavailable as e:
        pytest.skip(f"no isolating sandbox here: {e}")


def _exp():
    pid = predictions.predict("task:sb1-seam", {"success": True}, 0.7)
    return xp.Experience(task="t", context={}, outcome={"success": False},
                         actions=[{"tool": "calc", "input": {"a": 1}, "output": "division by zero", "ok": False}],
                         verifier_result={"passed": False, "score": 0.0, "reason": "r"}, cost={"usd": 0.0},
                         prediction={"id": pid, "expected": {"success": True}, "confidence": 0.7})


def _unavailable(script, *, stdin=b"", timeout_s):
    raise sandbox.SandboxUnavailable("no Docker on this Mac (test)")


def test_the_skill_test_runs_in_the_sandbox_and_cannot_read_a_file_outside_its_scratch(tmp_path, monkeypatch):
    _isolating_backend_or_skip()
    host_file = tmp_path / "outside.txt"
    host_file.write_text(SECRET)
    test = f"def test_reads_the_host_file():\n    assert read({str(host_file)!r}) == {SECRET!r}\n"

    calls = []
    real = sandbox.run_python

    def spy(script, **kw):
        res = real(script, **kw)
        calls.append(res)
        return res

    monkeypatch.setattr(sandbox, "run_python", spy)
    ok, why = passes.run_skill_test(CODE, test)
    assert len(calls) == 1, "the skill test did not go through tanishi.sandbox.run_python"
    assert ok is False, "the sandboxed skill test read a host file outside its scratch"
    assert why == "test failed"

    # Control: the same test passes when it is not sandboxed, so the refusal above is the sandbox's doing.
    monkeypatch.setattr(passes, "_sandbox", lambda: None)
    assert passes.run_skill_test(CODE, test) == (True, "1 test(s) passed")


def test_pass4_lands_through_the_real_sandbox_with_no_unsandboxed_event():
    _isolating_backend_or_skip()
    cands = [e for e in passes.compile_experience(_exp()) if e.kind == "skill_candidate"]
    assert [c.landed for c in cands] == [True], [c.reason for c in cands]
    assert list(events.iter_events(kind=passes.UNSANDBOXED_EVENT)) == []


def test_template_code_falls_back_with_a_logged_reason_when_the_sandbox_is_unavailable(monkeypatch):
    monkeypatch.setattr(sandbox, "run_python", _unavailable)
    cands = [e for e in passes.compile_experience(_exp()) if e.kind == "skill_candidate"]
    assert [c.landed for c in cands] == [True], [c.reason for c in cands]
    logged = [e.payload for e in events.iter_events(kind=passes.UNSANDBOXED_EVENT)]
    assert logged == [{"reason": "no Docker on this Mac (test)"}]


def test_other_code_never_takes_the_fallback(monkeypatch):
    monkeypatch.setattr(sandbox, "run_python", _unavailable)
    ok, why = passes.run_skill_test("def f():\n    return 1\n", "def test_f():\n    assert f() == 1\n")
    assert (ok, why) == (False, "test could not run: SandboxUnavailable")
    assert list(events.iter_events(kind=passes.UNSANDBOXED_EVENT)) == []


def test_other_sandbox_errors_are_still_refusals_for_template_code(monkeypatch):
    def broken(script, *, stdin=b"", timeout_s):
        raise OSError("docker died")

    monkeypatch.setattr(sandbox, "run_python", broken)
    ok, why = passes.run_skill_test("def f():\n    return 1\n", "def test_f():\n    assert f() == 1\n",
                                    from_template=True)
    assert (ok, why) == (False, "test could not run: OSError")


def test_run_python_takes_what_l1_sends():
    _isolating_backend_or_skip()
    script = "import sys\nsys.stdout.write(sys.stdin.read().upper())\n"
    for src, stdin, timeout_s in ((script, "abc", 20), (script.encode(), b"abc", 20.0)):
        res = sandbox.run_python(src, stdin=stdin, timeout_s=timeout_s)
        returncode, stdout = res
        assert (returncode, stdout, res.stderr) == (0, "ABC", "")
    assert sandbox.run_python("print(1)\n", timeout_s=20).stdout == "1\n"
