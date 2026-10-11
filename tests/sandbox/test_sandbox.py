"""SB1 exam, written from the spec only (build/graph.yaml node SB1), apart from the implementation.

Acceptance lines under test:
  1. A candidate runs with its own temporary HOME, a scratch working folder, no network unless the task grants it,
     and CPU, memory and wall-time limits.
  2. Reading the Sealed Vault, the builder vault, the Frontier store or its salt, the traces folder, ~/.ssh or any
     .env fails inside the sandbox; canary files are planted to prove it.
  3. Writing outside the scratch folder fails (and AR3 RT-2 / RT-3 pass: see tests/arena/test_redteam_ar3.py).
  4. Every run can be rolled back: nothing outside scratch has changed (file hash of the guarded folders).
  5. Works on macOS without Docker; Docker is used when present.

The card names a directory but no function names. The names used here are assumptions A1-A8 in
build/memory/open-problems/SB1-exam-assumptions.md. If the implementer disagrees with one, say so in
build/memory/open-problems/SB1-exam-conflicts.md; do not edit the tests.

Every confinement test also asserts that the candidate ran and could use its own scratch, so a sandbox that simply
refuses to start cannot pass by "everything failed".
"""
import hashlib
import json
import math
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tanishi.sandbox import Sandbox

REPO_ROOT = Path(__file__).resolve().parents[2]


# --- helpers ---------------------------------------------------------------------------------------------------------


def _docker_works() -> bool:
    exe = shutil.which("docker")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "info"], capture_output=True, timeout=15, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


DOCKER = _docker_works()


def tree_hash(*roots: Path) -> str:
    """Hash names, modes, link targets and bytes of everything under the roots (missing roots hash as MISSING)."""
    h = hashlib.sha256()
    for root in roots:
        h.update(b"ROOT:" + str(root).encode() + b"\n")
        if not (root.exists() or root.is_symlink()):
            h.update(b"MISSING\n")
            continue
        for p in sorted([root, *root.rglob("*")], key=str):
            s = p.lstat()
            h.update(f"{p.relative_to(root)}|{stat.S_IMODE(s.st_mode):o}|{stat.S_IFMT(s.st_mode):o}|".encode())
            if p.is_symlink():
                h.update(os.readlink(p).encode())
            elif p.is_file():
                h.update(p.read_bytes())
            h.update(b"\n")
    return h.hexdigest()


