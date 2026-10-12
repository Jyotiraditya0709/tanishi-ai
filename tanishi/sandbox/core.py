"""Sandbox: run code Tanishi wrote or changed so it can read nothing it was not given and write only its scratch.

    with Sandbox(worktree=repo, network=False, cpu_seconds=10, memory_mb=512, wall_seconds=30) as sb:
        result = sb.run([sys.executable, "-I", "candidate.py"])
        sb.rollback()  # the next run starts from a clean copy

- **Who wrote the code.** `trusted=False` is for code a model wrote: it runs only on a backend that hides the user's
  other processes (Docker; Landlock on Linux), and on macOS without Docker the constructor raises instead of falling
  back to sandbox-exec, which cannot keep another process's environment (keys) from a candidate. `trusted=True` is
  for code the system itself wrote. Unset keeps the old choice (sandbox-exec allowed); callers that run candidate
  code pass `trusted=False` explicitly.
- **Scratch.** Each sandbox owns a fresh 0700 folder: `.home` (HOME, with `.tanishi` as TANISHI_HOME, the Core State
  db path and `tmp` as TMPDIR) and `.workdir` (the cwd). With `worktree=` the workdir is a *copy* of that checkout
  without `.git`, `.env*`, `*.db`, `*.sqlite*`, `vault/`, `builder_vault/`, `traces/` or `.ssh/` at any depth and in
  any letter case; the original is never mounted, so a candidate cannot read or change the real checkout.
- **Environment.** Built, not inherited: PATH, LANG, the variables above and the names in `env_passthrough`.
- **Confinement.** Default-deny (see backends.py): the interpreter, system libraries and `read_paths` are readable
  (minus guarded and database names inside them), scratch is writable, nothing else is reachable; the folder holding
  a read path may be listed, not read. No network unless `network=True`, and never a unix socket by path.
- **Limits.** `cpu_seconds` (RLIMIT_CPU, which the candidate cannot raise), `memory_mb` (resident memory of every
  process of the run, polled; RLIMIT_AS is not enforced on macOS) and `wall_seconds`, all required to be finite and
  positive: "no limit" cannot be asked for. `max_processes` (a fork past it fails; more live processes than that
  stops the run) and `max_disk_mb` (a file larger than that cannot be written; more new bytes in scratch than that
  stops the run).
- **Ending.** A run ends when its first process exits; anything it left running is killed then, even if it called
  setsid (macOS: the kernel names the processes under this sandbox's profile; Linux: the launcher is a subreaper).
  `rollback()` kills, clears file flags and permissions in scratch, wipes it and recreates it (and the worktree copy);
  leaving the `with` block kills and deletes scratch, even when the caller raised.
- **One script.** `run_python(script, *, stdin, timeout_s)` runs Python source a model wrote in a fresh untrusted
  sandbox and returns its returncode and clipped output (L1's Learning Compiler; decision 0025).
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from tanishi.sandbox import backends

OUTPUT_LIMIT = 8 * 1024 * 1024  # bytes kept per stream; the rest is read and dropped so the candidate never blocks
POLL_S = 0.1
DISK_POLL_S = 0.5  # how often the watchdog adds up scratch
_LAUNCH = str(Path(__file__).with_name("_launch.py"))
_RESERVED = frozenset({"HOME", "TANISHI_HOME", "TANISHI_CORE_STATE_DB", "TMPDIR"})
_VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class SandboxResult:
    """What one run did. `limit` is "wall", "memory", "processes" or "disk" when the sandbox killed it for that."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float
    limit: str | None = None


