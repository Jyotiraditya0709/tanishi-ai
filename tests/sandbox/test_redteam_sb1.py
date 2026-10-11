"""SB1 red-team proofs. Written by the red-team agent, not the implementer or the exam author.

Each test is one break found by attacking tanishi/sandbox (macOS sandbox-exec backend, Python 3.12). A break is a
strict xfail: it fails today for the reason in its reason string. When the fix lands the test XPASSes, strict mode
turns that into a failure, and whoever fixed it removes the mark (same convention as AR3 RT-2/RT-3). Every test
first proves the control: the candidate ran, and the plain case (lower-case name, no grant) is refused, so a
sandbox that merely refuses to start cannot make a break look fixed.

Report: build/memory/runs/redteam-SB1-20261012.md
"""
import contextlib
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

from tanishi.sandbox import Sandbox, SandboxUnavailable

REPO_ROOT = Path(__file__).resolve().parents[2]
DARWIN = sys.platform == "darwin"
mac_only = pytest.mark.skipif(not DARWIN, reason="sandbox-exec profile attack")


def _run(sb: Sandbox, code: str, **kw):
    return sb.run([sys.executable, "-I", "-c", textwrap.dedent(code)], **kw)


def _sb(**kw) -> Sandbox:
    kw.setdefault("prefer_docker", False)  # AR1 does the same; the macOS profile is the target here
    kw.setdefault("wall_seconds", 30)
    return Sandbox(**kw)


def _canary() -> str:
    return "SB1-RT-" + uuid.uuid4().hex


# --- RT-SB1-1 · network=True hands out every unix socket on the machine --------------------------------------------


@mac_only
def test_rt1_network_grant_reaches_unix_sockets_outside_scratch():
    """`(allow network* system-socket)` includes AF_UNIX connect to any path: ssh-agent, docker.sock, gpg-agent, any
    local daemon. A task that is "granted network" is granted the host's local IPC too, which defeats the file
    confinement (docker.sock = root-equivalent; an ssh agent signs with the user's keys without ever reading them)."""
    token = _canary()
    d = Path(tempfile.mkdtemp(prefix="sb1rt-sock-"))
    path = str(d / "agent.sock")
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(path)
    srv.listen(4)
    srv.settimeout(20)

    def serve():
        try:
            while True:
                c, _ = srv.accept()
                c.sendall(token.encode())
                c.close()
        except OSError:
            pass

    threading.Thread(target=serve, daemon=True).start()
    code = f"""
        import socket
        s = socket.socket(socket.AF_UNIX)
        try:
            s.connect({path!r})
            print("GOT", s.recv(200).decode())
        except OSError as e:
            print("ERR", type(e).__name__)
        """
    try:
        with _sb(network=False) as sb:
            control = _run(sb, code)
        assert "ERR" in control.stdout, control  # no network grant: refused
        with _sb(network=True) as sb:
            granted = _run(sb, code)
        assert "GOT" in granted.stdout or "ERR" in granted.stdout, granted  # the candidate ran
        assert token not in granted.stdout, "network=True lets a candidate talk to a local unix socket"
    finally:
        srv.close()
        for p in d.iterdir():
            p.unlink()
        d.rmdir()


# --- RT-SB1-2 · guarded names are case-sensitive on a case-insensitive filesystem -----------------------------------


