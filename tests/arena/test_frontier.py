"""AR3 exam, written from the spec only (build/graph.yaml node AR3), before and apart from the implementation.

Acceptance lines under test:
  1. The API returns only {task_id_hash, score, cost}; task text and verifier code never cross the boundary.
  2. Tasks are stored under a path the agent OS user cannot read.

The card names a file (tanishi/arena/frontier_service.py) but no function names. The names used here are assumptions
A1-A9 in build/memory/open-problems/AR3-exam-assumptions.md. If the implementer disagrees with one, say so in
build/memory/open-problems/AR3-exam-conflicts.md; do not edit the tests.
"""
import json
import os
import re
import stat
import sys
import tempfile
import textwrap
import uuid
from pathlib import Path

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

REPO_ROOT = Path(__file__).resolve().parents[2]
KEYS = {"task_id_hash", "score", "cost"}

# Strings that must never be visible to the caller of the service. Each lives in exactly one place.
C_PROMPT = "CANARY-PROMPT-5f3a91"
C_ANSWER = "CANARY-ANSWER-77c2d0"
C_SOURCE = "CANARY-VERIFIER-SOURCE-b81e44"
C_ERROR = "CANARY-VERIFIER-ERROR-0d9e12"
C_ID = "canary-task-id-6a7b"

_FIXTURE_SRC = textwrap.dedent(
    '''
    # __C_SOURCE__  (a marker inside the verifier's source code)
    from tanishi.arena.runner import Attempt

    SECRET = "__C_ANSWER__"

    def good(brief, candidate, seed):
        return Attempt(SECRET, cost=0.25)

    def wrong(brief, candidate, seed):
        return Attempt("not the answer", cost=0.5)

    def crash(brief, candidate, seed):
        raise RuntimeError("candidate blew up")

    def empty(brief, candidate, seed):
        return Attempt("   ", cost=0.5)

    def exact(task, output):
        # A deliberately leaky reason: the service must not pass it on.
        if output.strip() == SECRET:
            return 1.0, "matched " + SECRET
        return 0.0, "expected " + SECRET + " got " + output

    def explode(task, output):
        raise RuntimeError("__C_ERROR__")

    def half(task, output):
        return 0.5, "half"
    '''
).replace("__C_SOURCE__", C_SOURCE).replace("__C_ANSWER__", C_ANSWER).replace("__C_ERROR__", C_ERROR)


@pytest.fixture
def fx(tmp_path, monkeypatch):
    """Importable module `frontier_fx` holding the exam's executors and verifiers."""
    mod_dir = tmp_path / "fxmod"
    mod_dir.mkdir()
    (mod_dir / "frontier_fx.py").write_text(_FIXTURE_SRC)
    monkeypatch.syspath_prepend(str(mod_dir))
    sys.modules.pop("frontier_fx", None)
    import frontier_fx

    return frontier_fx


def _write_task(store: Path, tid: str, *, verifier="exact", prompt=None, **over):
    data = {
        "id": tid, "family": "frontier", "tier": "frontier", "prompt": prompt or f"{C_PROMPT} {tid}",
        "verifier": f"frontier_fx.{verifier}", "timeout_s": 20, "tags": ["frontier"],
    }
    data.update(over)
    path = store / f"{tid}.yaml"
    path.write_text(yaml.safe_dump(data))
    path.chmod(0o600)
    return path


def _private_store(parent: Path, name="frontier_store") -> Path:
    store = parent / name
    store.mkdir(mode=0o700)
    store.chmod(0o700)
    return store


@pytest.fixture
def store(tmp_path):
    return _private_store(tmp_path)


def _service(store, executor):
    from tanishi.arena.frontier_service import FrontierService

    return FrontierService(store, executor=executor)


def _core_state_bytes() -> bytes:
    db = Path(os.environ["TANISHI_CORE_STATE_DB"])
    blob = b""
    if db.parent.exists():
        for p in db.parent.iterdir():
            if p.name.startswith(db.name):  # the db, its -wal and -shm
                blob += p.read_bytes()
    return blob


