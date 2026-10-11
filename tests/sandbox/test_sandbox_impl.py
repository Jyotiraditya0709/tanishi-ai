"""SB1 repair round: the implementer's own tests (not the exam, not the red-team proofs).

They pin the human decisions of the repair card: code a model wrote runs only where other processes are hidden
(fail closed on macOS without Docker), no unix socket with a network grant, no database or guarded name inside a read
path, a clean worktree copy, a rollback a candidate cannot block, and process and disk limits with a clear reason.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import uuid
from pathlib import Path

import pytest

from tanishi.sandbox import (
    Sandbox,
    SandboxUnavailable,
    backends,
    checked_read_path,
    run_python,
)
from tanishi.sandbox import _launch as launch

DARWIN = sys.platform == "darwin"
DOCKER = backends.docker_available()


def _run(sb: Sandbox, code: str, **kw):
    return sb.run([sys.executable, "-I", "-c", textwrap.dedent(code)], **kw)


def _sb(**kw) -> Sandbox:
    kw.setdefault("prefer_docker", False)
    return Sandbox(**kw)


def _token() -> str:
    return "SB1-IMPL-" + uuid.uuid4().hex


# --- 1/2 · who wrote the code decides the backend ---------------------------------------------------------------------


@pytest.mark.skipif(not DARWIN, reason="the sandbox-exec refusal is macOS only")
def test_untrusted_code_is_refused_on_macos_without_docker(monkeypatch):
    monkeypatch.setattr(backends, "docker_available", lambda: False)
    with pytest.raises(RuntimeError, match="Docker"):
        Sandbox(trusted=False)
    with pytest.raises(RuntimeError, match="Docker"):
        Sandbox(trusted=False, prefer_docker=False)
    assert Sandbox(trusted=True, prefer_docker=False).backend == "sandbox-exec"


@pytest.mark.skipif(not DARWIN, reason="macOS backend choice")
def test_untrusted_code_never_gets_sandbox_exec_even_with_prefer_docker_false():
    if not DOCKER:
        pytest.skip("needs Docker")
    assert Sandbox(trusted=False, prefer_docker=False).backend == "docker"


def test_trusted_must_be_a_bool_or_none():
    with pytest.raises(TypeError):
        Sandbox(trusted="no")


def test_run_python_tests_fails_closed_without_an_isolating_backend(monkeypatch):
    from tanishi.arena.practice import _common

    def nothing(*a, **k):
        raise RuntimeError("no sandbox")

    monkeypatch.setattr(backends, "pick_backend", nothing)
    with pytest.raises(RuntimeError):
        _common.run_python_tests("def f(x):\n    return x\n", "assert f(1) == 1")


def test_untrusted_candidate_cannot_find_another_processes_environment():
    """The candidate path of red-team break 2: with trusted=False the run is in a container (macOS) or under
    Landlock (Linux), so another process's environment is out of reach however it is asked for."""
    try:
        sb = _sb(trusted=False, wall_seconds=60, cpu_seconds=60)
    except RuntimeError:
        pytest.skip("no backend here can isolate processes (fail closed, see the test above)")
    token = _token()
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True,
                              env={"PATH": os.environ.get("PATH", ""), "SB1_IMPL_FAKE_KEY": token})
    try:
        with sb:
            assert sb.backend in ("docker", "landlock")
            # the token is sent reversed, so the candidate's own argv does not hold it
            res = _run(sb, f"""
                import ctypes, os, sys
                want, found = {token[::-1]!r}[::-1].encode(), False
                for pid in os.listdir("/proc") if os.path.isdir("/proc") else []:
                    for leaf in ("environ", "cmdline"):
                        try:
                            found = found or want in open(f"/proc/{{pid}}/{{leaf}}", "rb").read()
                        except OSError:
                            pass
                if sys.platform == "darwin":  # only reached if a macOS backend ever ran this
                    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
                    for pid in range(2, 100000):
                        mib = (ctypes.c_int * 3)(1, 49, pid)
                        size = ctypes.c_size_t(0)
                        if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) == 0 and size.value:
                            buf = ctypes.create_string_buffer(size.value)
                            if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) == 0:
                                found = found or want in buf.raw[: size.value]
                print("DONE", found)
                """)
        assert "DONE" in res.stdout, res
        assert "DONE False" in res.stdout
    finally:
        holder.kill()
        holder.wait()


# --- 3 · a network grant is IP only ------------------------------------------------------------------------------------