@mac_only
def test_rt2_guarded_names_are_not_bypassed_by_changing_case(tmp_path):
    """Regression guard, not a break: APFS is case-insensitive, and the profile's deny regex compares bytes, but the
    kernel hands the profile the canonical path, so `VAULT/`, `.ENV`, `Traces/`, `.SSH/` are refused too. (The
    worktree *copy* filter has no such help: see rt2b.)"""
    root = tmp_path / "proj"
    files = {"vault/exam.yaml": "vault", ".env": "dotenv", "traces/t.jsonl": "traces", ".ssh/id_ed25519": "ssh",
             "builder_vault/hidden.py": "builder"}
    tokens = {}
    for rel, name in files.items():
        tokens[name] = _canary()
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(tokens[name])
    (root / "ok.txt").write_text("plain")
    if not (root / "VAULT" / "exam.yaml").exists():
        pytest.skip("case-sensitive filesystem")
    variants = ["VAULT/exam.yaml", ".ENV", "Traces/t.jsonl", ".SSH/id_ed25519", "Builder_Vault/hidden.py"]
    plain = list(files)
    code = f"""
        import json, os
        out = {{}}
        for rel in {plain + variants + ["ok.txt"]!r}:
            try:
                out[rel] = open(os.path.join({str(root)!r}, rel)).read()
            except OSError:
                out[rel] = None
        print(json.dumps(out))
        """
    with _sb(read_paths=[root]) as sb:
        res = _run(sb, code)
    got = json.loads(res.stdout)
    assert got["ok.txt"] == "plain"  # the grant works
    assert all(got[p] is None for p in plain), got  # exact names are refused: the control
    leaked = {v: got[v] for v in variants if got[v] is not None}
    assert not leaked, f"case variants of guarded names were readable: {sorted(leaked)}"


@mac_only
def test_rt2b_worktree_copy_keeps_case_variants_of_guarded_names(tmp_path):
    """Same bug in `_not_copied`: a checkout whose vault folder is spelled `Vault` is copied into the candidate's
    workdir, where it is plainly readable (the workdir is inside scratch, which the profile allows)."""
    wt = tmp_path / "wt"
    (wt / "Vault").mkdir(parents=True)
    token = _canary()
    (wt / "Vault" / "exam.yaml").write_text(token)
    (wt / ".ENV").write_text(token)
    (wt / "code.py").write_text("x = 1\n")
    with _sb(worktree=wt) as sb:
        assert (sb.workdir / "code.py").exists()
        copied = sorted(p.name for p in sb.workdir.iterdir())
    assert "Vault" not in copied and ".ENV" not in copied, copied


# --- RT-SB1-3 · the worktree copy brings the legacy databases and .git along -----------------------------------------


def test_rt3_worktree_copy_contains_databases_and_git_history(tmp_path):
    """CLAUDE.md: `data/db.sqlite` is the legacy conversation db. `_not_copied` drops only guarded *names*, so the
    candidate gets a copy of every `*.db`/`*.sqlite*` file and the whole `.git` of the checkout it was given (every
    committed file in history, including anything that was ever committed under a later-protected path)."""
    wt = tmp_path / "wt"
    (wt / "data").mkdir(parents=True)
    (wt / "data" / "db.sqlite").write_bytes(b"SQLite format 3\0" + _canary().encode())
    (wt / "notes.db").write_bytes(b"x")
    (wt / ".git").mkdir()
    (wt / ".git" / "packed-refs").write_text(_canary())
    (wt / "mod.py").write_text("y = 2\n")
    with _sb(worktree=wt) as sb:
        names = {str(p.relative_to(sb.workdir)) for p in sb.workdir.rglob("*")}
    assert "mod.py" in names
    leaked = {n for n in names if n.endswith((".db", ".sqlite")) or n.startswith(".git")}
    assert not leaked, f"copied into the candidate's workdir: {sorted(leaked)}"


def test_rt3b_read_path_grants_whole_repo_including_legacy_db(tmp_path):
    """AR1 passes every `sys.path` entry as a read path, and the repo root is one. The profile guards five names;
    `data/db.sqlite` under the same root is readable, personal conversation text included."""
    root = tmp_path / "repo"
    token = _canary()
    (root / "data").mkdir(parents=True)
    (root / "data" / "db.sqlite").write_text(token)
    (root / "tanishi").mkdir()
    (root / "tanishi" / "__init__.py").write_text("")
    code = f"""
        try:
            print("GOT", open({str(root / 'data' / 'db.sqlite')!r}).read())
        except OSError as e:
            print("ERR", type(e).__name__)
        try:
            print("PKG", open({str(root / 'tanishi' / '__init__.py')!r}).read() == "")
        except OSError as e:
            print("PKGERR", type(e).__name__)
        """
    with _sb(read_paths=[root]) as sb:
        res = _run(sb, code)
    assert "PKG True" in res.stdout, res
    assert token not in res.stdout, "a read path exposes *.db / *.sqlite files under it"


