"""Sandbox: run code Tanishi wrote or changed so it can read nothing it was not given and write only its scratch.

    with Sandbox(worktree=repo, network=False, cpu_seconds=10, memory_mb=512, wall_seconds=30) as sb:
        result = sb.run([sys.executable, "-I", "candidate.py"])
        sb.rollback()  # the next run starts from a clean copy

- **Scratch.** Each sandbox owns a fresh 0700 folder: `.home` (HOME, with `.tanishi` as TANISHI_HOME, the Core State
  db path and `tmp` as TMPDIR) and `.workdir` (the cwd). With `worktree=` the workdir is a *copy* of that checkout
  without `.env`, `.env.*`, `vault/`, `builder_vault/`, `traces/` or `.ssh/` at any depth; the original is never
  mounted, so a candidate cannot read or change the real checkout while it runs.
- **Environment.** Built, not inherited: PATH, LANG, the variables above, the names in `env_passthrough`, and a
  marker the sandbox uses to find every process a run started.
- **Confinement.** Default-deny (see backends.py): the interpreter, system libraries and `read_paths` are readable,
  scratch is writable, nothing else is reachable. No network unless `network=True`.
- **Limits.** `cpu_seconds` (RLIMIT_CPU, which the candidate cannot raise), `memory_mb` (resident memory of every
  process of the run, polled; RLIMIT_AS is not enforced on macOS) and `wall_seconds`. All three are required to be
  finite and positive: "no limit" cannot be asked for.
- **Ending.** A run ends when its first process exits; anything it left running is killed then, found by the marker
  even if it called setsid. `rollback()` kills, wipes scratch and recreates it (and the worktree copy); leaving the
  `with` block kills and deletes scratch, even when the caller raised.
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

MARKER = "TANISHI_SANDBOX_RUN"
OUTPUT_LIMIT = 8 * 1024 * 1024  # bytes kept per stream; the rest is read and dropped so the candidate never blocks
POLL_S = 0.1
_LAUNCH = str(Path(__file__).with_name("_launch.py"))
_RESERVED = frozenset({"HOME", "TANISHI_HOME", "TANISHI_CORE_STATE_DB", "TMPDIR", MARKER})
_VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class SandboxResult:
    """What one run did. `limit` is "wall" or "memory" when the sandbox killed it for that, else None."""

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
                 read_paths=(), docker_image: str = backends.DOCKER_IMAGE) -> None:
        self.cpu_seconds = _positive("cpu_seconds", cpu_seconds)
        self.memory_mb = _positive("memory_mb", memory_mb)
        self.wall_seconds = _positive("wall_seconds", wall_seconds)
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
        self.read_paths = tuple(_checked_read_path(p) for p in read_paths)
        self.docker_image = docker_image
        self.backend = backends.pick_backend(prefer_docker, self.network)
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
        """Kill everything the candidate started and delete scratch."""
        if self.scratch is None:
            return
        try:
            self._kill_all()
        finally:
            _force_rmtree(self.scratch)
            _force_rmtree(self._tagdir)

    def rollback(self) -> None:
        """Undo every run: kill its processes, wipe scratch, recreate HOME and the working copy from the original."""
        self._check_open()
        self._kill_all()
        for child in list(self.scratch.iterdir()):
            _force_rmtree(child)
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
        env[MARKER] = self._token
        return env

    def popen(self, argv, *, cwd: str | os.PathLike | None = None, stdin=None, stdout=None, stderr=None,
              pass_fds=()) -> subprocess.Popen:
        """Start a confined process (its own session) under the wall and memory watchdog; the caller waits for it.
        `cwd` must lie inside scratch (default: the working copy)."""
        self._check_open()
        argv = _checked_argv(argv)
        where = Path(cwd).resolve() if cwd is not None else self.workdir
        if not where.is_relative_to(self.scratch):
            raise ValueError("cwd must be inside the sandbox's scratch folder")
        env = self.env()
        cpu = max(1, math.ceil(self.cpu_seconds))
        container = None
        if self.backend == "docker":
            container = f"tanishi-sb-{self._token[:12]}-{len(self._containers)}"
            names = [k for k in env if k != "PATH"]
            cmd = backends.docker_argv(name=container, image=self.docker_image, scratch=str(self.scratch),
                                       cwd=str(where), env_names=names, read_paths=list(self.read_paths),
                                       network=self.network, cpu_seconds=cpu,
                                       memory_mb=max(6, math.ceil(self.memory_mb)), argv=argv)
            env = {**os.environ, **env, "DOCKER_CONFIG": backends.docker_config_dir()}
            self._containers.append(container)
        else:
            cfg: dict = {"cpu": cpu, "landlock": None}
            if self.backend == "sandbox-exec":
                profile = backends.darwin_profile(str(self.scratch), list(self.read_paths), self.network, self._tag)
                argv = [backends.SANDBOX_EXEC, "-p", profile, *argv]
            else:
                cfg["landlock"] = {"rules": backends.landlock_rules(str(self.scratch), list(self.read_paths)),
                                   "deny_net": not self.network}
            cmd = [sys.executable, "-I", "-S", _LAUNCH, json.dumps(cfg), *argv]
        proc = subprocess.Popen(cmd, cwd=where, env=env, stdin=stdin, stdout=stdout, stderr=stderr,
                                pass_fds=tuple(pass_fds), start_new_session=True)
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


# --- helpers ------------------------------------------------------------------------------------------------------


def _checked_argv(argv) -> list[str]:
    if isinstance(argv, (str, bytes)) or not isinstance(argv, (list, tuple)):
        raise TypeError("argv must be a list of strings")
    if not argv:
        raise ValueError("argv must not be empty")
    if not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be a list of strings")
    return list(argv)


def _checked_read_path(p) -> str:
    """A path the candidate may read: it must exist, and must not be /, the user's home, or a folder holding it."""
    path = Path(p).resolve(strict=True)
    homes = {Path.home().resolve()}
    try:
        import pwd

        homes.add(Path(pwd.getpwuid(os.getuid()).pw_dir).resolve())
    except (ImportError, KeyError):
        pass
    if path == Path(path.anchor) or any(h == path or h.is_relative_to(path) for h in homes):
        raise ValueError(f"{p} is too broad to be readable by a candidate")
    if backends.is_guarded_name(path.name):
        raise ValueError(f"{p} is a guarded place and cannot be readable by a candidate")
    return str(path)


