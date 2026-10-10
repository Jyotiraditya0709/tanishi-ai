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


def test_malformed_reply_denies(monkeypatch):
    """A reply that is valid JSON but the wrong shape must deny, never raise."""
    import importlib
    import socket
    import tempfile
    import threading
    from pathlib import Path

    for bad in (b"[1, 2]\n", b'{"decision": "allow", "tier": "high"}\n', b'"allow"\n'):
        sock_path = Path(tempfile.mkdtemp(dir="/tmp")) / "w.sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        srv.listen(1)

        def serve(server=srv, payload=bad):
            conn, _ = server.accept()
            conn.recv(4096)
            conn.sendall(payload)
            conn.close()

        threading.Thread(target=serve, daemon=True).start()
        monkeypatch.setenv("TANISHI_WARDEN_SOCK", str(sock_path))
        import tanishi.guard.client as client
        importlib.reload(client)
        d = client.check("read_file", {"path": "/etc/hosts"})
        assert d.decision == "deny", bad
        assert "failing closed" in d.reason
        srv.close()