# --- RT-SB1-4 · a read-only hard link or clone out of a guarded folder --------------------------------------------


@mac_only
def test_rt4_link_or_clone_from_guarded_file_into_scratch(tmp_path):
    """The deny rule is on the path. link(2)/clonefile(2) name the source by path too, so these are expected to be
    refused; the test pins that (a pass here is a regression guard, not a break)."""
    root = tmp_path / "proj"
    token = _canary()
    (root / "vault").mkdir(parents=True)
    (root / "vault" / "exam.yaml").write_text(token)
    (root / "ok.txt").write_text("plain")
    code = f"""
        import ctypes, os
        src = {str(root / 'vault' / 'exam.yaml')!r}
        home = os.environ["HOME"]
        out = []
        try:
            os.link(src, home + "/hard")
            out.append(open(home + "/hard").read())
        except OSError as e:
            out.append("link-ERR")
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        r = libc.clonefile(src.encode(), (home + "/clone").encode(), 0)
        try:
            out.append(open(home + "/clone").read())
        except OSError:
            out.append("clone-ERR")
        try:
            os.rename(src, home + "/moved")
            out.append(open(home + "/moved").read())
        except OSError:
            out.append("rename-ERR")
        print("|".join(out))
        """
    with _sb(read_paths=[root]) as sb:
        res = _run(sb, code)
    assert "link-ERR" in res.stdout, res
    assert token not in res.stdout
    assert (root / "vault" / "exam.yaml").read_text() == token


# --- RT-SB1-5 · a candidate can see the environment of the user's other processes (API keys) -------------------------


#   sandbox-exec cannot hide other processes (conflict R1, decision 0025 §3), so candidate code (trusted=False) runs
#   in Docker on macOS and under Landlock on Linux. The claim is tested through that path, the way AR1 builds it.

# What the candidate runs. The secret arrives on stdin, so it is on no command line and in no environment. The scan
# covers every route the red team used: /proc/*/environ and /proc/*/cmdline (Linux, so also a container), sysctl
# KERN_PROCARGS2 (macOS), and `ps` listings that print environments (BSD `ps -E`, procps `ps e`). It also starts a
# child of its own whose environment holds a *control* value, so a scan that cannot see anything cannot pass.
_SCAN = """
import ctypes, os, subprocess, sys
want = sys.stdin.buffer.read().strip()
control = b"SB1-RT5-CONTROL-" + os.urandom(8).hex().encode()
child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(30)"],
                         env={"PATH": os.environ.get("PATH", ""), "SB1_RT5_CONTROL": control.decode()})
blobs = []


def grab(data):
    if data:
        blobs.append(data)


if os.path.isdir("/proc"):
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            for leaf in ("environ", "cmdline"):
                try:
                    grab(open("/proc/%s/%s" % (pid, leaf), "rb").read())
                except OSError:
                    pass
if sys.platform == "darwin":
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        for pid in range(1, 100000):
            mib = (ctypes.c_int * 3)(1, 49, pid)
            size = ctypes.c_size_t(0)
            if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) == 0 and size.value:
                buf = ctypes.create_string_buffer(size.value)
                if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) == 0:
                    grab(buf.raw[: size.value])
    except OSError:
        pass
for flags in (["-E", "-ww", "-A", "-o", "command="], ["eww", "-A"], ["-eww", "-o", "args="], ["auxeww"]):
    for exe in ("/bin/ps", "/usr/bin/ps"):
        try:
            done = subprocess.run([exe] + flags, capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            grab(done.stdout)
        break
child.kill()
print("CONTROL", any(control in b for b in blobs))
print("FOUND", any(want in b for b in blobs))
"""

# The probe for the second test: the candidate's own argv.
_OWN = """
import ctypes, os, sys
want, found = sys.stdin.buffer.read().strip(), False
try:
    found = want in open("/proc/self/cmdline", "rb").read()
except OSError:
    pass
if not found and sys.platform == "darwin":
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    mib = (ctypes.c_int * 3)(1, 49, os.getpid())
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) == 0:
        buf = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) == 0:
            found = want in buf.raw[: size.value]
print("OWN", found)
"""


