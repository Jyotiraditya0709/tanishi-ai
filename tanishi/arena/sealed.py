"""The Sealed Vault (AR4): the hidden tasks that measure the North Star honestly. Nobody who learns ever reads them.

Two sides, one module, so both are tested in CI. Nothing secret is in this file: the secrets are the vault's files.

**The vault side** runs only as the OS user `tanishi-vault`, from a pinned copy of `tanishi` installed in that user's
own venv (the human updates it; a change merged to main does nothing there until then). Its home is 0700, so no
process of the agent's user can read it. It holds:

    ~/sealed/tasks/*.yaml   the store: AR1 task files in the Frontier store format (decision 0022), with .salt, .queries
    ~/sealed/checks.py      CHECKS = {task_id: verifier}; every task's `verifier` is `tanishi.arena.sealed.check`
    ~/sealed/reads.jsonl    the read ledger: every read and every refusal, one JSON line each
    ~/sealed/receipt.key    32 random bytes; HMAC key for read receipts
    ~/staging/<read_id>/    a candidate's copy while it is scored, deleted afterwards

The agent's user reaches it through exactly one sudo rule, for the root-owned wrapper
`/usr/local/libexec/tanishi-vault/vault-run`, which sets
this module's environment itself (sudo drops the caller's) and runs `python -I -m tanishi.arena.sealed <command>`.
Commands: `evaluate` (candidate tarball on stdin), `manifest`, `status`, `verify` (a result on stdin).

A read is refused (exit 3, nothing run) when a canary leak is flagged (`/Users/Shared/tanishi-vault/LEAK`, made by the
nightly scan as root and cleared only by the human), when this exact candidate (content hash) was read before, or when
today's read budget is spent. The charge is written before the run, so a crash still counts: no retrying into an
oracle. A store with code tasks is refused (exit 4) where no isolating sandbox exists for model-written code
(decision 0025): never scored on an unconfined run.

Scores cross back exactly as the Frontier CLI sends them: `{task_id_hash, score, cost}` per task, nothing else. The
vault adds a read id, the candidate's content hash and an HMAC receipt, writes the same JSON to
`/Users/Shared/tanishi-vault/reads/<read_id>.json` (the shared folder) and prints it.

**The agent side** is `SealedVault`: it packs a candidate checkout (its `tanishi/` package and `pyproject.toml`, no
`.git`, databases, env files or guarded names), pipes it to the wrapper, and returns a checked `SealedResult`.
It raises `SealedRefused` or `SealedUnavailable`; a caller that gates on sealed scores must treat both as "no pass".
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import importlib.util
import io
import json
import math
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

VAULT_USER = "tanishi-vault"
WRAPPER = "/usr/local/libexec/tanishi-vault/vault-run"  # root-owned: neither user can change it
DEFAULT_COMMAND: tuple[str, ...] = ("sudo", "-n", "-u", VAULT_USER, WRAPPER)
DEFAULT_SHARED = "/Users/Shared/tanishi-vault"
CHECK_VERIFIER = "tanishi.arena.sealed.check"

DAILY_READS = 6  # reads per UTC day, all candidates together; the wrapper may lower it, never raise it past MAX
MAX_DAILY_READS = 20
MAX_SEEDS = 3
MAX_CANDIDATE_BYTES = 64 * 1024 * 1024  # uncompressed, the whole candidate
MAX_CANDIDATE_FILES = 20_000
MAX_TARBALL_BYTES = 32 * 1024 * 1024  # compressed, what crosses the pipe
MAX_REPLY = 1 << 20

EXIT_OK, EXIT_ERROR, EXIT_REFUSED, EXIT_UNAVAILABLE = 0, 2, 3, 4
_HASH = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_READ_ID = re.compile(r"[0-9a-f]{32}")
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+")
EXECUTOR_PREFIXES = ("tanishi.",)  # the vault resolves executors only from its own pinned tanishi
_CAP_TAG = "cap:"
_REFUSALS = frozenset({"leak", "already_read", "daily_cap"})
_UNAVAILABLE = frozenset({"no_isolating_sandbox", "not_installed"})
_SKIP_DIRS = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules"})


class SealedUnavailable(RuntimeError):
    """The vault cannot score right now: not installed, sudo refused, no isolating sandbox, or it failed."""


class SealedRefused(RuntimeError):
    """The vault refused this read: a flagged leak, a candidate read before, or today's budget spent.

    `.reason` is one of "leak", "already_read", "daily_cap"."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"the Sealed Vault refused this read ({reason})")
        self.reason = reason