@pytest.fixture
def unix_server():
    """A listening unix socket outside scratch that answers with a token."""
    token = _token()
    d = Path(tempfile.mkdtemp(prefix="sb1impl-sock-"))
    path = d / "agent.sock"
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(4)
    srv.settimeout(0.2)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except OSError:
                continue
            c.sendall(token.encode())
            c.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield path, token
    stop.set()
    t.join(2)
    srv.close()
    path.unlink(missing_ok=True)
    d.rmdir()


_CONNECT_UNIX = """
import json, socket, sys
out = {}
for p in json.loads(sys.argv[1]):
    s = socket.socket(socket.AF_UNIX)
    try:
        s.settimeout(3)
        s.connect(p)
        out[p] = "GOT " + s.recv(200).decode(errors="replace")
    except OSError as e:
        out[p] = "ERR " + type(e).__name__
    finally:
        s.close()
print(json.dumps(out))
"""


@pytest.mark.parametrize("network", [True, False])
def test_docker_sock_and_ssh_agent_stay_unreachable(unix_server, network):
    """With or without a network grant, no unix socket outside scratch can be reached: the Docker daemon (root on the
    host), the user's ssh-agent, or a stand-in agent socket that is known to be listening."""
    planted, token = unix_server
    probe = socket.socket(socket.AF_UNIX)
    probe.connect(str(planted))  # the control: the planted agent answers outside the sandbox
    assert probe.recv(200).decode() == token
    probe.close()
    targets = [str(planted), "/var/run/docker.sock", str(Path.home() / ".docker" / "run" / "docker.sock")]
    if os.environ.get("SSH_AUTH_SOCK"):
        targets.append(os.environ["SSH_AUTH_SOCK"])
    with _sb(network=network) as sb:
        res = sb.run([sys.executable, "-I", "-c", _CONNECT_UNIX, json.dumps(targets)])
    got = json.loads(res.stdout)
    assert set(got) == set(targets), res
    reached = {p: v for p, v in got.items() if v.startswith("GOT")}
    assert not reached, f"unix sockets reachable from the sandbox (network={network}): {sorted(reached)}"


@pytest.mark.skipif(not DARWIN, reason="checks the macOS profile text")
def test_network_grant_in_the_profile_is_ip_only():
    profile = backends.darwin_profile("/tmp/s", [], True, ("/tmp/a", "/tmp/b"))
    assert "network*" not in profile
    assert "(allow network-outbound (remote ip))" in profile
    assert "unix-socket" not in profile


def test_ip_network_still_works_with_the_grant():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        with _sb(network=True) as sb:
            if sb.backend == "docker":
                pytest.skip("host loopback is not the container's")
            res = _run(sb, f"""
                import socket
                s = socket.create_connection(("127.0.0.1", {port}), timeout=3)
                print("CONNECTED")
                """)
        assert "CONNECTED" in res.stdout, res
    finally:
        srv.close()


# --- 4 · read paths ---------------------------------------------------------------------------------------------------


def test_database_and_guarded_names_inside_a_read_path_are_refused_in_any_case(tmp_path):
    root = tmp_path / "pkgroot"
    token = _token()
    files = ["data/db.sqlite", "NOTES.DB", "deep/er/x.Sqlite3", "cache.db-wal.sqlite-wal", "sub/Vault/e.yaml",
             "sub/.Env", "TRACES/t.jsonl", "store.db/inner.txt"]
    for rel in files:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(token)
    (root / "deep" / "er" / "ok.py").write_text("ok")
    with _sb(read_paths=[root]) as sb:
        res = _run(sb, f"""
            import json, os
            out = {{}}
            for rel in {[*files, "deep/er/ok.py"]!r}:
                try:
                    out[rel] = open(os.path.join({str(root)!r}, rel)).read()
                except OSError:
                    out[rel] = None
            open(os.path.join(os.environ["HOME"], "own.db"), "w").write("mine")  # its own scratch: allowed
            out["own"] = open(os.path.join(os.environ["HOME"], "own.db")).read()
            print(json.dumps(out))
            """)
    got = json.loads(res.stdout)
    assert got["deep/er/ok.py"] == "ok" and got["own"] == "mine"
    leaked = [rel for rel in files if got[rel] is not None]
    assert not leaked, f"readable inside a read path: {leaked}"


@pytest.mark.parametrize("name", ["core_state.db", "x.SQLITE", "Vault", ".ENV.local", "Traces"])
def test_a_read_path_named_like_a_database_or_guarded_place_is_refused(tmp_path, name):
    p = tmp_path / name
    p.mkdir()
    with pytest.raises(ValueError):
        checked_read_path(p)