def _candidate_sandbox() -> Sandbox:
    """The way AR1 builds one for code a model wrote: never trusted, the default backend choice. On macOS that is
    Docker; without it the constructor fails closed and the test skips (it cannot pass, and must not fail)."""
    try:
        return Sandbox(trusted=False, wall_seconds=60, cpu_seconds=60)
    except SandboxUnavailable as e:
        pytest.skip(f"no isolating backend here (Docker is needed on macOS): {e}")


def test_rt5_candidate_cannot_read_the_arguments_or_environment_of_another_process_of_the_user():
    """The real claim: candidate code cannot read another same-user process's argv or environment. The token lives
    only in the environment of a helper process the test keeps alive; it is on no command line (the helper's own is
    a fixed string) and not in the candidate's environment (it comes in on stdin)."""
    token = _canary()
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(180)"], start_new_session=True,
                              env={"PATH": os.environ.get("PATH", ""), "SB1_RT_FAKE_API_KEY": token})
    try:
        time.sleep(0.5)
        if DARWIN:  # control: an unconfined process of this user can see the helper's secret, so it is really there
            seen = subprocess.run(["ps", "-E", "-ww", "-p", str(holder.pid), "-o", "command="],
                                  capture_output=True, text=True, check=False).stdout
            assert token in seen, "the helper's environment is not readable by an unconfined process; test is void"
        with _candidate_sandbox() as sb:
            assert sb.backend in ("docker", "landlock"), sb.backend
            res = sb.run([sys.executable, "-I", "-c", _SCAN], input=token)
        assert "CONTROL True" in res.stdout, f"the scan could not read a process it should see: {res}"
        assert "FOUND False" in res.stdout, f"the candidate read another process's argv or environment: {res}"
    finally:
        holder.kill()
        holder.wait()


def test_rt5b_candidate_can_read_its_own_arguments_which_is_not_a_sandbox_break():
    """Documented as impossible to defend, so a passing test and not an xfail. A process can always read its own
    argv (`/proc/self/cmdline`, `sysctl KERN_PROCARGS2` of its own pid): it was handed them. The original rt5 put
    its token in the candidate's own `-c` program, so it only ever proved this. Anything a caller puts on the
    candidate's command line is known to the candidate; secrets go on stdin or in a file, never in argv."""
    token = _canary()
    with _candidate_sandbox() as sb:
        # the token is on the command line on purpose (a trailing argument); stdin carries it only to compare
        res = sb.run([sys.executable, "-I", "-c", _OWN, token], input=token)
    assert "OWN True" in res.stdout, res


# --- RT-SB1-6 · rollback and close cannot always remove what the candidate made -------------------------------------