@dataclass(frozen=True)
class TaskScore:
    task_id_hash: str
    score: float
    cost: float


@dataclass(frozen=True)
class SealedResult:
    read_id: str
    ts: str
    candidate_hash: str
    executor: str
    seeds: int
    scores: tuple[TaskScore, ...]
    receipt: str

    @property
    def mean(self) -> float:
        """Mean score over the sealed tasks. Raises on an empty result: an empty vault must never read as a pass."""
        if not self.scores:
            raise ValueError("the Sealed Vault returned no scores")
        return sum(s.score for s in self.scores) / len(self.scores)

    def as_dict(self) -> dict:
        return {
            "read_id": self.read_id, "ts": self.ts, "candidate_hash": self.candidate_hash, "executor": self.executor,
            "seeds": self.seeds, "scores": [vars(s) for s in self.scores], "receipt": self.receipt,
        }


# --- the agent side ----------------------------------------------------------------------------------------------


class SealedVault:
    """The agent side of the vault. `command` is the argv prefix that reaches the vault (sudo + wrapper by default)."""

    def __init__(self, command: Sequence[str] = DEFAULT_COMMAND, *, timeout_s: float = 4 * 3600) -> None:
        if isinstance(command, (str, bytes)) or not command or not all(isinstance(c, str) for c in command):
            raise TypeError("command must be a non-empty list of strings")
        self._command = tuple(command)
        self._timeout_s = timeout_s

    def __repr__(self) -> str:
        return "SealedVault()"

    def evaluate(self, candidate_dir: str | os.PathLike, *, executor: str, seeds: int = 1) -> SealedResult:
        """Score the checkout at `candidate_dir` on every sealed task. One read per candidate, ever."""
        if not isinstance(executor, str) or not _DOTTED.fullmatch(executor):
            raise ValueError("executor must be a dotted path module.function")
        _check_seeds(seeds)
        tarball = pack_candidate(candidate_dir)
        out = self._call(["evaluate", "--executor", executor, "--seeds", str(seeds)], tarball)
        return _parse_result(out, executor, seeds)

    def manifest(self) -> dict[str, str]:
        """{task_id_hash: capability id} for every sealed task (E3 uses it for sealed instances)."""
        data = self._call(["manifest"], b"")
        if not isinstance(data, dict) or not all(
                isinstance(k, str) and _HASH.fullmatch(k) and isinstance(v, str) and 0 < len(v) <= 100
                for k, v in data.items()):
            raise SealedUnavailable("the Sealed Vault sent a malformed manifest")
        return dict(data)

    def status(self) -> dict:
        data = self._call(["status"], b"")
        if not isinstance(data, dict):
            raise SealedUnavailable("the Sealed Vault sent a malformed status")
        return data

    def verify(self, result: SealedResult) -> bool:
        """True if the vault recognises `result` as one it issued, unchanged."""
        data = self._call(["verify"], json.dumps(result.as_dict()).encode())
        return isinstance(data, dict) and data.get("valid") is True

    def _call(self, args: list[str], stdin: bytes) -> object:
        try:
            done = subprocess.run([*self._command, *args], input=stdin, capture_output=True, timeout=self._timeout_s,
                                  check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise SealedUnavailable(f"the Sealed Vault could not be reached ({type(e).__name__})") from None
        reply = _json_reply(done.stdout)
        if done.returncode == EXIT_REFUSED and isinstance(reply, dict) and reply.get("refused") in _REFUSALS:
            raise SealedRefused(reply["refused"])
        if done.returncode == EXIT_UNAVAILABLE and isinstance(reply, dict) and reply.get("unavailable") in _UNAVAILABLE:
            raise SealedUnavailable(f"the Sealed Vault cannot score right now ({reply['unavailable']})")
        if done.returncode != EXIT_OK or reply is None:
            raise SealedUnavailable(f"the Sealed Vault did not answer (exit {done.returncode})")
        return reply


def _json_reply(raw: bytes) -> object | None:
    if len(raw) > MAX_REPLY:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _check_seeds(seeds: object) -> None:
    if isinstance(seeds, bool) or not isinstance(seeds, int):
        raise TypeError("seeds must be an int")
    if not 1 <= seeds <= MAX_SEEDS:
        raise ValueError(f"seeds must be between 1 and {MAX_SEEDS}")


def _parse_result(data: object, executor: str, seeds: int) -> SealedResult:
    bad = SealedUnavailable("the Sealed Vault sent a malformed result")
    if not isinstance(data, dict):
        raise bad
    read_id, ts, chash, receipt = data.get("read_id"), data.get("ts"), data.get("candidate_hash"), data.get("receipt")
    if not (isinstance(read_id, str) and _READ_ID.fullmatch(read_id) and isinstance(chash, str)
            and _HEX64.fullmatch(chash) and isinstance(receipt, str) and _HEX64.fullmatch(receipt)
            and isinstance(ts, str) and len(ts) <= 40):
        raise bad
    if data.get("executor") != executor or data.get("seeds") != seeds:
        raise bad
    raw = data.get("scores")
    if not isinstance(raw, list) or not raw:
        raise bad
    scores, seen = [], set()
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {"task_id_hash", "score", "cost"}:
            raise bad
        h, score, cost = entry["task_id_hash"], entry["score"], entry["cost"]
        if not isinstance(h, str) or not _HASH.fullmatch(h) or h in seen:
            raise bad
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not (
                math.isfinite(score) and 0.0 <= score <= 1.0):
            raise bad
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not (math.isfinite(cost) and cost >= 0):
            raise bad
        seen.add(h)
        scores.append(TaskScore(h, float(score), float(cost)))
    return SealedResult(read_id, ts, chash, executor, seeds, tuple(sorted(scores, key=lambda s: s.task_id_hash)),
                        receipt)


def _skipped(name: str) -> bool:
    """Left out of a candidate: the sandbox's own list (guarded names, databases, .git, .env*), caches, bytecode."""
    from tanishi.sandbox.backends import is_not_copied_name

    return is_not_copied_name(name) or name in _SKIP_DIRS or name.lower().endswith((".pyc", ".pyo"))


def pack_candidate(candidate_dir: str | os.PathLike) -> bytes:
    """A gzip tarball of the candidate: `tanishi/` (regular files only) and `pyproject.toml`. Nothing else leaves.

    Symlinks, guarded names, databases, `.git` and `.env*` are left out. Raises ValueError if there is no `tanishi/`
    package or the candidate is larger than the vault accepts."""
    root = Path(candidate_dir).resolve(strict=True)
    pkg = root / "tanishi"
    if pkg.is_symlink() or not pkg.is_dir() or not (pkg / "__init__.py").is_file():
        raise ValueError("a candidate is a checkout with a tanishi/ package")
    files: list[tuple[str, Path]] = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and not pyproject.is_symlink():
        files.append(("pyproject.toml", pyproject))
    for dirpath, dirnames, filenames in os.walk(pkg, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not _skipped(d) and not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if _skipped(name) or path.is_symlink() or not path.is_file():
                continue
            files.append((path.relative_to(root).as_posix(), path))
    total = 0
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        for count, (name, path) in enumerate(files, 1):
            data = path.read_bytes()
            total += len(data)
            if count > MAX_CANDIDATE_FILES or total > MAX_CANDIDATE_BYTES:
                raise ValueError("the candidate is larger than the Sealed Vault accepts")
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o600, 0
            tar.addfile(info, io.BytesIO(data))
    out = buf.getvalue()
    if len(out) > MAX_TARBALL_BYTES:
        raise ValueError("the candidate is larger than the Sealed Vault accepts")
    return out


# --- the verifier every sealed task names -------------------------------------------------------------------------

_CHECKS: tuple[str, ModuleType] | None = None


def checks_path() -> Path:
    """Where the vault's checks live: $TANISHI_SEALED_CHECKS (set by the vault itself), else ~vault/sealed/checks.py
    of the user this process really runs as (not $HOME, which an attempt sets to its scratch)."""
    env = os.environ.get("TANISHI_SEALED_CHECKS")
    if env:
        return Path(env)
    return Path(pwd.getpwuid(os.geteuid()).pw_dir) / "sealed" / "checks.py"


def load_checks(path: Path | None = None) -> ModuleType:
    """Import the vault's checks file by path (never via sys.path). It must be a private regular file of this user."""
    global _CHECKS
    path = checks_path() if path is None else path
    key = str(path)
    if _CHECKS is not None and _CHECKS[0] == key:
        return _CHECKS[1]
    _private_file(path, "the sealed checks file")
    spec = importlib.util.spec_from_file_location("tanishi_sealed_checks", path)
    if spec is None or spec.loader is None:
        raise ImportError("the sealed checks file cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not isinstance(getattr(module, "CHECKS", None), dict):
        raise TypeError("the sealed checks file has no CHECKS dict")
    _CHECKS = (key, module)
    return module


def check(task, output):
    """The verifier of every sealed task: the vault's own check for `task.id`. Runs in the verifier stage only."""
    fn = load_checks().CHECKS[task.id]
    return fn(task, output)


def _private_file(path: Path, what: str) -> None:
    st = path.lstat()  # FileNotFoundError if missing
    if not stat.S_ISREG(st.st_mode):
        raise PermissionError(f"{what} must be a regular file, not a link")
    if st.st_uid != os.geteuid():
        raise PermissionError(f"{what} must be owned by the user the vault runs as")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise PermissionError(f"{what} must give no access to group or other")


# --- the vault side ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    home: Path
    shared: Path

    @classmethod
    def from_env(cls) -> Layout:
        home = os.environ.get("TANISHI_SEALED_HOME") or pwd.getpwuid(os.geteuid()).pw_dir
        return cls(Path(home), Path(os.environ.get("TANISHI_SEALED_SHARED") or DEFAULT_SHARED))

    @property
    def store(self) -> Path:
        return self.home / "sealed" / "tasks"

    @property
    def checks(self) -> Path:
        return self.home / "sealed" / "checks.py"

    @property
    def ledger(self) -> Path:
        return self.home / "sealed" / "reads.jsonl"

    @property
    def receipt_key(self) -> Path:
        return self.home / "sealed" / "receipt.key"

    @property
    def staging(self) -> Path:
        return self.home / "staging"

    @property
    def leak_marker(self) -> Path:
        return self.shared / "LEAK"

    @property
    def reads_out(self) -> Path:
        return self.shared / "reads"


class _Stop(Exception):
    def __init__(self, code: int, reply: dict) -> None:
        super().__init__(json.dumps(reply))
        self.code, self.reply = code, reply


def _refuse(reason: str) -> _Stop:
    return _Stop(EXIT_REFUSED, {"refused": reason})


def _unavailable(reason: str) -> _Stop:
    return _Stop(EXIT_UNAVAILABLE, {"unavailable": reason})


def _daily_reads() -> int:
    raw = os.environ.get("TANISHI_SEALED_DAILY_READS", "")
    if raw.isdigit():
        return max(1, min(int(raw), MAX_DAILY_READS))
    return DAILY_READS


def _executor_prefixes() -> tuple[str, ...]:
    """Module prefixes the vault resolves executors from. The wrapper sets the environment (sudo drops the caller's),
    so only the human widens this, e.g. for the vault's own tests."""
    raw = os.environ.get("TANISHI_SEALED_EXECUTOR_PREFIXES", "")
    extra = tuple(p for p in raw.split(",") if p and p.endswith("."))
    return EXECUTOR_PREFIXES + extra


def _passthrough() -> tuple[str, ...]:
    names = [n for n in os.environ.get("TANISHI_SEALED_PASSTHROUGH", "").split(",") if n]
    return tuple(names)


def _load_store(layout: Layout) -> tuple[Path, list, ModuleType]:
    """The store, its tasks and the checks, or _Stop. Nothing about a task's content goes into any message."""
    from tanishi.arena.frontier_service import (
        _checked_store,
        _load_all,
        _verifiers_import,
    )

    try:
        store = _checked_store(layout.store)
    except FileNotFoundError:
        raise _unavailable("not_installed") from None
    tasks = _load_all(store)
    if tasks is None or len({t.id for t in tasks}) != len(tasks) or not tasks:
        raise _Stop(EXIT_ERROR, {"error": "the sealed store is broken or empty"})
    try:
        checks = load_checks(layout.checks)
    except FileNotFoundError:
        raise _unavailable("not_installed") from None
    except Exception:  # noqa: BLE001 - the error could quote the file
        raise _Stop(EXIT_ERROR, {"error": "the sealed checks file is broken"}) from None
    if any(t.id not in checks.CHECKS or not callable(checks.CHECKS[t.id]) for t in tasks) \
            or not _verifiers_import(tasks):
        raise _Stop(EXIT_ERROR, {"error": "a sealed task has no check"})
    return store, tasks, checks


def isolating_sandbox_available() -> bool:
    """True if model-written code can run in an isolating sandbox here (decision 0025: Docker or Landlock)."""
    from tanishi.sandbox import Sandbox, SandboxUnavailable

    try:
        with Sandbox(trusted=False):
            return True
    except (SandboxUnavailable, OSError):
        return False


def _private_key(path: Path) -> bytes:
    """32 random bytes, created 0600 on first use (link-then-read, so concurrent first calls agree)."""
    try:
        _private_file(path, "the receipt key")
        data = path.read_bytes()
        if len(data) >= 32:
            return data
    except FileNotFoundError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(os.urandom(32))
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
    finally:
        os.unlink(tmp)
    _private_file(path, "the receipt key")
    data = path.read_bytes()
    if len(data) < 32:
        raise PermissionError("the receipt key is too short; the human must replace it")
    return data


def _receipt(key: bytes, result: dict) -> str:
    body = {k: result[k] for k in ("read_id", "ts", "candidate_hash", "executor", "seeds", "scores")}
    msg = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _ledger_append(path: Path, entry: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(entry, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _charge(path: Path, read_id: str, candidate_hash: str, executor: str, seeds: int) -> None:
    """Record this read before it runs, or record a refusal and raise. One read per candidate hash, ever, and at most
    the daily budget across all candidates (a new candidate string or a whitespace change still spends the day)."""
    now = datetime.now(UTC)
    today = now.date().isoformat()
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.seek(0)
        used, seen = 0, False
        for line in f.read().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict) or e.get("outcome") != "ran":
                continue
            used += e.get("day") == today
            seen = seen or e.get("candidate_hash") == candidate_hash
        why = "already_read" if seen else "daily_cap" if used >= _daily_reads() else None
        entry = {"read_id": read_id, "ts": now.isoformat(), "day": today, "candidate_hash": candidate_hash,
                 "executor": executor, "seeds": seeds, "outcome": "refused" if why else "ran"}
        if why:
            entry["why"] = why
        f.write(json.dumps(entry, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    if why:
        raise _refuse(why)


def _safe_name(name: str) -> str | None:
    """The member name if it may be written into staging: relative, no `..`, under tanishi/ or pyproject.toml."""
    from tanishi.sandbox.backends import is_not_copied_name

    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        return None
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    if name != "pyproject.toml" and parts[0] != "tanishi":
        return None
    if any(is_not_copied_name(p) for p in parts):
        return None
    return name


def extract_candidate(data: bytes, dest: Path) -> str:
    """Write the candidate tarball's regular files into `dest`; return the candidate's content hash.

    Anything that is not a regular file or a folder, any name outside tanishi/ or pyproject.toml, an absolute name,
    `..`, a guarded name, or more than the size caps refuses the whole candidate (ValueError)."""
    if len(data) > MAX_TARBALL_BYTES:
        raise ValueError("the candidate tarball is too large")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            return _extract_members(tar, dest)
    except (tarfile.TarError, EOFError, zlib.error):
        raise ValueError("the candidate is not a gzip tarball") from None


def _extract_members(tar: tarfile.TarFile, dest: Path) -> str:
    digests: list[tuple[str, str]] = []
    total = 0
    members = tar.getmembers()
    if len(members) > MAX_CANDIDATE_FILES * 2:
        raise ValueError("the candidate has too many entries")
    for m in members:
        name = _safe_name(m.name)
        if name is None or not (m.isfile() or m.isdir()):
            raise ValueError("the candidate holds an entry the vault does not accept")
        if m.isdir():
            continue
        total += m.size
        if total > MAX_CANDIDATE_BYTES or len(digests) >= MAX_CANDIDATE_FILES:
            raise ValueError("the candidate is larger than the vault accepts")
        src = tar.extractfile(m)
        content = src.read(m.size + 1) if src is not None else b""
        if len(content) != m.size:
            raise ValueError("a candidate file is not the size it says")
        target = dest / name
        try:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except (FileExistsError, NotADirectoryError):
            raise ValueError("the candidate names one file twice") from None
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        digests.append((name, hashlib.sha256(content).hexdigest()))
    if not any(n.startswith("tanishi/") for n, _ in digests):
        raise ValueError("the candidate has no tanishi/ package")
    return hashlib.sha256(json.dumps(sorted(digests), separators=(",", ":")).encode()).hexdigest()


def _write_shared(layout: Layout, result: dict) -> None:
    """Copy the result into the shared folder (world-readable), if the folder exists. Atomic, never overwrites."""
    out = layout.reads_out
    if not out.is_dir():
        return
    fd, tmp = tempfile.mkstemp(prefix=".read-", dir=out)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(result, f, sort_keys=True)
        os.chmod(tmp, 0o644)
        os.link(tmp, out / f"{result['read_id']}.json")
    finally:
        os.unlink(tmp)


def serve_evaluate(stdin: bytes, executor: str, seeds: int, layout: Layout | None = None) -> dict:
    """The vault's `evaluate`: check everything, charge the read, score the candidate, return the result. _Stop on
    refusal or error."""
    from tanishi.arena.frontier_service import _evaluate_in_child, _salt
    from tanishi.arena.verifiers import resolve

    layout = Layout.from_env() if layout is None else layout
    if layout.leak_marker.exists():
        raise _refuse("leak")
    if not _DOTTED.fullmatch(executor) or not executor.startswith(_executor_prefixes()):
        raise _Stop(EXIT_ERROR, {"error": "executor must be a dotted path inside the tanishi package"})
    _check_seeds(seeds)
    store, tasks, _ = _load_store(layout)
    if any("code" in t.tags for t in tasks) and not isolating_sandbox_available():
        raise _unavailable("no_isolating_sandbox")
    try:
        resolve(executor)
    except Exception:  # noqa: BLE001
        raise _Stop(EXIT_ERROR, {"error": "the executor cannot be imported in the vault"}) from None
    layout.staging.mkdir(mode=0o700, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="cand-", dir=layout.staging))
    try:
        try:
            chash = extract_candidate(stdin, staging)
        except ValueError as e:
            raise _Stop(EXIT_ERROR, {"error": str(e)}) from None
        read_id = uuid.uuid4().hex
        key = _salt(store)
        receipt_key = _private_key(layout.receipt_key)
        _charge(layout.ledger, read_id, chash, executor, seeds)
        os.environ["TANISHI_SEALED_CHECKS"] = str(layout.checks)  # the verifier stage finds the checks by this
        passthrough = tuple(dict.fromkeys([*_passthrough(), "TANISHI_SEALED_CHECKS"]))
        try:
            scores = _evaluate_in_child(store, tasks, key, str(staging), seeds, executor, passthrough)
        except Exception as e:  # noqa: BLE001 - the read stays charged
            _ledger_append(layout.ledger, {"read_id": read_id, "outcome": "failed", "error": type(e).__name__})
            raise _Stop(EXIT_ERROR, {"error": "the sealed evaluation failed"}) from None
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    result = {"read_id": read_id, "ts": datetime.now(UTC).isoformat(), "candidate_hash": chash, "executor": executor,
              "seeds": seeds, "scores": scores}
    result["receipt"] = _receipt(receipt_key, result)
    mean = sum(s["score"] for s in scores) / len(scores)
    _ledger_append(layout.ledger, {"read_id": read_id, "outcome": "scored", "tasks": len(scores),
                                   "mean": round(mean, 6), "receipt": result["receipt"]})
    _write_shared(layout, result)
    return result


def serve_manifest(layout: Layout | None = None) -> dict:
    from tanishi.arena.frontier_service import _hash, _salt

    layout = Layout.from_env() if layout is None else layout
    store, tasks, _ = _load_store(layout)
    key = _salt(store)
    out = {}
    for t in tasks:
        caps = [tag[len(_CAP_TAG):] for tag in t.tags if tag.startswith(_CAP_TAG)]
        out[_hash(key, t.id)] = caps[0] if len(caps) == 1 else t.family
    return dict(sorted(out.items()))


def serve_status(layout: Layout | None = None) -> dict:
    layout = Layout.from_env() if layout is None else layout
    today = datetime.now(UTC).date().isoformat()
    try:
        _, tasks, _ = _load_store(layout)
        n_tasks: int | None = len(tasks)
        code = any("code" in t.tags for t in tasks)
    except _Stop:
        n_tasks, code = None, False
    used = 0
    try:
        for line in layout.ledger.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            used += isinstance(e, dict) and e.get("outcome") == "ran" and e.get("day") == today
    except FileNotFoundError:
        pass
    return {"installed": n_tasks is not None, "tasks": n_tasks, "leak": layout.leak_marker.exists(),
            "reads_today": used, "daily_reads": _daily_reads(),
            "isolating_sandbox": isolating_sandbox_available() if code else None}


def serve_verify(stdin: bytes, layout: Layout | None = None) -> dict:
    layout = Layout.from_env() if layout is None else layout
    data = _json_reply(stdin)
    try:
        key = layout.receipt_key.read_bytes()
        _private_file(layout.receipt_key, "the receipt key")
        claimed = data["receipt"]
        ok = isinstance(claimed, str) and hmac.compare_digest(_receipt(key, data), claimed)
    except (OSError, KeyError, TypeError, ValueError, PermissionError):
        ok = False
    return {"valid": bool(ok)}


def main(argv: list[str] | None = None) -> int:
    """The vault's CLI. Run only as the vault user, through the wrapper. Prints one JSON object."""
    parser = argparse.ArgumentParser(prog="python -I -m tanishi.arena.sealed")
    sub = parser.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--executor", required=True)
    ev.add_argument("--seeds", type=int, default=1)
    sub.add_parser("manifest")
    sub.add_parser("status")
    sub.add_parser("verify")
    args = parser.parse_args(argv)
    code, reply = EXIT_OK, {}
    try:
        if args.command == "evaluate":
            reply = serve_evaluate(sys.stdin.buffer.read(MAX_TARBALL_BYTES + 1), args.executor, args.seeds)
        elif args.command == "manifest":
            reply = serve_manifest()
        elif args.command == "status":
            reply = serve_status()
        else:
            reply = serve_verify(sys.stdin.buffer.read(MAX_REPLY + 1))
    except _Stop as stop:
        code, reply = stop.code, stop.reply
    except (TypeError, ValueError) as e:
        code, reply = EXIT_ERROR, {"error": type(e).__name__}
    except Exception as e:  # noqa: BLE001 - only the class name: an error may quote a task
        code, reply = EXIT_ERROR, {"error": type(e).__name__}
    sys.stdout.write(json.dumps(reply, sort_keys=True, allow_nan=False) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "CHECK_VERIFIER", "DAILY_READS", "MAX_SEEDS", "SealedRefused", "SealedResult", "SealedUnavailable", "SealedVault",
    "TaskScore", "check", "extract_candidate", "load_checks", "main", "pack_candidate",
]