def _put(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text


def make_world(root: Path, home: Path) -> SimpleNamespace:
    """Plant canaries where a candidate must not reach: names look like the real guarded places, none is the real one."""
    token = uuid.uuid4().hex
    spots = {
        "ssh_key": home / ".ssh" / "id_ed25519",
        "home_env": home / ".env",
        "traces": home / ".tanishi" / "traces" / "2026-10-11.jsonl",
        "core_db": home / ".tanishi" / "core_state.db",
        "vault": root / "vault" / "sealed" / "exam-1.yaml",
        "builder_vault": root / "builder_vault" / "hidden" / "test_ba01.py",
        "frontier_task": root / "frontier_store" / "t1.yaml",
        "frontier_salt": root / "frontier_store" / ".salt",
        "frontier_queries": root / "frontier_store" / ".queries",
        "project_env": root / "project" / ".env",
        "project_env_nested": root / "project" / "sub" / ".env.local",
        "plain_outside": root / "outside" / "keep.txt",
    }
    tokens = {}
    for name, path in spots.items():
        tokens[name] = f"SB1-CANARY-{name}-{token}"
        _put(path, tokens[name] + "\n")
    dirs = {
        "ssh_dir": home / ".ssh",
        "traces_dir": home / ".tanishi" / "traces",
        "vault_dir": root / "vault" / "sealed",
        "builder_dir": root / "builder_vault" / "hidden",
        "frontier_dir": root / "frontier_store",
        "outside_dir": root / "outside",
    }
    return SimpleNamespace(files=spots, tokens=tokens, dirs=dirs, root=root, home=home, hash_roots=(root, home))


@pytest.fixture
def world(tmp_path):
    home = Path(os.environ["HOME"])
    return make_world(tmp_path / "world", home)


@pytest.fixture
def worktree(tmp_path):
    """A small git checkout with committed code, and ignored secrets that are not in git."""
    wt = tmp_path / "wt"
    _put(wt / "src" / "app.py", "def answer():\n    return 41\n")
    _put(wt / "README.md", "readme\n")
    _put(wt / ".gitignore", ".env\n.env.*\nvault/\nbuilder_vault/\ntraces/\n.ssh/\n")
    git = ["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True)
    secrets = {
        ".env": "WT-SECRET-env",
        "sub/.env.local": "WT-SECRET-env-local",
        "vault/sealed.txt": "WT-SECRET-vault",
        "builder_vault/t.py": "WT-SECRET-builder",
        "traces/t.jsonl": "WT-SECRET-trace",
        ".ssh/id_rsa": "WT-SECRET-ssh",
    }
    for rel, text in secrets.items():
        _put(wt / rel, text)
    head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    return SimpleNamespace(path=wt, secrets=secrets, head=head, git=git)


def make(**kw) -> Sandbox:
    """Deterministic fast path for most tests: no Docker. The Docker default has its own tests (A7)."""
    kw.setdefault("prefer_docker", False)
    return Sandbox(**kw)


_PRELUDE = """\
import json, os, sys, socket, subprocess, shutil, time, pathlib
DATA = json.loads(__DATA__)
OUT = {}
def attempt(key, fn):
    try:
        OUT[key] = ["ok", fn()]
    except BaseException as e:
        OUT[key] = ["err", type(e).__name__]
"""
_EPILOG = "\nOUT['ran'] = True\nprint(json.dumps(OUT), flush=True)\n"


def source(body: str, data=None) -> str:
    return _PRELUDE.replace("__DATA__", repr(json.dumps(data or {}))) + textwrap.dedent(body) + _EPILOG


def probe(sb, body: str, data=None, **kw):
    return sb.run([sys.executable, "-I", "-c", source(body, data)], **kw)


def out_of(result) -> dict:
    assert result.returncode == 0, f"candidate did not run cleanly: rc={result.returncode} stderr={result.stderr[-400:]}"
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert lines, "no output from candidate"
    got = json.loads(lines[-1])
    assert got.get("ran") is True
    return got


def failed(got: dict, key: str) -> bool:
    return got[key][0] == "err"


@pytest.fixture
def cleanup():
    paths: list[Path] = []
    yield paths
    for p in paths:
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()
        except OSError:
            pass


# --- 0. the contract the rest relies on -------------------------------------------------------------------------------


def test_run_returns_output_exit_code_and_timing():
    with make() as sb:
        assert isinstance(sb.backend, str) and sb.backend
        ok = sb.run([sys.executable, "-I", "-c", "print('hello sandbox')"])
        assert ok.returncode == 0
        assert ok.stdout.strip() == "hello sandbox"
        assert ok.timed_out is False
        assert isinstance(ok.duration_s, float) and ok.duration_s >= 0
        bad = sb.run([sys.executable, "-I", "-c", "import sys; sys.stderr.write('oops'); sys.exit(3)"])
        assert bad.returncode == 3
        assert "oops" in bad.stderr
        assert bad.timed_out is False


def test_stdin_is_passed_through():
    with make() as sb:
        r = sb.run([sys.executable, "-I", "-c", "import sys; print(sys.stdin.read().upper())"], input="abc")
        assert r.returncode == 0 and r.stdout.strip() == "ABC"


def test_empty_argv_is_refused():
    with make() as sb, pytest.raises(ValueError):
        sb.run([])


def test_the_interpreter_and_stdlib_work_inside():
    """Default-deny must not be 'deny everything': json, sqlite3, socket, subprocess and a child process all work."""
    with make() as sb:
        got = out_of(probe(sb, """
            import sqlite3, hashlib, tempfile
            c = sqlite3.connect(":memory:"); c.execute("create table t(x)"); c.execute("insert into t values (1)")
            OUT["sqlite"] = c.execute("select x from t").fetchall()
            OUT["sha"] = hashlib.sha256(b"a").hexdigest()[:8]
            OUT["child"] = subprocess.run([sys.executable, "-I", "-c", "print(7)"], capture_output=True, text=True).stdout.strip()
            with tempfile.NamedTemporaryFile() as f:
                OUT["tmp"] = os.path.dirname(f.name)
        """))
        assert got["sqlite"] == [[1]]
        assert got["sha"] == "ca978112"
        assert got["child"] == "7"
        assert Path(got["tmp"]).resolve().is_relative_to(sb.home.resolve()) or Path(got["tmp"]).resolve().is_relative_to(
            sb.scratch.resolve()
        )


@pytest.mark.parametrize("name", ["cpu_seconds", "memory_mb", "wall_seconds"])
@pytest.mark.parametrize("bad", [None, 0, -1, float("nan"), float("inf")])
def test_a_limit_cannot_be_turned_off(name, bad):
    with pytest.raises(ValueError):
        Sandbox(prefer_docker=False, **{name: bad})


@pytest.mark.parametrize("name", ["cpu_seconds", "memory_mb", "wall_seconds"])
def test_default_limits_are_finite_and_positive(name):
    v = getattr(Sandbox(prefer_docker=False), name)
    assert isinstance(v, (int, float)) and math.isfinite(v) and v > 0


# --- 1. own HOME, scratch folder, built environment ---------------------------------------------------------------


def test_candidate_has_its_own_home_and_scratch(world):
    real_home = Path(os.environ["HOME"]).resolve()
    with make() as sb:
        got = out_of(probe(sb, """
            import tempfile
            OUT["HOME"] = os.environ.get("HOME")
            OUT["TANISHI_HOME"] = os.environ.get("TANISHI_HOME")
            OUT["TMPDIR"] = os.environ.get("TMPDIR")
            OUT["cwd"] = os.getcwd()
            OUT["expand"] = os.path.expanduser("~")
            OUT["write_cwd"] = pathlib.Path("note.txt").write_text("a") and "ok"
            pathlib.Path(os.environ["HOME"], "h.txt").write_text("b")
            os.makedirs(os.environ["TANISHI_HOME"], exist_ok=True)
            pathlib.Path(os.environ["TANISHI_HOME"], "x").write_text("c")
            fd, tmp = tempfile.mkstemp(); os.close(fd); OUT["mkstemp"] = tmp
        """))
        scratch, home, workdir = sb.scratch.resolve(), sb.home.resolve(), sb.workdir.resolve()
        assert scratch.is_dir() and home.is_relative_to(scratch) and workdir.is_relative_to(scratch)
        assert Path(got["HOME"]).resolve() == home == Path(got["expand"]).resolve()
        assert home != real_home and not real_home.is_relative_to(home)
        assert Path(got["TANISHI_HOME"]).resolve() == home / ".tanishi"
        assert Path(got["cwd"]).resolve() == workdir
        assert Path(got["TMPDIR"]).resolve().is_relative_to(home)
        assert Path(got["mkstemp"]).resolve().is_relative_to(scratch)
        assert (workdir / "note.txt").read_text() == "a" and (home / "h.txt").read_text() == "b"
        assert stat.S_IMODE(scratch.stat().st_mode) & 0o077 == 0, "scratch must be private to the user"
    assert not scratch.exists(), "leaving the sandbox must remove the scratch folder"


def test_two_sandboxes_do_not_share_scratch():
    with make() as a, make() as b:
        assert a.scratch.resolve() != b.scratch.resolve()
        assert not a.scratch.resolve().is_relative_to(b.scratch.resolve())
        a.run([sys.executable, "-I", "-c", "open('mine.txt','w').write('a')"])
        got = out_of(probe(b, "OUT['seen'] = os.path.exists('mine.txt')"))
        assert got["seen"] is False


def test_environment_is_built_not_inherited(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "SB1-KEY-anthropic-" + uuid.uuid4().hex)
    monkeypatch.setenv("OPENAI_API_KEY", "SB1-KEY-openai-" + uuid.uuid4().hex)
    monkeypatch.setenv("SB1_PASS_ME", "passed-value")
    monkeypatch.setenv("SB1_NOT_PASSED", "not-passed-value")
    real_db = os.environ["TANISHI_CORE_STATE_DB"]
    with make(env_passthrough=("SB1_PASS_ME",)) as sb:
        r = probe(sb, "OUT['env'] = dict(os.environ)")
        got = out_of(r)
    blob = r.stdout + r.stderr
    assert "SB1-KEY-" not in blob
    assert got["env"].get("SB1_PASS_ME") == "passed-value"
    assert "SB1_NOT_PASSED" not in got["env"] and "ANTHROPIC_API_KEY" not in got["env"]
    assert got["env"].get("TANISHI_CORE_STATE_DB", "") != real_db
    assert "PATH" in got["env"]


# --- 2. reads of guarded things fail (canaries) ------------------------------------------------------------------------

_READ = """
for k, p in DATA["files"].items():
    def _r(p=p):
        with open(p) as f:
            return f.read()
    attempt("read:" + k, _r)
for k, p in DATA["dirs"].items():
    attempt("list:" + k, lambda p=p: sorted(os.listdir(p)))
    attempt("scan:" + k, lambda p=p: [e.name for e in os.scandir(p)])
"""


def _paths(world):
    return {
        "files": {k: str(v) for k, v in world.files.items()},
        "dirs": {k: str(v) for k, v in world.dirs.items()},
    }


def test_guarded_files_cannot_be_read_or_listed(world):
    before = tree_hash(*world.hash_roots)
    with make() as sb:
        r = probe(sb, _READ, _paths(world))
        got = out_of(r)
    blob = r.stdout + r.stderr
    for name, tok in world.tokens.items():
        assert tok not in blob, f"canary {name} leaked"
        assert failed(got, f"read:{name}"), f"canary {name} was readable"
    for name in world.dirs:
        assert failed(got, f"list:{name}") and failed(got, f"scan:{name}"), f"directory {name} was listable"
    assert tree_hash(*world.hash_roots) == before


def test_the_real_home_is_not_reachable_through_the_password_database(world):
    with make() as sb:
        got = out_of(probe(sb, """
            import pwd
            real = pwd.getpwuid(os.getuid()).pw_dir
            OUT["pw_dir"] = real
            attempt("ls", lambda: os.listdir(real))
            attempt("ssh", lambda: open(os.path.join(DATA["real_home"], ".ssh", "id_ed25519")).read())
            attempt("env", lambda: open(os.path.join(DATA["real_home"], ".env")).read())
        """, {"real_home": os.environ["HOME"]}))
        for key in ("ssh", "env"):
            assert failed(got, key)
        # the user's actual login home (when it is not the temp HOME the suite set) must not list either
        if Path(got["pw_dir"]).resolve() != Path(os.environ["HOME"]).resolve():
            assert failed(got, "ls"), "the login home directory was listable"


def test_reads_fail_through_symlinks_hardlinks_children_and_a_nested_sandbox(world):
    target = str(world.files["frontier_salt"])
    with make() as sb:
        r = probe(sb, """
            t = DATA["target"]
            os.symlink(t, "lnk")
            attempt("symlink", lambda: open("lnk").read())
            os.symlink(DATA["dir"], "dirlnk")
            attempt("dirlink", lambda: os.listdir("dirlnk"))
            attempt("hardlink", lambda: (os.link(t, "hl"), open("hl").read())[1])
            attempt("child_cat", lambda: subprocess.run(["/bin/cat", t], capture_output=True, text=True, check=True).stdout)
            attempt("child_py", lambda: subprocess.run([sys.executable, "-I", "-c", "print(open(%r).read())" % t],
                                                       capture_output=True, text=True, check=True).stdout)
            nested = ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/bin/cat", t]
            attempt("nested", lambda: subprocess.run(nested, capture_output=True, text=True, check=True).stdout)
            attempt("chdir_rel", lambda: (os.chdir(os.path.dirname(t)), open(os.path.basename(t)).read())[1])
        """, {"target": target, "dir": str(world.dirs["frontier_dir"])})
        got = out_of(r)
    tok = world.tokens["frontier_salt"]
    assert tok not in r.stdout + r.stderr
    for key in ("symlink", "dirlink", "hardlink", "child_cat", "child_py", "nested", "chdir_rel"):
        assert failed(got, key), f"{key} escaped the sandbox"


def test_env_files_are_unreadable_wherever_they_are(world, worktree):
    paths = {
        "home": world.files["home_env"],
        "project": world.files["project_env"],
        "nested": world.files["project_env_nested"],
        "worktree": worktree.path / ".env",
        "worktree_local": worktree.path / "sub" / ".env.local",
    }
    with make(worktree=worktree.path) as sb:
        got = out_of(probe(sb, """
            for k, p in DATA["paths"].items():
                attempt(k, lambda p=p: open(p).read())
            found = []
            for dp, dn, fn in os.walk(os.getcwd()):
                found += [f for f in fn if f == ".env" or f.startswith(".env.")]
            OUT["found"] = found
        """, {"paths": {k: str(v) for k, v in paths.items()}}))
    for k in paths:
        assert failed(got, k), f".env at {k} was readable"
    assert got["found"] == [], "no .env may exist in the candidate's working copy"


def test_traces_folder_and_core_state_db_are_unreadable(world, monkeypatch):
    """The real default paths: $TANISHI_HOME/traces and TANISHI_CORE_STATE_DB under the caller's HOME."""
    db = Path(os.environ["TANISHI_CORE_STATE_DB"])
    _put(db, "SB1-CANARY-db-" + uuid.uuid4().hex)
    traces = Path(os.environ["HOME"]) / ".tanishi" / "traces"
    with make() as sb:
        got = out_of(probe(sb, """
            attempt("db", lambda: open(DATA["db"], "rb").read())
            attempt("traces", lambda: os.listdir(DATA["traces"]))
            attempt("trace_file", lambda: open(os.path.join(DATA["traces"], "2026-10-11.jsonl")).read())
        """, {"db": str(db), "traces": str(traces)}))
    assert failed(got, "db") and failed(got, "traces") and failed(got, "trace_file")


# --- worktree copy ---------------------------------------------------------------------------------------------------


def test_worktree_is_copied_without_secrets_and_original_is_untouched(worktree):
    before = tree_hash(worktree.path)
    with make(worktree=worktree.path) as sb:
        got = out_of(probe(sb, """
            files = []
            for dp, dn, fn in os.walk("."):
                dn[:] = [d for d in dn if d != ".git"]
                files += [os.path.join(dp, f)[2:] for f in fn]
            OUT["files"] = sorted(files)
            OUT["app"] = open("src/app.py").read()
            open("src/app.py", "w").write("def answer():\\n    return 0\\n")
            open("new.txt", "w").write("n")
            os.remove("README.md")
            for k, p in DATA["secrets"].items():
                attempt(k, lambda p=p: open(p).read())
        """, {"secrets": {k: str(worktree.path / k) for k in worktree.secrets}}))
        assert Path(sb.workdir).resolve() != worktree.path.resolve()
        assert "src/app.py" in got["files"] and "README.md" in got["files"]
        assert got["app"] == "def answer():\n    return 41\n"
        for f in got["files"]:
            parts = f.split("/")
            assert not any(x == ".env" or x.startswith(".env.") for x in parts), f"{f} was copied into the sandbox"
            assert not set(parts[:-1]) & {"vault", "builder_vault", "traces", ".ssh"}, f"{f} was copied"
        for rel in worktree.secrets:
            assert failed(got, rel), f"{rel} was readable by its original path"
    assert tree_hash(worktree.path) == before


# --- network -------------------------------------------------------------------------------------------------------


@pytest.fixture
def tcp_server():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.2)
    hits: list[bytes] = []
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except (TimeoutError, OSError):
                continue
            c.settimeout(2)
            try:
                hits.append(c.recv(64) or b"<connected>")
            except OSError:
                hits.append(b"<connected>")
            c.close()

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    yield SimpleNamespace(port=srv.getsockname()[1], hits=hits)
    stop.set()
    t.join(2)
    srv.close()


_CONNECT = """
def _c():
    s = socket.create_connection(("127.0.0.1", DATA["port"]), timeout=3)
    s.sendall(b"ping"); s.close(); return "sent"
attempt("connect", _c)
attempt("child", lambda: subprocess.run([sys.executable, "-I", "-c",
    "import socket; s=socket.create_connection(('127.0.0.1', %d), timeout=3); s.sendall(b'ping')" % DATA["port"]],
    check=True, capture_output=True, timeout=10).returncode)
def _udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.sendto(b"ping", ("127.0.0.1", DATA["port"])); return "sent"
attempt("udp", _udp)
"""


def test_no_network_unless_granted(tcp_server):
    with make() as sb:  # network defaults to False
        got = out_of(probe(sb, _CONNECT, {"port": tcp_server.port}))
    time.sleep(0.5)
    assert failed(got, "connect") and failed(got, "child"), "an outbound connection succeeded without a grant"
    assert tcp_server.hits == [], "the server saw traffic from a no-network sandbox"


def test_network_is_available_when_the_task_grants_it(tcp_server):
    with make(network=True) as sb:
        if sb.backend == "docker":
            pytest.skip("host loopback is not the container's loopback; the grant is not testable this way under Docker")
        got = out_of(probe(sb, _CONNECT, {"port": tcp_server.port}))
    assert got["connect"][0] == "ok" and got["child"][0] == "ok"
    time.sleep(0.5)
    assert b"ping" in tcp_server.hits


# --- limits --------------------------------------------------------------------------------------------------------------


def test_wall_time_limit_kills_a_sleeper():
    with make(wall_seconds=2, cpu_seconds=30) as sb:
        r = sb.run([sys.executable, "-I", "-c", "import time; print('started', flush=True); time.sleep(120)"])
    assert r.timed_out is True
    assert r.returncode != 0
    assert r.duration_s < 20
    assert "started" in r.stdout, "output produced before the kill must be returned"


def test_cpu_limit_stops_a_busy_loop_before_the_wall_limit():
    with make(cpu_seconds=1, wall_seconds=30) as sb:
        r = sb.run([sys.executable, "-I", "-c", "print('spin', flush=True)\nwhile True:\n    pass"])
    assert r.returncode != 0
    assert r.duration_s < 15, f"a 1 s CPU limit took {r.duration_s:.1f}s to bite"
    assert "spin" in r.stdout


def test_memory_limit_stops_a_growing_process():
    code = textwrap.dedent(
        """
        import time
        chunks = []
        for i in range(16):
            chunks.append(b"x" * (64 * 1024 * 1024))
            print("chunk", i, flush=True)
            time.sleep(0.25)
        print("DONE", flush=True)
        """
    )
    with make(memory_mb=128, wall_seconds=60, cpu_seconds=60) as sb:
        r = sb.run([sys.executable, "-I", "-c", code])
    assert "DONE" not in r.stdout, "1 GB was allocated under a 128 MB limit"
    assert r.returncode != 0


def test_the_candidate_cannot_raise_its_own_limits():
    code = textwrap.dedent(
        """
        import resource, time
        for name in ("RLIMIT_CPU", "RLIMIT_AS", "RLIMIT_DATA"):
            try:
                resource.setrlimit(getattr(resource, name), (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
            except (ValueError, OSError):
                pass
        t = time.time()
        while time.time() - t < 20:
            pass
        print("SURVIVED", flush=True)
        """
    )
    with make(cpu_seconds=1, wall_seconds=30) as sb:
        r = sb.run([sys.executable, "-I", "-c", code])
    assert "SURVIVED" not in r.stdout
    assert r.returncode != 0


@pytest.mark.parametrize("detach", ["plain", "setsid"])
def test_a_timeout_leaves_no_process_behind(detach):
    kw = "start_new_session=True" if detach == "setsid" else ""
    code = textwrap.dedent(
        f"""
        import subprocess, time
        p = subprocess.Popen(["/bin/sleep", "300"], {kw})
        print(p.pid, flush=True)
        time.sleep(300)
        """
    )
    with make(wall_seconds=2) as sb:
        r = sb.run([sys.executable, "-I", "-c", code])
        backend = sb.backend
    assert r.timed_out
    if backend == "docker":
        return  # the pid is in the container's namespace; the container itself is gone
    pid = int(r.stdout.split()[0])
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    os.kill(pid, 9)
    pytest.fail(f"a {detach} child of the candidate survived the sandbox")


# --- 3. writes outside scratch fail ------------------------------------------------------------------------------------


def test_writes_outside_scratch_fail(world, cleanup):
    tag = uuid.uuid4().hex
    repo_file = REPO_ROOT / f"sb1-write-canary-{tag}"
    tmp_file = Path("/tmp") / f"sb1-write-canary-{tag}"
    cleanup.extend([repo_file, tmp_file])
    new_in_dirs = {k: str(v / f"new-{tag}.txt") for k, v in world.dirs.items()}
    existing = {k: str(v) for k, v in world.files.items()}
    sibling = str(world.root / f"sibling-{tag}.txt")
    home_new = str(Path(os.environ["HOME"]) / f"home-new-{tag}.txt")
    before = tree_hash(*world.hash_roots)
    with make() as sb:
        r = probe(sb, """
            def w(p, mode="w"):
                with open(p, mode) as f:
                    f.write("PWNED")
                return "wrote"
            T = DATA
            attempt("sibling", lambda: w(T["sibling"]))
            attempt("home_new", lambda: w(T["home_new"]))
            attempt("repo", lambda: w(T["repo"]))
            attempt("tmp", lambda: w(T["tmp"]))
            attempt("osopen", lambda: os.close(os.open(T["sibling"] + ".o", os.O_CREAT | os.O_WRONLY)))
            attempt("traversal", lambda: w(os.path.join(os.getcwd(), *([".."] * 12), T["sibling"].lstrip("/"))))
            for k, p in T["new_in_dirs"].items():
                attempt("newfile:" + k, lambda p=p: w(p))
            for k, p in T["existing"].items():
                attempt("overwrite:" + k, lambda p=p: w(p))
                attempt("append:" + k, lambda p=p: w(p, "a"))
                attempt("rplus:" + k, lambda p=p: open(p, "r+").truncate(0))
                attempt("delete:" + k, lambda p=p: os.remove(p))
                attempt("rename:" + k, lambda p=p: os.rename(p, p + ".moved"))
                attempt("chmod:" + k, lambda p=p: os.chmod(p, 0o777))
            attempt("rmtree", lambda: shutil.rmtree(os.path.dirname(T["existing"]["plain_outside"])))
            attempt("mkdir", lambda: os.makedirs(T["sibling"] + ".d"))
            os.symlink(os.path.dirname(T["sibling"]), "escape")
            attempt("via_symlink", lambda: w(os.path.join("escape", "via-symlink.txt")))
            attempt("child_sh", lambda: subprocess.run(["/bin/sh", "-c", "echo PWNED > '%s'" % (T["sibling"] + ".sh")],
                                                       check=True, capture_output=True))
            attempt("child_py", lambda: subprocess.run([sys.executable, "-I", "-c",
                "open(%r, 'w').write('PWNED')" % (T["sibling"] + ".py")], check=True, capture_output=True))
            # positive controls: scratch is writable
            OUT["scratch_cwd"] = w("ok-cwd.txt")
            OUT["scratch_home"] = w(os.path.join(os.environ["HOME"], "ok-home.txt"))
        """, {"sibling": sibling, "home_new": home_new, "repo": str(repo_file), "tmp": str(tmp_file),
              "new_in_dirs": new_in_dirs, "existing": existing})
        got = out_of(r)
    assert got["scratch_cwd"] == "wrote" and got["scratch_home"] == "wrote"
    for key, val in got.items():
        if key.startswith(("sibling", "home_new", "repo", "tmp", "osopen", "traversal", "newfile:", "overwrite:",
                           "append:", "rplus:", "delete:", "rename:", "chmod:", "rmtree", "mkdir", "via_symlink",
                           "child_sh", "child_py")):
            assert val[0] == "err", f"{key} succeeded outside scratch"
    assert not repo_file.exists() and not tmp_file.exists()
    assert not any(Path(str(world.root)).glob("sibling-*")), "a file appeared next to the guarded folders"
    assert tree_hash(*world.hash_roots) == before, "something outside scratch changed"
    assert not any(REPO_ROOT.glob("sb1-write-canary-*"))


# --- 4. rollback -------------------------------------------------------------------------------------------------------

_BROKEN = {
    "raises": "raise RuntimeError('boom')",
    "hard_exit": "os._exit(7)",
    "hangs": "time.sleep(300)",
    "wipes_workdir": """
        for n in os.listdir("."):
            shutil.rmtree(n, ignore_errors=True) if os.path.isdir(n) else os.remove(n)
    """,
    "trashes_everything_it_can_reach": """
        for p in DATA["victims"]:
            shutil.rmtree(p, ignore_errors=True)
            try: os.remove(p)
            except OSError: pass
        subprocess.run(["/bin/rm", "-rf", *DATA["victims"]])
    """,
    "garbage_everywhere": """
        for p in DATA["victims"]:
            for target in (p, os.path.join(p, "junk.txt")):
                try: open(target, "w").write("garbage")
                except OSError: pass
        open("src/app.py", "w").write("syntax error (((")
    """,
    "git_commit_in_copy": """
        subprocess.run(["git", "-c", "user.name=x", "-c", "user.email=x@y.z", "commit", "--allow-empty", "-qm", "evil"])
        open("src/app.py", "a").write("# evil\\n")
    """,
}


@pytest.mark.parametrize("name", list(_BROKEN))
def test_a_broken_candidate_is_rolled_back_with_no_change_outside_scratch(name, world, worktree):
    victims = [str(p) for p in (*world.files.values(), *world.dirs.values(), worktree.path, worktree.path / "src")]
    before = tree_hash(*world.hash_roots, worktree.path)
    kw = {"wall_seconds": 3} if name == "hangs" else {}
    with make(worktree=worktree.path, **kw) as sb:
        scratch = sb.scratch.resolve()
        assert (sb.workdir / "src" / "app.py").read_text() == "def answer():\n    return 41\n", "worktree not copied"
        body = textwrap.dedent(_BROKEN[name])
        r = sb.run([sys.executable, "-I", "-c", source(body, {"victims": victims})])
        assert r.returncode != 0 or name in ("wipes_workdir", "trashes_everything_it_can_reach", "garbage_everywhere",
                                             "git_commit_in_copy")
    after = tree_hash(*world.hash_roots, worktree.path)
    assert after == before, f"{name}: something outside scratch changed"
    assert not scratch.exists(), f"{name}: scratch survived the sandbox"
    status = subprocess.run([*worktree.git, "status", "--porcelain"], capture_output=True, text=True, check=True)
    assert status.stdout.strip() == ""
    head = subprocess.run([*worktree.git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    assert head.stdout.strip() == worktree.head


def test_rollback_resets_the_working_copy_and_the_home(worktree):
    with make(worktree=worktree.path) as sb:
        out_of(probe(sb, """
            open("src/app.py", "w").write("def answer():\\n    return 0\\n")
            open("new.txt", "w").write("n")
            os.remove("README.md")
            pathlib.Path(os.environ["HOME"], "leftover.txt").write_text("l")
            os.makedirs(os.path.join(os.environ["HOME"], ".tanishi"), exist_ok=True)
            pathlib.Path(os.environ["HOME"], ".tanishi", "state").write_text("s")
        """))
        # Without a rollback the state carries over (so the reset below is not vacuous).
        carried = out_of(probe(sb, "OUT['new'] = os.path.exists('new.txt'); OUT['home'] = os.path.exists(os.path.join(os.environ['HOME'], 'leftover.txt'))"))
        assert carried["new"] is True and carried["home"] is True
        sb.rollback()
        got = out_of(probe(sb, """
            OUT["new"] = os.path.exists("new.txt")
            OUT["readme"] = os.path.exists("README.md")
            OUT["app"] = open("src/app.py").read()
            OUT["leftover"] = os.path.exists(os.path.join(os.environ["HOME"], "leftover.txt"))
            OUT["state"] = os.path.exists(os.path.join(os.environ["HOME"], ".tanishi", "state"))
        """))
        assert got["new"] is False and got["leftover"] is False and got["state"] is False
        assert got["readme"] is True
        assert got["app"] == "def answer():\n    return 41\n"


def test_rollback_works_without_a_worktree():
    with make() as sb:
        out_of(probe(sb, "open('a.txt','w').write('a')"))
        sb.rollback()
        got = out_of(probe(sb, "OUT['a'] = os.path.exists('a.txt')"))
        assert got["a"] is False


def test_leaving_the_with_block_on_an_exception_still_rolls_back(world, worktree):
    before = tree_hash(*world.hash_roots, worktree.path)
    scratch = None
    with pytest.raises(KeyError), make(worktree=worktree.path) as sb:
        scratch = sb.scratch.resolve()
        sb.run([sys.executable, "-I", "-c", "open('x.txt','w').write('x')"])
        raise KeyError("caller failed")
    assert scratch is not None and not scratch.exists()
    assert tree_hash(*world.hash_roots, worktree.path) == before


def test_rollback_kills_leftover_processes_and_a_second_run_is_usable(worktree):
    with make(worktree=worktree.path, wall_seconds=20) as sb:
        if sb.backend == "docker":
            pytest.skip("pids live in the container namespace")
        r = sb.run([sys.executable, "-I", "-c",
                    "import subprocess; p = subprocess.Popen(['/bin/sleep','300'], start_new_session=True); print(p.pid, flush=True)"])
        pid = int(r.stdout.split()[0])
        sb.rollback()
        deadline = time.time() + 5
        alive = True
        while alive and time.time() < deadline:
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except ProcessLookupError:
                alive = False
        if alive:
            os.kill(pid, 9)
        assert not alive, "rollback left a running process"
        assert out_of(probe(sb, "pass"))["ran"] is True


# --- 5. backends -------------------------------------------------------------------------------------------------------


def test_works_without_docker_on_the_path(monkeypatch, world):
    kept = [d for d in os.environ["PATH"].split(os.pathsep) if not (Path(d) / "docker").exists()]
    monkeypatch.setenv("PATH", os.pathsep.join(kept) or "/usr/bin:/bin")
    assert shutil.which("docker") is None
    with Sandbox() as sb:  # default prefer_docker=True, but there is no docker
        assert sb.backend != "docker"
        got = out_of(probe(sb, _READ, _paths(world)))
        assert failed(got, "read:frontier_salt") and failed(got, "read:vault")


def test_prefer_docker_false_never_uses_docker():
    with Sandbox(prefer_docker=False) as sb:
        assert sb.backend != "docker"
        assert sb.run([sys.executable, "-I", "-c", "print(1)"]).stdout.strip() == "1"


@pytest.mark.skipif(not DOCKER, reason="Docker is not installed or its daemon is not running")
def test_docker_is_used_when_present_and_still_confines(world, cleanup):
    sibling = world.root / f"docker-sibling-{uuid.uuid4().hex}.txt"
    with Sandbox() as sb:
        assert sb.backend == "docker"
        got = out_of(probe(sb, _READ + """
try:
    open(DATA["sibling"], "w").write("PWNED"); OUT["write"] = "wrote"
except BaseException as e:
    OUT["write"] = "err"
""", {**_paths(world), "sibling": str(sibling)}))
    assert not sibling.exists()
    assert failed(got, "read:frontier_salt") and failed(got, "read:ssh_key") and got["write"] == "err"


# --- property tests ------------------------------------------------------------------------------------------------------

_KINDS = ["write", "append", "delete", "rename", "mkdir", "rmtree", "chmod", "truncate"]
_NAMES = ["files.frontier_salt", "files.vault", "files.ssh_key", "files.home_env", "files.traces",
          "dirs.frontier_dir", "dirs.vault_dir", "dirs.ssh_dir", "dirs.outside_dir", "rel.a", "rel.b/c", "rel.src/app.py"]

_OPS_CODE = """
for kind, p in DATA["ops"]:
    try:
        if kind == "write": open(p, "w").write("x")
        elif kind == "append": open(p, "a").write("x")
        elif kind == "delete": os.remove(p)
        elif kind == "rename": os.rename(p, p + ".moved")
        elif kind == "mkdir": os.makedirs(p, exist_ok=True)
        elif kind == "rmtree": shutil.rmtree(p)
        elif kind == "chmod": os.chmod(p, 0o777)
        elif kind == "truncate": open(p, "r+").truncate(0)
    except Exception:
        pass
"""


@settings(max_examples=12, deadline=None, suppress_health_check=list(HealthCheck))
@given(st.lists(st.tuples(st.sampled_from(_KINDS), st.sampled_from(_NAMES)), max_size=14))
def test_property_no_sequence_of_operations_changes_anything_outside_scratch(ops):
    """Invariant: for any sequence of file operations the candidate attempts, the guarded hash is unchanged."""
    with tempfile.TemporaryDirectory(prefix="sb1-prop-") as td:
        root = Path(td)
        w = make_world(root / "world", root / "home")
        wt = root / "wt"
        _put(wt / "src" / "app.py", "x = 1\n")
        before = tree_hash(root)
        resolved = []
        for kind, name in ops:
            space, key = name.split(".", 1)
            if space == "files":
                p = str(w.files[key])
            elif space == "dirs":
                p = str(w.dirs[key])
            else:
                p = key
            resolved.append([kind, p])
        with make(worktree=wt) as sb:
            r = sb.run([sys.executable, "-I", "-c", source(_OPS_CODE, {"ops": resolved})])
            assert r.returncode == 0, r.stderr[-300:]
            scratch = sb.scratch.resolve()
        assert tree_hash(root) == before
        assert not scratch.exists()


_FILE_NAMES = st.from_regex(r"[a-z]{1,8}\.bin", fullmatch=True)


@settings(max_examples=8, deadline=None, suppress_health_check=list(HealthCheck))
@given(st.dictionaries(_FILE_NAMES, st.binary(max_size=64), min_size=1, max_size=5))
def test_property_scratch_round_trip_then_rollback_forgets(files):
    """What a run writes, the next run reads back byte for byte; after rollback none of it is there."""
    payload = {k: v.hex() for k, v in files.items()}
    write = "for k, v in DATA['files'].items():\n    open(k, 'wb').write(bytes.fromhex(v))\n"
    read = ("for k in DATA['files']:\n"
            "    OUT[k] = open(k, 'rb').read().hex() if os.path.exists(k) else None\n")
    with make() as sb:
        out_of(probe(sb, write, {"files": payload}))
        got = out_of(probe(sb, read, {"files": payload}))
        assert {k: got[k] for k in payload} == payload
        sb.rollback()
        got = out_of(probe(sb, read, {"files": payload}))
        assert all(got[k] is None for k in payload)