def _clear_flags_and_remove(path: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(path):
        for n in [*dirnames, *filenames]:
            try:
                os.chflags(os.path.join(dirpath, n), 0, follow_symlinks=False)
            except (OSError, AttributeError, NotImplementedError):
                pass
    subprocess.run(["rm", "-rf", str(path)], check=False)


@mac_only
def test_rt6_immutable_flag_survives_rollback_and_close():
    """`chflags uchg` is allowed inside scratch. `_force_rmtree` swallows the failure, so `rollback()` leaves the
    candidate's file in place (and `_fill`'s mkdir raises FileExistsError), and `close()` leaves scratch on disk."""
    sb = _sb()
    sb.open()
    scratch = sb.scratch
    try:
        res = _run(sb, """
            import os, stat
            p = os.path.join(os.environ["HOME"], "pin")
            open(p, "w").write("x")
            try:
                os.chflags(p, stat.UF_IMMUTABLE)
                print("FLAGGED")
            except OSError as e:
                print("NOFLAG", e)
            """)
        assert "FLAGGED" in res.stdout or "NOFLAG" in res.stdout, res
        if "NOFLAG" in res.stdout:
            pytest.skip("this profile refuses chflags")
        failure = None
        try:
            sb.rollback()
        except Exception as e:  # noqa: BLE001
            failure = e
        assert failure is None, f"rollback raised {failure!r}"
        assert not (sb.home / "pin").exists(), "the candidate's file survived rollback"
    finally:
        with contextlib.suppress(Exception):
            sb.close()
        leaked = scratch.exists()
        _clear_flags_and_remove(scratch)
    assert not leaked, "scratch is still on disk after close()"


def test_rt6b_deep_directory_tree_is_removed_by_close():
    """Regression guard, not a break: a 1200-level directory (mkdir + chdir, so no path is ever long) is allowed in
    scratch and `close()` removes it (shutil.rmtree did not hit the recursion limit here)."""
    sb = _sb(cpu_seconds=60, wall_seconds=60)
    sb.open()
    scratch, tagdir = sb.scratch, sb._tagdir
    try:
        res = _run(sb, """
            import os
            for _ in range(1200):
                os.mkdir("d")
                os.chdir("d")
            print("DEEP")
            """)
        assert "DEEP" in res.stdout, res
        failure = None
        try:
            sb.close()
        except BaseException as e:  # noqa: BLE001
            failure = e
        assert failure is None, f"close() raised {type(failure).__name__}"
        assert not scratch.exists(), "scratch left on disk"
        assert not tagdir.exists()
    finally:
        subprocess.run(["rm", "-rf", str(scratch), str(tagdir)], check=False)


# --- RT-SB1-7 · limits that the card promises but the code does not enforce ----------------------------------------


def test_rt7_fork_count_is_unbounded():
    """cpu/memory/wall are limited; process count is not. On macOS RLIMIT_NPROC is per user, so the sandbox must
    count the run's members itself (it already lists them every 0.1 s). 200 sleeping children are allowed here; a
    real fork bomb takes the user's whole session down before the wall limit."""
    with _sb(wall_seconds=15, memory_mb=2048) as sb:
        res = _run(sb, """
            import os, time
            kids = 0
            for _ in range(200):
                try:
                    pid = os.fork()
                except OSError:
                    break
                if pid == 0:
                    time.sleep(4)
                    os._exit(0)
                kids += 1
            print("KIDS", kids)
            """)
    assert "KIDS" in res.stdout, res
    kids = int(res.stdout.split("KIDS")[1].split()[0])
    assert kids < 200, f"{kids} processes were started"


def test_rt7b_scratch_disk_use_is_unbounded():
    """A candidate may write as much as the disk holds (200 MB here, in 0.3 s). `rollback` deletes it afterwards, but
    nothing stops a full disk during the run, which also breaks every other agent in the swarm on the machine."""
    with _sb(wall_seconds=30) as sb:
        res = _run(sb, """
            import os
            chunk = b"\\0" * (1 << 20)
            try:
                with open(os.path.join(os.environ["HOME"], "fill"), "wb") as f:
                    for _ in range(200):
                        f.write(chunk)
                print("WROTE 200MB")
            except OSError as e:
                print("STOPPED", type(e).__name__)
            """)
    assert "WROTE" in res.stdout or "STOPPED" in res.stdout, res
    assert "WROTE" not in res.stdout, "200 MB written into scratch"


# --- RT-SB1-8 · the code Tanishi writes still runs outside the sandbox on the Arena's critical path ------------------


def test_rt8_run_python_tests_runs_candidate_code_unconfined(tmp_path):
    """`tanishi/arena/practice/_common.py::_CodeProcess` starts `python -I _code_harness.py` with a plain Popen, in
    the AR1 verifier stage (which SB1 deliberately left unconfined). The harness `exec`s the candidate's *output
    string*. So the code a model writes for slugify/dedupe/fix_bug tasks (and every future code task) can read
    `~/.ssh`, the Frontier store or the legacy db and write anywhere, as the user. The card says code Tanishi writes
    runs only in a sandbox; this is the one place it does not."""
    from tanishi.arena.practice._common import run_python_tests

    secret = tmp_path / "secret.txt"
    secret.write_text(_canary())
    outside = tmp_path / "written_by_candidate.txt"
    code = textwrap.dedent(f"""
        import os
        try:
            open({str(outside)!r}, "w").write(open({str(secret)!r}).read())
        except OSError:
            pass
        def f(x):
            return x + 1
        """)
    verdict = run_python_tests(code, "assert f(1) == 2")
    assert verdict.score == 1.0  # the candidate ran and its function worked
    assert not outside.exists(), "candidate code read a file it was not given and wrote outside its folder"

