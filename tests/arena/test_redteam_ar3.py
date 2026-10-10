"""Red-team exam for AR3 (see build/memory/runs/redteam-AR3-20261010.md). Each test proves one break.

They are xfail(strict=True): each fails today because the break is real. When a fix lands the test XPASSes, strict
mode turns that into an error, and the fixer removes the marker. Do not delete or weaken them.
"""
import json
import math
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tanishi.arena import frontier_service as fs
from tanishi.arena.frontier_service import FrontierService

PROMPT = "RT3-SECRET-PROMPT-abc"
NEEDLES = ["apple", "mango", "pear", "plum", "fig", "kiwi", "lime", "date"]
HIDDEN = "kiwi"

_SRC = textwrap.dedent(
    '''
    import os
    from tanishi.arena.runner import Attempt
    from tanishi.arena.verifiers import contains

    DROP = __DROP__
    STORE = __STORE__

    def good(brief, candidate, seed):
        return Attempt("42", cost=0.1)

    def cost_carries_prompt(brief, candidate, seed):
        return Attempt("42", cost=float(int.from_bytes(brief.prompt.encode()[:6], "big")))

    def huge_cost(brief, candidate, seed):
        return Attempt("42", cost=1.7e308)

    def drops_prompt(brief, candidate, seed):
        with open(DROP, "w") as f:
            f.write(brief.prompt)
        return Attempt("42")

    def reads_store(brief, candidate, seed):
        try:
            seen = [open(os.path.join(STORE, n)).read() for n in os.listdir(STORE) if n.endswith(".yaml")]
        except OSError:
            seen = []
        with open(DROP, "w") as f:
            f.write(chr(10).join(seen))
        return Attempt("42")

    def echo_candidate(brief, candidate, seed):
        return Attempt(candidate.replace(",", " "))

    def exact(task, output):
        return (1.0, "ok") if output.strip() == "42" else (0.0, "does not match")

    def has_needle(task, output):
        return contains(output, "__HIDDEN__")
    '''
)

_CLI_SRC = textwrap.dedent(
    """
    import glob
    import json

    from rt_ar3_fx import good  # noqa: F401

    _orig = json.dumps


    def _dumps(obj, *a, **k):
        if not (isinstance(obj, list) and obj and isinstance(obj[0], dict) and "task_id_hash" in obj[0]):
            return _orig(obj, *a, **k)
        text = open(glob.glob(__STORE__ + "/*.yaml")[0]).read()
        return _orig([dict(e, leak=text) for e in obj], *a, **k)


    json.dumps = _dumps
    """
)


@pytest.fixture
def fx(tmp_path, monkeypatch):
    d = tmp_path / "rtmod"
    d.mkdir()
    store = tmp_path / "store"
    store.mkdir(mode=0o700)
    store.chmod(0o700)
    drop = tmp_path / "drop.txt"
    src = _SRC.replace("__DROP__", repr(str(drop))).replace("__STORE__", repr(str(store))).replace("__HIDDEN__", HIDDEN)
    (d / "rt_ar3_fx.py").write_text(src)
    monkeypatch.syspath_prepend(str(d))
    sys.modules.pop("rt_ar3_fx", None)
    import rt_ar3_fx

    return SimpleNamespace(mod=rt_ar3_fx, store=store, drop=drop, moddir=d)


def _task(store: Path, tid="t1", *, verifier="rt_ar3_fx.exact", prompt=PROMPT, ext="yaml"):
    p = store / f"{tid}.{ext}"
    p.write_text(yaml.safe_dump({"id": tid, "family": "f", "tier": "frontier", "prompt": prompt,
                                 "verifier": verifier, "timeout_s": 20}))
    p.chmod(0o600)
    return p


# --- RT-1..4 (high): candidate code runs unsandboxed as the service user, so the boundary is only a convention ------


def test_cost_cannot_carry_the_prompt_across_the_boundary(fx):
    _task(fx.store)
    (e,) = FrontierService(fx.store, executor=fx.mod.cost_carries_prompt).evaluate("c")
    assert e["cost"] != float(int.from_bytes(PROMPT.encode()[:6], "big"))


@pytest.mark.xfail(strict=True, reason="RT-AR3-2: the executor can write the prompt anywhere the service user can")
def test_executor_cannot_write_the_prompt_outside_its_home(fx):
    _task(fx.store)
    FrontierService(fx.store, executor=fx.mod.drops_prompt).evaluate("c")
    assert not fx.drop.exists() or PROMPT not in fx.drop.read_text()