def _not_copied(directory: str, names: list[str]) -> list[str]:
    out = []
    for n in names:
        if backends.is_guarded_name(n):
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


def _force_rmtree(path: Path) -> None:
    """Delete a tree the candidate may have made hard to delete (chmod 000 folders)."""

    def fix(func, p, _exc):
        try:
            os.chmod(os.path.dirname(p), 0o700)
            if os.path.isdir(p) and not os.path.islink(p):
                os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass

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
    """Enforces the wall-time and memory limits of one process tree, from a thread."""

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
        while self.proc.poll() is None:
            if time.monotonic() >= self._deadline:
                self.timed_out, self.limit = True, "wall"
                break
            if self.container is None:
                procs = _processes(self.sb)
                if sum(procs[p].rss_kb for p in _members(procs, {self.proc.pid})) > cap_kb:
                    self.limit = "memory"
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
    """Live processes: parent, resident memory, and whether they belong to the sandbox with this token.

    On macOS the kernel says which processes run under this sandbox's profile (the environment of a platform binary
    such as /bin/sleep is hidden from ps). On Linux the marker in /proc/<pid>/environ is used.
    """
    if sys.platform.startswith("linux"):
        return _linux_processes(f"{MARKER}={sb._token}")
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


def _linux_processes(needle: str) -> dict[int, _Proc]:
    page_kb = os.sysconf("SC_PAGE_SIZE") // 1024
    marker = needle.encode()
    procs = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as f:
                fields = f.read().rsplit(b")", 1)[1].split()
            if fields[0] == b"Z":
                continue
            try:
                with open(f"/proc/{name}/environ", "rb") as f:
                    marked = marker in f.read().split(b"\0")
            except OSError:
                marked = False
            procs[int(name)] = _Proc(int(name), int(fields[1]), int(fields[21]) * page_kb, marked)
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
