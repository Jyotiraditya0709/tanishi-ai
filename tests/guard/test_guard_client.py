"""The guard client must fail closed: with no Warden listening, every check denies.

Put this file at tests/guard/test_guard_client.py in the main repo (a tester owns the real
exam; this is the minimal fail-closed proof to keep with the guard).
"""

from __future__ import annotations

from tanishi.guard import check


def test_no_warden_denies(monkeypatch, tmp_path):
    # Point the client at a socket that does not exist.
    monkeypatch.setenv("TANISHI_WARDEN_SOCK", str(tmp_path / "absent.sock"))
    # Re-import so the module picks up the env (or set the attr directly if already imported).
    import importlib

    import tanishi.guard.client as client
    importlib.reload(client)

    d = client.check("read_file", {"path": "/etc/hosts"}, actor="tanishi")
    assert d.decision == "deny"
    assert not d.allowed
    assert "failing closed" in d.reason