@pytest.mark.xfail(strict=True, reason="RT-AR3-3: the executor runs as the store's owner, so it can read every task")
def test_executor_cannot_read_the_store(fx):
    _task(fx.store, "t1", prompt="first")
    _task(fx.store, "t2", prompt=PROMPT)
    FrontierService(fx.store, executor=fx.mod.reads_store).evaluate("c")
    assert not fx.drop.exists() or PROMPT not in fx.drop.read_text()


def test_cli_output_is_only_scores_even_for_a_hostile_executor_module(fx):
    _task(fx.store)
    (fx.moddir / "rt_ar3_cli.py").write_text(_CLI_SRC.replace("__STORE__", repr(str(fx.store))))
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(fx.moddir), str(repo)]))
    out = subprocess.run([sys.executable, "-m", "tanishi.arena.frontier_service", str(fx.store), "c",
                          "--executor", "rt_ar3_cli.good"], cwd=fx.moddir, env=env, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert PROMPT not in out.stdout


# --- RT-5 (medium): oracle with no query budget ---------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="RT-AR3-5: no query budget; a hidden needle falls to a bisection in log2(n) calls")
def test_a_hidden_needle_cannot_be_bisected_out_of_the_scores(fx):
    _task(fx.store, verifier="rt_ar3_fx.has_needle")
    svc = FrontierService(fx.store, executor=fx.mod.echo_candidate)
    pool = list(NEEDLES)
    while len(pool) > 1:
        half = pool[: len(pool) // 2]
        (e,) = svc.evaluate(",".join(half))
        pool = half if e["score"] == 1.0 else pool[len(pool) // 2:]
    assert pool != [HIDDEN]


# --- RT-6 (medium): a crashed first run bricks the store ------------------------------------------------------------


def test_a_salt_left_empty_by_a_crash_does_not_brick_the_store(fx):
    _task(fx.store)
    salt = fx.store / ".salt"
    salt.write_bytes(b"")
    salt.chmod(0o600)
    assert len(FrontierService(fx.store, executor=fx.mod.good).evaluate("c")) == 1


# --- RT-7 (medium): process-global env swap corrupts the caller's env when two evaluations overlap ------------------


def test_overlapping_evaluations_restore_the_callers_db_path(fx, monkeypatch):
    _task(fx.store)
    before = os.environ["TANISHI_CORE_STATE_DB"]
    a_inside, b_inside, a_done = threading.Event(), threading.Event(), threading.Event()

    def fake_run(tasks, candidate, seeds, executor=None, env_passthrough=()):
        if threading.current_thread().name == "A":
            a_inside.set()
            b_inside.wait(10)
        else:
            b_inside.set()
            a_done.wait(10)
        return SimpleNamespace(results=())

    monkeypatch.setattr(fs, "run", fake_run)

    def go(name):
        FrontierService(fx.store, executor=fx.mod.good).evaluate("c")
        if name == "A":
            a_done.set()

    a = threading.Thread(target=go, args=("A",), name="A")
    b = threading.Thread(target=go, args=("B",), name="B")
    a.start()
    assert a_inside.wait(10)
    b.start()
    a.join(20)
    b.join(20)
    assert os.environ["TANISHI_CORE_STATE_DB"] == before


# --- RT-8..11 (medium/low): a wrong or hostile input gives a normal-looking answer ----------------------------------


def test_seeds_are_capped(fx, monkeypatch):
    _task(fx.store)

    def boom(*a, **k):
        raise RuntimeError("the run started")

    monkeypatch.setattr(fs, "_run_privately", boom)
    with pytest.raises(ValueError):
        FrontierService(fx.store, executor=fx.mod.good).evaluate("c", seeds=10**6)


def test_cost_stays_finite_and_json_valid(fx):
    _task(fx.store)
    out = FrontierService(fx.store, executor=fx.mod.huge_cost).evaluate("c", seeds=2)
    assert all(math.isfinite(e["cost"]) for e in out)
    json.dumps(out, allow_nan=False)


def test_a_task_with_a_missing_verifier_is_refused_not_scored_zero(fx):
    _task(fx.store, "t1")
    _task(fx.store, "t2", verifier="no_such_module_rt_ar3.check")
    with pytest.raises(Exception):  # noqa: B017 - any refusal will do; a returned score list is the break
        FrontierService(fx.store, executor=fx.mod.good).evaluate("c")


def test_a_task_file_with_another_extension_is_refused_not_skipped(fx):
    _task(fx.store, "t1")
    _task(fx.store, "t2", ext="yml")
    with pytest.raises(ValueError):
        FrontierService(fx.store, executor=fx.mod.good).evaluate("c")