def _positive(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number, got {value!r}")  # noqa: TRY004 - None means "no limit"
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number, got {value!r}")
    return value


class Sandbox:
    """A confined place to run a candidate. See the module docstring."""

    def __init__(self, *, worktree: str | os.PathLike | None = None, network: bool = False, cpu_seconds: float = 10,
                 memory_mb: float = 512, wall_seconds: float = 30, env_passthrough=(), prefer_docker: bool = True,
                 read_paths=(), docker_image: str = backends.DOCKER_IMAGE, trusted: bool | None = None,
                 max_processes: int = 64, max_disk_mb: float = 128) -> None:
        self.cpu_seconds = _positive("cpu_seconds", cpu_seconds)
        self.memory_mb = _positive("memory_mb", memory_mb)
        self.wall_seconds = _positive("wall_seconds", wall_seconds)
        self.max_processes = math.ceil(_positive("max_processes", max_processes))
        self.max_disk_mb = _positive("max_disk_mb", max_disk_mb)
        if trusted is not None and not isinstance(trusted, bool):
            raise TypeError("trusted must be True, False or None")
        self.trusted = trusted
        if isinstance(env_passthrough, (str, bytes)):
            raise TypeError("env_passthrough must be a list of variable names")
        self.env_passthrough = tuple(env_passthrough)
        for n in self.env_passthrough:
            if not isinstance(n, str) or not _VAR_NAME.fullmatch(n) or n in _RESERVED:
                raise ValueError(f"env_passthrough cannot pass {n!r}")
        self.network = bool(network)
        self.worktree = None
        if worktree is not None:
            self.worktree = Path(worktree).resolve(strict=True)
            if not self.worktree.is_dir():
                raise ValueError(f"worktree {worktree} is not a directory")
        self.read_paths = tuple(checked_read_path(p) for p in read_paths)
        self.list_paths = _listable_parents(self.read_paths)
        self.docker_image = docker_image
        self.backend = backends.pick_backend(prefer_docker, self.network, trusted)
        self.scratch: Path | None = None
        self.home: Path | None = None
        self.workdir: Path | None = None
        self._token = uuid.uuid4().hex
        self._procs: list[subprocess.Popen] = []
        self._containers: list[str] = []
        self._watches: dict[int, _Watch] = {}

    def __repr__(self) -> str:
        return f"Sandbox(backend={self.backend!r}, scratch={str(self.scratch)!r})"

    # --- lifetime -------------------------------------------------------------------------------------------------

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        if self.scratch is not None:
            raise RuntimeError("this sandbox is already open")
        self._tagdir = Path(tempfile.mkdtemp(prefix="tanishi-sandbox-tag-")).resolve()
        self._tag = (str(self._tagdir / "yes"), str(self._tagdir / "no"))
        for p in self._tag:
            Path(p).touch(mode=0o600)
        self.scratch = Path(tempfile.mkdtemp(prefix="tanishi-sandbox-")).resolve()
        os.chmod(self.scratch, 0o700)
        self._fill()

    def close(self) -> None:
        """Kill everything the candidate started and delete scratch. Raises if scratch could not be deleted."""
        if self.scratch is None:
            return
        try:
            self._kill_all()
        finally:
            _force_rmtree(self.scratch)
            _force_rmtree(self._tagdir)
        if self.scratch.exists():
            raise RuntimeError(f"sandbox scratch {self.scratch} could not be deleted")

    def rollback(self) -> None:
        """Undo every run: kill its processes, wipe scratch, recreate HOME and the working copy from the original."""
        self._check_open()
        self._kill_all()
        for child in list(self.scratch.iterdir()):
            _force_rmtree(child)
        left = sorted(p.name for p in self.scratch.iterdir())
        if left:
            raise RuntimeError(f"rollback could not wipe scratch: {left} remain")
        self._fill()

    def _fill(self) -> None:
        self.home = self.scratch / "home"
        self.home.mkdir(mode=0o700)
        (self.home / ".tanishi").mkdir(mode=0o700)
        (self.home / "tmp").mkdir(mode=0o700)
        self.workdir = self.scratch / "work"
        if self.worktree is None:
            self.workdir.mkdir(mode=0o700)
        else:
            shutil.copytree(self.worktree, self.workdir, symlinks=True, ignore=_not_copied)
        self._disk_base = _disk_use(self.scratch)

    def _check_open(self) -> None:
        if self.scratch is None or not self.scratch.exists():
            raise RuntimeError("the sandbox is not open; use it as a context manager")

    # --- running --------------------------------------------------------------------------------------------------

    def env(self) -> dict[str, str]:
        """The whole environment a candidate gets."""
        env = {n: os.environ[n] for n in self.env_passthrough if n in os.environ}
        env["PATH"] = os.environ.get("PATH", os.defpath)
        if "LANG" in os.environ:
            env["LANG"] = os.environ["LANG"]
        tanishi_home = self.home / ".tanishi"
        env.update(HOME=str(self.home), TANISHI_HOME=str(tanishi_home),
                   TANISHI_CORE_STATE_DB=str(tanishi_home / "core_state.db"), TMPDIR=str(self.home / "tmp"))
        return env

    def popen(self, argv, *, cwd: str | os.PathLike | None = None, stdin=None, stdout=None, stderr=None,
              pass_fds=()) -> subprocess.Popen:
        """Start a confined process (its own session) under the limits watchdog; the caller waits for it.
        `cwd` must lie inside scratch (default: the working copy). Under Docker, `pass_fds` may hold one descriptor,
        and only with `stdout` left unset or DEVNULL: the container's stdout carries it."""
        self._check_open()
        argv = _checked_argv(argv)
        pass_fds = tuple(pass_fds)
        where = Path(cwd).resolve() if cwd is not None else self.workdir
        if not where.is_relative_to(self.scratch):
            raise ValueError("cwd must be inside the sandbox's scratch folder")
        env = self.env()
        cpu = max(1, math.ceil(self.cpu_seconds))
        fsize = math.ceil(self.max_disk_mb * 1024 * 1024)
        container = None
        if self.backend == "docker":
            if pass_fds and (len(pass_fds) > 1 or stdout not in (None, subprocess.DEVNULL)):
                raise ValueError("under Docker, pass_fds holds at most one descriptor and stdout must be DEVNULL")
            container = f"tanishi-sb-{self._token[:12]}-{len(self._containers)}"
            names = [k for k in env if k != "PATH"]
            cmd = backends.docker_argv(name=container, image=self.docker_image, scratch=str(self.scratch),
                                       cwd=str(where), env_names=names, read_paths=list(self.read_paths),
                                       network=self.network, cpu_seconds=cpu,
                                       memory_mb=max(6, math.ceil(self.memory_mb)), argv=argv,
                                       wall_seconds=math.ceil(self.wall_seconds) + 5,
                                       max_processes=self.max_processes, max_disk_mb=math.ceil(self.max_disk_mb),
                                       fds=pass_fds)
            env = {**os.environ, **env, "DOCKER_CONFIG": backends.docker_config_dir()}
            self._containers.append(container)
            if pass_fds:
                stdout, pass_fds = pass_fds[0], ()
        else:
            cfg: dict = {"cpu": cpu, "fsize": fsize, "nproc": _process_cap(self.max_processes), "landlock": None}
            if self.backend == "sandbox-exec":
                profile = backends.darwin_profile(str(self.scratch), list(self.read_paths), self.network, self._tag,
                                                  list(self.list_paths))
                argv = [backends.SANDBOX_EXEC, "-p", profile, *argv]
            else:
                rules = backends.landlock_rules(str(self.scratch), list(self.read_paths), list(self.list_paths))
                cfg["landlock"] = {"rules": rules, "deny_net": not self.network}
            cmd = [sys.executable, "-I", "-S", _LAUNCH, json.dumps(cfg), *argv]
        proc = subprocess.Popen(cmd, cwd=where, env=env, stdin=stdin, stdout=stdout, stderr=stderr,
                                pass_fds=pass_fds, start_new_session=True)
        self._procs.append(proc)
        self._watches[proc.pid] = _Watch(self, proc, container)
        return proc

    def run(self, argv, *, input: str | bytes | None = None) -> SandboxResult:
        """Run one candidate to the end and return what it did. Never raises for a candidate that fails."""
        start = time.monotonic()
        proc = self.popen(argv, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        watch = self._watches[proc.pid]
        out, err = _Capture(proc.stdout), _Capture(proc.stderr)
        if input is not None:
            data = input.encode() if isinstance(input, str) else bytes(input)
            threading.Thread(target=_feed, args=(proc.stdin, data), daemon=True).start()
        proc.wait()
        watch.join()
        self._kill_run(proc, watch.container)  # the run is over: nothing it started outlives it
        out.join(5)
        err.join(5)
        return SandboxResult(proc.returncode, out.text(), err.text(), watch.timed_out,
                             float(time.monotonic() - start), watch.limit)

    # --- killing --------------------------------------------------------------------------------------------------

    def kill(self, proc: subprocess.Popen) -> None:
        """Kill a process from popen() and everything it started (use this, not killpg: setsid escapes a group)."""
        watch = self._watches.get(proc.pid)
        self._kill_run(proc, watch.container if watch else None)

    def _kill_run(self, proc: subprocess.Popen, container: str | None) -> None:
        if container is not None:
            _docker_quiet("kill", container)
        else:
            _kill_members(self, {proc.pid})  # first: once the root dies its children lose their parent link
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if proc.poll() is None:
            proc.kill()

    def _kill_all(self) -> None:
        for name in self._containers:
            _docker_quiet("rm", "-f", name)
        if self.backend != "docker":
            _kill_members(self, {p.pid for p in self._procs if p.poll() is None})
        for p in self._procs:
            if p.poll() is None:
                p.kill()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                pass
        self._procs.clear()
        self._watches.clear()


PYTHON_OUTPUT_LIMIT = 64 * 1024  # bytes of each stream run_python returns


@dataclass(frozen=True)
class PythonResult:
    """What run_python's script did; `stdout` and `stderr` hold at most PYTHON_OUTPUT_LIMIT bytes of text each.
    It also unpacks as `returncode, stdout`, the pair L1's harness reads (`passes.run_skill_test`)."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool

    def __iter__(self):
        return iter((self.returncode, self.stdout))


def run_python(script: str | bytes, *, stdin: str | bytes | None = b"", timeout_s: float) -> PythonResult:
    """Run `script` (Python source as text or bytes, e.g. L1's generated skill test) as untrusted candidate code:
    never `trusted=True`, no network, nothing of this machine readable but the interpreter. `stdin` is its standard
    input (str or bytes; None = empty); it is killed after `timeout_s` seconds (int or float). Raises
    SandboxUnavailable where no backend hides the user's other processes (macOS without Docker). The script is a file
    in scratch, so its text is not on any command line."""
    if not isinstance(script, (str, bytes)):
        raise TypeError("script must be Python source, str or bytes")
    if stdin is not None and not isinstance(stdin, (str, bytes)):
        raise TypeError("stdin must be str, bytes or None")
    timeout_s = _positive("timeout_s", timeout_s)
    with Sandbox(trusted=False, network=False, cpu_seconds=timeout_s, wall_seconds=timeout_s) as sb:
        path = sb.workdir / "script.py"
        if isinstance(script, bytes):
            path.write_bytes(script)
        else:
            path.write_text(script, encoding="utf-8")
        res = sb.run([sys.executable, "-I", "script.py"], input=stdin if stdin is not None else b"")
    return PythonResult(res.returncode, _clip(res.stdout), _clip(res.stderr), res.timed_out)


def _clip(text: str) -> str:
    return text.encode("utf-8")[:PYTHON_OUTPUT_LIMIT].decode("utf-8", errors="ignore")


# --- helpers ------------------------------------------------------------------------------------------------------


def _checked_argv(argv) -> list[str]:
    if isinstance(argv, (str, bytes)) or not isinstance(argv, (list, tuple)):
        raise TypeError("argv must be a list of strings")
    if not argv:
        raise ValueError("argv must not be empty")
    if not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be a list of strings")
    return list(argv)


def _homes() -> set[Path]:
    homes = {Path.home().resolve()}
    try:
        import pwd

        homes.add(Path(pwd.getpwuid(os.getuid()).pw_dir).resolve())
    except (ImportError, KeyError):
        pass
    return homes


def _too_broad(path: Path) -> bool:
    return path == Path(path.anchor) or any(h == path or h.is_relative_to(path) for h in _homes())


def checked_read_path(p) -> str:
    """A path the candidate may read: it must exist, must not be /, the user's home, or a folder holding it, and
    must not be named like a guarded place or a database."""
    path = Path(p).resolve(strict=True)
    if _too_broad(path):
        raise ValueError(f"{p} is too broad to be readable by a candidate")
    if backends.is_refused_name(path.name):
        raise ValueError(f"{p} is a guarded place or a database and cannot be readable by a candidate")
    return str(path)


def _listable_parents(read_paths: tuple[str, ...]) -> tuple[str, ...]:
    """The folders holding the read paths, so a granted package can be imported from them (Python lists the folder
    on sys.path). Only names are listable there; never a home, a folder holding one, or /."""
    out = []
    for r in read_paths:
        parent = Path(r).parent
        if not _too_broad(parent) and str(parent) not in out and str(parent) not in read_paths:
            out.append(str(parent))
    return tuple(out)


def _not_copied(directory: str, names: list[str]) -> list[str]:
    out = []
    for n in names:
        if backends.is_not_copied_name(n):
            out.append(n)
            continue
        try:
            mode = os.lstat(os.path.join(directory, n)).st_mode
        except OSError:
            out.append(n)
            continue
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
            out.append(n)  # sockets, fifos, devices
    return out


def _unlock(path: Path) -> None:
    """Undo what a candidate can do to stop a delete: file flags (`chflags uchg`, `uappnd`) and folder permissions.
    Links are not followed. The candidate's processes are dead by now, so nothing changes under us."""
    chflags = getattr(os, "chflags", None)

    def fix(p: str) -> None:
        try:
            st = os.lstat(p)
        except OSError:
            return
        if chflags is not None and getattr(st, "st_flags", 0):
            try:
                chflags(p, 0, follow_symlinks=False)
            except (OSError, NotImplementedError):
                pass
        if stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) & 0o700 != 0o700:
            try:
                os.chmod(p, 0o700)
            except OSError:
                pass

    if not path.exists() and not path.is_symlink():
        return
    fix(str(path))
    for dirpath, dirnames, filenames in os.walk(path):
        for n in [*dirnames, *filenames]:
            fix(os.path.join(dirpath, n))
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]


def _disk_use(path: Path) -> int:
    """Bytes of disk the files under `path` take (allocated blocks, so a sparse file counts what it really uses)."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for n in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, n))
            except OSError:
                continue
            total += st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
    return total


def _process_cap(max_processes: int) -> int | None:
    """RLIMIT_NPROC counts every process (Linux: thread) of the user, so the cap for a run is what the user has now
    plus the run's share. None for root, which the kernel does not limit, or when the count cannot be taken."""
    if not hasattr(os, "getuid") or os.getuid() == 0:
        return None
    uid = os.getuid()
    count = 0
    if sys.platform.startswith("linux"):
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                with open(f"/proc/{name}/status") as f:
                    fields = dict(line.split(":", 1) for line in f if ":" in line)
                if int(fields["Uid"].split()[0]) == uid:
                    count += int(fields["Threads"].strip())
            except (OSError, KeyError, ValueError):
                continue
    else:
        try:
            out = subprocess.run(["ps", "-U", str(uid), "-o", "pid="], capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        count = sum(1 for line in out.stdout.splitlines() if line.strip())
    return count + max_processes if count else None


def _force_rmtree(path: Path) -> None:
    """Delete a tree the candidate may have made hard to delete (immutable flags, chmod 000 folders)."""

    def fix(func, p, _exc):
        try:
            os.chmod(os.path.dirname(p), 0o700)
            if os.path.isdir(p) and not os.path.islink(p):
                os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass

    _unlock(path)
    if path.is_symlink() or path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
        return
    shutil.rmtree(path, onexc=fix) if sys.version_info >= (3, 12) else shutil.rmtree(path, onerror=fix)


def _feed(pipe, data: bytes) -> None:
    try:
        pipe.write(data)
        pipe.close()
    except (BrokenPipeError, OSError, ValueError):
        pass


def _docker_quiet(*args: str) -> None:
    exe = shutil.which("docker")
    if exe:
        env = dict(os.environ, DOCKER_CONFIG=backends.docker_config_dir())
        try:
            subprocess.run([exe, *args], capture_output=True, timeout=30, check=False, env=env)
        except (OSError, subprocess.TimeoutExpired):
            pass


class _Capture:
    """Reads a pipe to its end in a thread, keeping the first OUTPUT_LIMIT bytes."""

    def __init__(self, pipe) -> None:
        self._pipe = pipe
        self._buf = bytearray()
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        try:
            while chunk := self._pipe.read1(65536) if hasattr(self._pipe, "read1") else self._pipe.read(65536):
                if len(self._buf) < OUTPUT_LIMIT:
                    self._buf += chunk[: OUTPUT_LIMIT - len(self._buf)]
        except (OSError, ValueError):
            pass

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)

    def text(self) -> str:
        return bytes(self._buf).decode("utf-8", errors="replace")


class _Watch:
    """Enforces the wall-time, memory, process-count and disk limits of one process tree, from a thread."""

    def __init__(self, sb: Sandbox, proc: subprocess.Popen, container: str | None) -> None:
        self.sb, self.proc, self.container = sb, proc, container
        self.timed_out = False
        self.limit: str | None = None
        self._deadline = time.monotonic() + sb.wall_seconds
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def join(self) -> None:
        self._thread.join(10)

    def _loop(self) -> None:
        cap_kb = self.sb.memory_mb * 1024
        disk_cap = self.sb.max_disk_mb * 1024 * 1024
        next_disk = time.monotonic()
        while self.proc.poll() is None:
            now = time.monotonic()
            if now >= self._deadline:
                self.timed_out, self.limit = True, "wall"
                break
            if self.container is None:  # Docker enforces memory and processes itself
                procs = _processes(self.sb)
                members = _members(procs, {self.proc.pid})
                if sum(procs[p].rss_kb for p in members) > cap_kb:
                    self.limit = "memory"
                    break
                if len(members) > self.sb.max_processes:
                    self.limit = "processes"
                    break
            if now >= next_disk:
                next_disk = now + DISK_POLL_S
                if _disk_use(self.sb.scratch) - self.sb._disk_base > disk_cap:
                    self.limit = "disk"
                    break
            time.sleep(POLL_S)
        else:
            return
        self.sb._kill_run(self.proc, self.container)


# --- finding every process of a run -------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Proc:
    pid: int
    ppid: int
    rss_kb: int
    marked: bool


def _processes(sb: Sandbox) -> dict[int, _Proc]:
    """Live processes: parent, resident memory, and whether they are known to belong to this sandbox.

    On macOS the kernel says which processes run under this sandbox's profile, so even an orphan that called setsid
    is found. On Linux the launcher is a child subreaper, so every process of a run stays in the tree under it.
    """
    if sys.platform.startswith("linux"):
        return _linux_processes()
    try:
        out = subprocess.run(["ps", "-U", str(os.getuid()), "-o", "pid=,ppid=,rss=,stat="], capture_output=True,
                             timeout=10, check=False).stdout.decode("ascii", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return {}
    procs = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or not parts[0].isdigit() or not parts[1].isdigit() or parts[3].startswith("Z"):
            continue
        pid = int(parts[0])
        rss = int(parts[2]) if parts[2].isdigit() else 0
        procs[pid] = _Proc(pid, int(parts[1]), rss, backends.in_darwin_sandbox(pid, sb._tag))
    return procs


def _linux_processes() -> dict[int, _Proc]:
    page_kb = os.sysconf("SC_PAGE_SIZE") // 1024
    procs = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as f:
                fields = f.read().rsplit(b")", 1)[1].split()
            if fields[0] == b"Z":
                continue
            procs[int(name)] = _Proc(int(name), int(fields[1]), int(fields[21]) * page_kb, False)
        except (OSError, IndexError, ValueError):
            continue
    return procs


def _members(procs: dict[int, _Proc], roots: set[int]) -> set[int]:
    """The marked processes, the roots, and everything descended from either."""
    found = {p for p in roots if p in procs} | {p.pid for p in procs.values() if p.marked}
    children: dict[int, list[int]] = {}
    for p in procs.values():
        children.setdefault(p.ppid, []).append(p.pid)
    todo = list(found)
    while todo:
        for c in children.get(todo.pop(), ()):
            if c not in found:
                found.add(c)
                todo.append(c)
    return found


def _kill_members(sb: Sandbox, roots: set[int]) -> None:
    """SIGKILL every process of the sandbox until none is left (new forks are caught by the next pass)."""
    me = os.getpid()
    for _ in range(50):
        alive = _members(_processes(sb), roots) - {me}
        if not alive:
            return
        for pid in alive:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        time.sleep(0.02)