def test_the_folder_holding_a_read_path_is_listable_but_its_files_are_not_readable(tmp_path):
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("VALUE = 7\n")
    (root / "secret.txt").write_text(_token())
    with _sb(read_paths=[root / "pkg"]) as sb:
        res = _run(sb, f"""
            import json, os, sys
            sys.path.insert(0, {str(root)!r})
            import pkg
            out = {{"value": pkg.VALUE, "names": sorted(os.listdir({str(root)!r}))}}
            try:
                out["secret"] = open({str(root / 'secret.txt')!r}).read()
            except OSError:
                out["secret"] = None
            print(json.dumps(out))
            """)
    got = json.loads(res.stdout)
    assert got["value"] == 7
    assert "pkg" in got["names"]
    assert got["secret"] is None


def test_ar1_never_grants_the_repo_root_or_the_cwd():
    import tanishi
    from tanishi.arena import runner

    repo = os.path.dirname(os.path.dirname(os.path.realpath(tanishi.__file__)))
    paths = runner._import_paths()
    assert repo not in paths and os.path.realpath(os.getcwd()) not in paths
    assert os.path.join(repo, "tanishi") in paths


def test_landlock_rules_carve_out_refused_names_at_any_depth(tmp_path):
    root = tmp_path / "r"
    for rel in ["a/b/x.db", "a/ok.py", "a/b/fine.txt", "c/d.py", "Vault/v.yaml"]:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
    rules = {p: kind for p, kind in backends.landlock_rules(str(tmp_path / "s"), [str(root)], [str(tmp_path)])}
    assert rules[str(root)] == "list" and rules[str(root / "a")] == "list" and rules[str(root / "a" / "b")] == "list"
    assert rules[str(root / "a" / "ok.py")] == "ro" and rules[str(root / "a" / "b" / "fine.txt")] == "ro"
    assert rules[str(root / "c")] == "ro"
    assert rules[str(tmp_path)] == "list"
    assert str(root / "a" / "b" / "x.db") not in rules and str(root / "Vault") not in rules


# --- 5/8 · the worktree copy ------------------------------------------------------------------------------------------


def test_worktree_copy_leaves_out_git_databases_env_and_guarded_names_in_any_case(tmp_path):
    wt = tmp_path / "wt"
    kept = ["src/app.py", "README.md", "docs/environment.md", "sub/envy.txt"]
    dropped = [".git/HEAD", "data/db.sqlite", "x.SQLITE3", "NOTES.DB", ".envrc", ".ENV", "sub/.Env.local",
               "Builder_Vault/t.py", "VAULT/s.txt", "sub/Traces/t.jsonl", ".SSH/id", "cache/a.sqlite-wal"]
    for rel in kept + dropped:
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / rel).write_text("x")
    with _sb(worktree=wt) as sb:
        copied = {str(p.relative_to(sb.workdir)) for p in sb.workdir.rglob("*") if p.is_file()}
    assert set(kept) <= copied
    assert not copied & set(dropped), sorted(copied & set(dropped))


# --- 6 · rollback ------------------------------------------------------------------------------------------------------


@pytest.mark.skipif(not hasattr(os, "chflags"), reason="BSD file flags")
def test_rollback_clears_flags_and_permissions_the_candidate_set():
    with _sb() as sb:
        res = _run(sb, """
            import os, stat
            home = os.environ["HOME"]
            os.makedirs(os.path.join(home, "locked", "inner"))
            open(os.path.join(home, "locked", "inner", "f"), "w").write("x")
            os.chflags(os.path.join(home, "locked", "inner", "f"), stat.UF_IMMUTABLE)
            open(os.path.join(home, "log"), "w").write("x")
            os.chflags(os.path.join(home, "log"), stat.UF_APPEND)
            os.chflags(os.path.join(home, "locked", "inner"), stat.UF_IMMUTABLE)
            os.chmod(os.path.join(home, "locked"), 0)
            print("LOCKED")
            """)
        assert "LOCKED" in res.stdout, res
        sb.rollback()
        assert sorted(p.name for p in sb.home.iterdir()) == [".tanishi", "tmp"]
        scratch = sb.scratch
    assert not scratch.exists()


def test_rollback_and_close_work_after_a_chmod_000_scratch_tree():
    sb = _sb()
    sb.open()
    scratch = sb.scratch
    try:
        _run(sb, """
            import os
            os.makedirs("a/b")
            open("a/b/f", "w").write("x")
            os.chmod("a/b", 0)
            os.chmod("a", 0)
            """)
        sb.rollback()
        assert not (sb.workdir / "a").exists()
    finally:
        sb.close()
    assert not scratch.exists()