def _assert_clean(text: str, *needles: str):
    for n in needles:
        assert n not in text, f"{n!r} crossed the boundary"


ALL_CANARIES = (C_PROMPT, C_ANSWER, C_SOURCE, C_ERROR, C_ID)


# ---------------------------------------------------------------- acceptance 1: the shape of the answer


def test_returns_exactly_three_fields_per_task(fx, store):
    _write_task(store, "t1")
    _write_task(store, "t2")
    out = _service(store, fx.good).evaluate("cand-a")
    assert isinstance(out, list) and len(out) == 2
    for entry in out:
        assert type(entry) is dict
        assert set(entry) == KEYS


def test_result_is_plain_json(fx, store):
    _write_task(store, "t1")
    out = _service(store, fx.good).evaluate("cand-a")
    assert json.loads(json.dumps(out)) == out


def test_score_and_cost_values(fx, store):
    _write_task(store, "right")
    out = _service(store, fx.good).evaluate("cand-a")
    (entry,) = out
    assert entry["score"] == 1.0
    assert entry["cost"] == pytest.approx(0.25)
    assert isinstance(entry["task_id_hash"], str) and entry["task_id_hash"]


def test_wrong_answer_scores_low_and_pays_its_cost(fx, store):
    _write_task(store, "t1")
    (entry,) = _service(store, fx.wrong).evaluate("cand-a")
    assert entry["score"] == 0.0
    assert entry["cost"] == pytest.approx(0.5)


def test_partial_score_passes_through(fx, store):
    _write_task(store, "t1", verifier="half")
    (entry,) = _service(store, fx.good).evaluate("cand-a")
    assert entry["score"] == pytest.approx(0.5)


def test_hash_is_not_the_task_id_and_hides_it(fx, store):
    _write_task(store, C_ID)
    (entry,) = _service(store, fx.good).evaluate("cand-a")
    assert entry["task_id_hash"] != C_ID
    assert C_ID not in entry["task_id_hash"]
    assert re.fullmatch(r"[0-9a-f]{16,}", entry["task_id_hash"]), "expected a hex digest of at least 64 bits"


def test_hash_is_stable_across_calls_candidates_and_instances(fx, store):
    _write_task(store, "t1")
    _write_task(store, "t2")
    svc = _service(store, fx.good)
    a = {e["task_id_hash"] for e in svc.evaluate("cand-a")}
    b = {e["task_id_hash"] for e in svc.evaluate("cand-b")}
    c = {e["task_id_hash"] for e in _service(store, fx.good).evaluate("cand-a")}
    assert len(a) == 2, "two tasks must have two different hashes"
    assert a == b == c, "the same task must keep one hash, or scores cannot be compared across candidates"


def test_seeds_do_not_multiply_the_fields(fx, store):
    _write_task(store, "t1")
    out = _service(store, fx.good).evaluate("cand-a", seeds=3)
    assert out, "a three-seed run must still report something"
    for entry in out:
        assert set(entry) == KEYS
        assert 0.0 <= entry["score"] <= 1.0


# ---------------------------------------------------------------- acceptance 1: nothing from the task crosses


def test_no_task_text_or_verifier_data_in_the_result(fx, store):
    _write_task(store, C_ID)
    out = _service(store, fx.good).evaluate("cand-a")
    _assert_clean(json.dumps(out) + repr(out), *ALL_CANARIES)


def test_leaky_verifier_reason_is_not_returned(fx, store):
    """The verifier's reason carries the expected answer; the service must drop reasons entirely."""
    _write_task(store, "t1")
    out = _service(store, fx.wrong).evaluate("cand-a")
    _assert_clean(json.dumps(out), C_ANSWER, "expected", "got")


def test_verifier_exception_text_is_not_returned_or_raised(fx, store):
    _write_task(store, "t1", verifier="explode")
    out = _service(store, fx.good).evaluate("cand-a")  # must not raise
    (entry,) = out
    assert entry["score"] == 0.0
    _assert_clean(json.dumps(out), C_ERROR, "RuntimeError", "Traceback")


