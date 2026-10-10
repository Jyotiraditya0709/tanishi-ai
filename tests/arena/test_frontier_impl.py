"""Implementer's tests for AR3 choices the exam leaves open (salted hash, symlinks, duplicate ids, env, CLI)."""
import hashlib
import json
import os
import stat
import sys
import textwrap

import pytest
import yaml

from tanishi.arena.frontier_service import FrontierService, main

_SRC = textwrap.dedent(
    """
    from tanishi.arena.runner import Attempt

    def good(brief, candidate, seed):
        return Attempt("42", cost=0.1)

    def exact(task, output):
        return (1.0, "ok") if output.strip() == "42" else (0.0, "does not match")
    """
)


@pytest.fixture
def fx(tmp_path, monkeypatch):
    d = tmp_path / "mod"
    d.mkdir()
    (d / "frontier_impl_fx.py").write_text(_SRC)
    monkeypatch.syspath_prepend(str(d))
    sys.modules.pop("frontier_impl_fx", None)
    import frontier_impl_fx

    return frontier_impl_fx


@pytest.fixture
def store(tmp_path):
    s = tmp_path / "store"
    s.mkdir(mode=0o700)
    s.chmod(0o700)
    return s


def _task(store, tid, name=None):
    p = store / f"{name or tid}.yaml"
    p.write_text(yaml.safe_dump({"id": tid, "family": "f", "tier": "frontier", "prompt": "p",
                                 "verifier": "frontier_impl_fx.exact", "timeout_s": 20}))
    p.chmod(0o600)
    return p


def test_hash_is_salted_not_a_bare_digest_of_the_id(fx, store):
    _task(store, "t1")
    (e,) = FrontierService(store, executor=fx.good).evaluate("c")
    for algo in (hashlib.sha256, hashlib.sha1, hashlib.md5):
        assert not algo(b"t1").hexdigest().startswith(e["task_id_hash"])
    salt = store / ".salt"
    assert stat.S_IMODE(salt.stat().st_mode) == 0o600


def test_hash_differs_between_stores(fx, tmp_path):
    hashes = []
    for name in ("a", "b"):
        s = tmp_path / name
        s.mkdir(mode=0o700)
        _task(s, "t1")
        hashes.append(FrontierService(s, executor=fx.good).evaluate("c")[0]["task_id_hash"])
    assert hashes[0] != hashes[1]


def test_symlinked_task_file_is_refused(fx, store, tmp_path):
    outside = _task(tmp_path, "t1")
    (store / "t1.yaml").symlink_to(outside)
    with pytest.raises(PermissionError):
        FrontierService(store, executor=fx.good).evaluate("c")


def test_open_salt_is_refused(fx, store):
    _task(store, "t1")
    FrontierService(store, executor=fx.good).evaluate("c")
    (store / ".salt").chmod(0o644)
    with pytest.raises(PermissionError):
        FrontierService(store, executor=fx.good).evaluate("c")


def test_duplicate_ids_are_refused_without_naming_them(fx, store):
    _task(store, "dup-id-xyz", name="a")
    _task(store, "dup-id-xyz", name="b")
    with pytest.raises(ValueError) as exc:
        FrontierService(store, executor=fx.good).evaluate("c")
    assert "dup-id-xyz" not in str(exc.value)


def test_bad_file_error_has_no_chained_cause(fx, store):
    _task(store, "t1")
    (store / "bad.yaml").write_text("id: [x\n")
    (store / "bad.yaml").chmod(0o600)
    with pytest.raises(ValueError) as exc:
        FrontierService(store, executor=fx.good).evaluate("c")
    assert exc.value.__cause__ is None and exc.value.__context__ is None


def test_seeds_average_score_and_sum_cost(fx, store):
    _task(store, "t1")
    (e,) = FrontierService(store, executor=fx.good).evaluate("c", seeds=3)
    assert e["score"] == 1.0
    assert e["cost"] == pytest.approx(0.3)


def test_callers_core_state_env_is_restored(fx, store):
    before = os.environ["TANISHI_CORE_STATE_DB"]
    _task(store, "t1")
    FrontierService(store, executor=fx.good).evaluate("c")
    assert os.environ["TANISHI_CORE_STATE_DB"] == before


def test_cli_prints_only_the_scores(fx, store, capsys):
    _task(store, "t1")
    assert main([str(store), "c", "--executor", "frontier_impl_fx.good"]) == 0
    out = capsys.readouterr()
    (e,) = json.loads(out.out)
    assert set(e) == {"task_id_hash", "score", "cost"} and e["score"] == 1.0
    assert out.err == ""


def test_cli_reports_a_refused_store(fx, tmp_path, capsys):
    assert main([str(tmp_path / "missing"), "c", "--executor", "frontier_impl_fx.good"]) == 2
    assert "FileNotFoundError" in capsys.readouterr().err