# --- 7 · process and disk limits ---------------------------------------------------------------------------------------


def test_a_fork_past_max_processes_fails():
    """The kernel's cap is per user, so it is set to the user's count at start plus max_processes: approximate when
    other processes of the user come and go meanwhile, but a flood stops near the cap."""
    with _sb(max_processes=8, wall_seconds=20) as sb:
        res = _run(sb, """
            import os, time
            kids = 0
            for _ in range(100):
                try:
                    pid = os.fork()
                except OSError:
                    break
                if pid == 0:
                    time.sleep(3)
                    os._exit(0)
                kids += 1
            print("KIDS", kids)
            """)
    assert "KIDS" in res.stdout, res
    assert int(res.stdout.split("KIDS")[1].split()[0]) < 50


def test_the_watchdog_stops_a_run_with_too_many_processes(monkeypatch):
    """When the fork cap cannot hold (root, or the user's other processes exited), the watchdog stops the run."""
    from tanishi.sandbox import core

    monkeypatch.setattr(core, "_process_cap", lambda n: None)
    with _sb(max_processes=4, wall_seconds=20) as sb:
        if sb.backend == "docker":
            pytest.skip("Docker counts processes itself")
        res = _run(sb, """
            import os, time
            for _ in range(10):
                if os.fork() == 0:
                    time.sleep(10)
                    os._exit(0)
            time.sleep(10)
            """)
    assert res.limit == "processes", res


def test_a_file_larger_than_max_disk_cannot_be_written():
    with _sb(max_disk_mb=4) as sb:
        res = _run(sb, """
            import os
            try:
                with open(os.path.join(os.environ["HOME"], "big"), "wb") as f:
                    f.write(b"\\0" * (8 << 20))
                print("WROTE")
            except OSError as e:
                print("STOPPED", e.errno)
            """)
    assert "STOPPED" in res.stdout, res


def test_many_small_files_past_max_disk_stop_the_run():
    with _sb(max_disk_mb=4, wall_seconds=30) as sb:
        res = _run(sb, """
            import os, time
            for i in range(12):
                with open(os.path.join(os.environ["HOME"], f"f{i}"), "wb") as f:
                    f.write(os.urandom(1 << 20))
            time.sleep(20)
            """)
    assert res.limit == "disk", res
    assert res.duration_s < 15


def test_limits_must_be_positive():
    with pytest.raises(ValueError):
        Sandbox(prefer_docker=False, max_processes=0)
    with pytest.raises(ValueError):
        Sandbox(prefer_docker=False, max_disk_mb=float("inf"))


def test_docker_gets_the_pids_limit_a_sized_tmpfs_and_a_file_size_cap():
    cmd = backends.docker_argv(name="n", image="img", scratch="/s", cwd="/s/w", env_names=["HOME"], read_paths=[],
                               network=False, cpu_seconds=5, memory_mb=64, argv=["python3", "x.py"],
                               max_processes=9, max_disk_mb=7)
    assert cmd[cmd.index("--pids-limit") + 1] == "9"
    assert "/tmp:rw,size=7m" in cmd
    assert f"fsize={7 * 1024 * 1024}" in cmd


# --- 9 · run_python, L1's way in ---------------------------------------------------------------------------------------

isolating = pytest.mark.skipif(DARWIN and not DOCKER, reason="untrusted code needs Docker on macOS")


@isolating
def test_run_python_passes_stdin_and_returns_what_the_script_did():
    res = run_python("import sys\ndata = sys.stdin.read()\nprint(data.upper())\nprint('err', file=sys.stderr)\n"
                     "sys.exit(3)\n", stdin="skill input", timeout_s=60)
    assert (res.returncode, res.stdout.strip(), res.stderr.strip(), res.timed_out) == (3, "SKILL INPUT", "err", False)


@isolating
def test_run_python_kills_the_script_after_timeout_s():
    start = time.monotonic()
    res = run_python("import time\nprint('started', flush=True)\ntime.sleep(120)\nprint('finished')\n",
                     stdin=None, timeout_s=8)
    took = time.monotonic() - start
    assert res.timed_out and res.returncode != 0
    assert "finished" not in res.stdout
    assert took < 8 + 30, took  # the kill, plus container start and removal


@isolating
def test_run_python_clips_each_stream_to_64_kb():
    res = run_python("import sys\nsys.stdout.write('o' * 200_000)\nsys.stderr.write('é' * 100_000)\n", timeout_s=60)
    assert res.returncode == 0
    assert res.stdout == "o" * 64 * 1024
    assert len(res.stderr.encode()) <= 64 * 1024 and set(res.stderr) == {"é"}