def test_candidate_output_is_not_returned(fx, store):
    _write_task(store, "t1")
    out = _service(store, fx.good).evaluate("cand-a")
    _assert_clean(json.dumps(out), C_ANSWER)


def test_core_state_db_holds_no_task_text_or_raw_id(fx, store):
    """Rows the runner writes into the Core State are readable by the agent. They must not carry the task."""
    _write_task(store, C_ID)
    _service(store, fx.good).evaluate("cand-a")
    blob = _core_state_bytes()
    for n in ALL_CANARIES:
        assert n.encode() not in blob, f"{n!r} was written to the agent-readable Core State"


def test_nothing_printed_or_logged(fx, store, capsys, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    _write_task(store, C_ID)
    _service(store, fx.wrong).evaluate("cand-a")
    captured = capsys.readouterr()
    _assert_clean(captured.out + captured.err + caplog.text, *ALL_CANARIES)


def test_service_object_exposes_no_task_accessors(fx, store):
    _write_task(store, C_ID)
    svc = _service(store, fx.good)
    svc.evaluate("cand-a")
    _assert_clean(repr(svc), *ALL_CANARIES)
    banned = re.compile(r"task|prompt|verifier|source|answer|expected", re.IGNORECASE)
    public = [n for n in dir(svc) if not n.startswith("_")]
    assert [n for n in public if banned.search(n)] == [], "the public surface must not hand out task material"
    for name in public:
        value = getattr(svc, name)
        if not callable(value):
            _assert_clean(repr(value), *ALL_CANARIES)


def test_errors_from_bad_input_do_not_quote_tasks(fx, store):
    _write_task(store, C_ID)
    svc = _service(store, fx.good)
    for bad in ("", "   ", None):
        with pytest.raises((ValueError, TypeError)) as exc:
            svc.evaluate(bad)
        _assert_clean(str(exc.value), *ALL_CANARIES)
    with pytest.raises(ValueError) as exc:
        svc.evaluate("cand-a", seeds=0)
    _assert_clean(str(exc.value), *ALL_CANARIES)


# ---------------------------------------------------------------- failures never score positive (AR1 contract)


def test_crashed_candidate_scores_zero(fx, store):
    _write_task(store, "t1")
    (entry,) = _service(store, fx.crash).evaluate("cand-a")
    assert entry["score"] == 0.0
    assert set(entry) == KEYS


def test_empty_output_scores_zero(fx, store):
    _write_task(store, "t1")
    (entry,) = _service(store, fx.empty).evaluate("cand-a")
    assert entry["score"] == 0.0


def test_a_broken_task_file_does_not_hide_the_others_or_leak(fx, store):
    _write_task(store, "good")
    bad = store / "bad.yaml"
    bad.write_text(f"id: broken\nprompt: {C_PROMPT}\nfamily: [unterminated\n")
    bad.chmod(0o600)
    try:
        out = _service(store, fx.good).evaluate("cand-a")
    except (ValueError, TypeError) as e:
        _assert_clean(str(e), *ALL_CANARIES)  # refusing is allowed; quoting the task is not
        return
    _assert_clean(json.dumps(out), *ALL_CANARIES)
    assert any(e["score"] == 1.0 for e in out)


def test_empty_store_returns_no_scores_and_no_fake_ones(fx, store):
    try:
        out = _service(store, fx.good).evaluate("cand-a")
    except ValueError:
        return
    assert out == []


def test_service_requires_an_executor(fx, store):
    from tanishi.arena.frontier_service import FrontierService

    _write_task(store, "t1")
    with pytest.raises((ValueError, TypeError)):
        FrontierService(store, executor=None).evaluate("cand-a")


# ---------------------------------------------------------------- acceptance 2: where tasks live


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_private_store_is_accepted_and_left_private(fx, store):
    t = _write_task(store, "t1")
    out = _service(store, fx.good).evaluate("cand-a")
    assert len(out) == 1
    assert _mode(store) & 0o077 == 0
    assert _mode(t) & 0o077 == 0


def test_group_or_world_readable_store_is_refused_or_tightened(fx, tmp_path):
    store = tmp_path / "open_store"
    store.mkdir()
    store.chmod(0o755)
    t = _write_task(store, "t1")
    t.chmod(0o644)
    try:
        svc = _service(store, fx.good)
        svc.evaluate("cand-a")
    except (PermissionError, ValueError):
        return  # refusing a readable store is the safe answer
    assert _mode(store) & 0o077 == 0, "store left group/world accessible"
    for p in store.rglob("*"):
        assert _mode(p) & 0o077 == 0, f"{p.name} left group/world readable"


def test_world_readable_task_file_in_private_dir_is_refused_or_tightened(fx, store):
    t = _write_task(store, "t1")
    t.chmod(0o644)
    try:
        _service(store, fx.good).evaluate("cand-a")
    except (PermissionError, ValueError):
        return
    assert _mode(t) & 0o077 == 0


def test_store_inside_the_repository_is_refused(fx):
    """The agent reads its own checkout, so a store there is not hidden from it."""
    inside = REPO_ROOT / f".frontier_exam_{uuid.uuid4().hex[:8]}"
    inside.mkdir(mode=0o700)
    try:
        _write_task(inside, "t1")
        with pytest.raises((PermissionError, ValueError)):
            _service(inside, fx.good).evaluate("cand-a")
    finally:
        for p in inside.rglob("*"):
            p.unlink()
        inside.rmdir()


def test_missing_store_is_an_error_not_an_empty_pass(fx, tmp_path):
    with pytest.raises((FileNotFoundError, ValueError, PermissionError)):
        _service(tmp_path / "does_not_exist", fx.good).evaluate("cand-a")


def test_tasks_are_never_copied_into_the_working_tree_or_home(fx, store):
    """Running must not spill task files anywhere an agent can read (cwd, repo, home)."""
    _write_task(store, C_ID)
    _service(store, fx.good).evaluate("cand-a")
    for sub in ("tanishi", "tests", "build"):
        for p in (REPO_ROOT / sub).rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts and p.name != "test_frontier.py":
                _assert_clean(p.read_text(errors="ignore"), C_ID, C_PROMPT)
    home = Path(os.environ["HOME"])
    for p in home.rglob("*"):
        if p.is_file() and not p.name.startswith("core_state.db"):
            _assert_clean(p.read_text(errors="ignore"), *ALL_CANARIES)


# ---------------------------------------------------------------- property: the boundary holds for any task text


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    prompts=st.lists(st.text(alphabet=st.characters(min_codepoint=33, max_codepoint=0x24F,
                                                     blacklist_categories=("Cs", "Cc")), min_size=8, max_size=40),
                     min_size=1, max_size=3),
    ids=st.lists(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=6, max_size=12),
                 min_size=3, max_size=3, unique=True),
)
def test_property_boundary_holds_for_arbitrary_tasks(fx, prompts, ids):
    with tempfile.TemporaryDirectory() as d:
        store = _private_store(Path(d))
        written = []
        for prompt, tid in zip(prompts, ids):
            full_id = f"id-{tid}"
            _write_task(store, full_id, prompt=f"PROMPT-{prompt}")
            written.append((full_id, f"PROMPT-{prompt}"))
        svc = _service(store, fx.wrong)
        first = svc.evaluate("cand-a")
        second = svc.evaluate("cand-a")
        assert len(first) == len(written)
        assert all(set(e) == KEYS for e in first)
        assert all(0.0 <= e["score"] <= 1.0 and e["cost"] >= 0 for e in first)
        hashes = [e["task_id_hash"] for e in first]
        assert len(set(hashes)) == len(hashes)                       # injective on task ids
        assert sorted(hashes) == sorted(e["task_id_hash"] for e in second)  # deterministic
        blob = json.dumps(first, ensure_ascii=False)
        for full_id, prompt in written:
            assert full_id not in blob
            assert prompt not in blob
        _assert_clean(blob, C_ANSWER, C_SOURCE)