@isolating
def test_run_python_cannot_read_another_processes_environment():
    token = _token()
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True,
                              env={"PATH": os.environ.get("PATH", ""), "SB1_IMPL_FAKE_KEY": token})
    try:
        res = run_python("import glob, sys\nwant = sys.stdin.read().encode()\nfound = False\n"
                         "for p in glob.glob('/proc/[0-9]*/environ'):\n"
                         "    try:\n        found = found or want in open(p, 'rb').read()\n"
                         "    except OSError:\n        pass\nprint('FOUND', found)\n", stdin=token, timeout_s=60)
    finally:
        holder.kill()
        holder.wait()
    assert "FOUND False" in res.stdout, res


@pytest.mark.skipif(not DARWIN, reason="the sandbox-exec refusal is macOS only")
def test_run_python_fails_closed_on_macos_without_docker(monkeypatch):
    monkeypatch.setattr(backends, "docker_available", lambda: False)
    with pytest.raises(SandboxUnavailable, match="Docker"):
        run_python("print('ran')\n", stdin="", timeout_s=5)


def test_run_python_never_asks_for_a_trusted_sandbox(monkeypatch):
    asked = []

    def record(prefer_docker, network, trusted=None):
        asked.append(trusted)
        raise SandboxUnavailable("recorded")

    monkeypatch.setattr(backends, "pick_backend", record)
    with pytest.raises(SandboxUnavailable):
        run_python("print(1)\n", timeout_s=5)
    assert asked == [False]


def test_run_python_needs_source_text_and_a_finite_timeout():
    with pytest.raises(TypeError):
        run_python(b"print(1)", timeout_s=5)
    with pytest.raises(ValueError):
        run_python("print(1)", timeout_s=float("inf"))


# --- Docker plumbing --------------------------------------------------------------------------------------------------


@pytest.mark.skipif(not DOCKER, reason="needs Docker")
def test_docker_environment_is_only_what_the_sandbox_built():
    with Sandbox() as sb:
        assert sb.backend == "docker"
        res = _run(sb, "import json, os; print(json.dumps(sorted(os.environ)))")
    assert set(json.loads(res.stdout)) <= {"PATH", "LANG", "HOME", "TANISHI_HOME", "TANISHI_CORE_STATE_DB", "TMPDIR"}


@pytest.mark.skipif(not DOCKER, reason="needs Docker")
def test_docker_carries_one_passed_descriptor_on_the_container_stdout():
    r, w = os.pipe()
    with Sandbox() as sb:
        proc = sb.popen([sys.executable, "-I", "-c", f"import os; print('noise'); os.write({w}, b'MSG')"],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, pass_fds=(w,))
        os.close(w)
        proc.wait(60)
        data = os.read(r, 100)
    os.close(r)
    assert data == b"MSG"


# --- the Linux seccomp filter, checked by running it here --------------------------------------------------------------


def _bpf(prog, nr: int, arch: int, arg0: int = 0) -> int:
    data = {0: nr, 4: arch, 16: arg0}
    acc, pc = 0, 0
    while True:
        code, jt, jf, k = prog[pc]
        if code == launch._BPF_LD:
            acc = data[k]
            pc += 1
        elif code == launch._BPF_RET:
            return k
        else:
            hit = acc == k if code == launch._BPF_JEQ else acc >= k
            pc += 1 + (jt if hit else jf)


@pytest.mark.parametrize("machine", sorted(launch._SECCOMP))
def test_seccomp_filter_refuses_unix_sockets_and_chmod_and_allows_the_rest(machine):
    prog = launch._seccomp_program(machine)
    arch, refused, x32, sys_socket = launch._SECCOMP[machine]
    allow, eperm = launch._SECCOMP_ALLOW, launch._SECCOMP_EPERM
    assert _bpf(prog, sys_socket, arch, arg0=1) == eperm  # socket(AF_UNIX, ...)
    assert _bpf(prog, sys_socket, arch, arg0=2) == allow  # socket(AF_INET, ...)
    assert _bpf(prog, sys_socket, arch, arg0=10) == allow  # AF_INET6
    for nr in refused:
        assert _bpf(prog, nr, arch) == eperm
    assert _bpf(prog, 0, arch) == allow and _bpf(prog, 1, arch) == allow
    assert _bpf(prog, 0, 0x12345678) == eperm  # a foreign ABI
    if x32:
        assert _bpf(prog, x32 + 1, arch) == eperm
