"""The Frontier tier: run hidden tasks against a candidate and hand back scores, never the tasks.

`FrontierService(store_dir, executor=...).evaluate(candidate, seeds=1)` returns one dict per task with exactly
`{task_id_hash, score, cost}`. Nothing else crosses the boundary:

- no prompt, task id, verifier path, verifier reason, exception text or candidate output in the return value,
  in an error message, on stdout/stderr or in a log record;
- no row in the caller's Core State. AR1's `run()` writes `task_id` and the verifier's `reason` into `experiments`,
  and a Frontier verifier's reason may carry its answer, so the service points `run()` at a throwaway db in a
  private temp dir and deletes it afterwards. The caller records the scores it was given, if it wants them.
- `cost` is the executor's own report, so it is bounded before it leaves: each attempt's cost must be a finite,
  non-negative number (anything else counts as 0), it is rounded to cents and capped at MAX_COST_PER_TASK, and so is
  the task's sum over seeds. A candidate can no longer hide bytes of the prompt in it at full float precision (RT-1).

The store is a directory of AR1 task files named `*.yaml`, plus files the service keeps for itself: `.salt` (the
hash key), `.queries` (the query ledger) and, briefly, `.salt-*.tmp`. Any other entry (a `.yml` file, a
subdirectory, a stray file) refuses the whole store, so no task can silently drop out of the score.
`task_id_hash` is HMAC-SHA256 of the task id keyed by the salt, so it is stable for one store but a caller cannot
confirm a guessed id by hashing it. The store is refused unless it, its task files, its salt and its ledger are
owned by the user the service runs as and give no access to group or other, it holds no symlinked task file, and
it lies outside this checkout (the agent reads its own checkout). Every task's verifier must import, or the store is
refused as broken: a broken task must not look like a candidate that scored 0.

Each candidate gets DAILY_QUERY_BUDGET evaluations per UTC day per store; past that, `evaluate` raises
QueryBudgetExceeded and the refusal is written to the ledger. `seeds` is capped at MAX_SEEDS.
An empty store returns `[]`: a gate that uses Frontier scores must check the list is non-empty.

"A path the agent OS user cannot read" also needs the service to run as a different OS user than the agent: mode
bits only protect the store from users other than its owner. Run it across that boundary with the CLI, e.g.
`sudo -u tanishi-frontier python -P -m tanishi.arena.frontier_service STORE CANDIDATE --executor module.function`.
The CLI never imports the executor module itself: a child process with a minimal environment runs the evaluation
and sends back only hashes and numbers, which the CLI checks against the store before printing them as JSON (RT-4).
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
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
from datetime import UTC, datetime
from pathlib import Path

from tanishi.arena.runner import Executor, run
from tanishi.arena.task import load_task
from tanishi.arena.verifiers import resolve
from tanishi.core_state.db import ENV_VAR

# Limits of the service. They live here, not in tanishi/config/, which autoresearch may rewrite.
MAX_SEEDS = 10
DAILY_QUERY_BUDGET = 20  # evaluate() calls per candidate, per store, per UTC day
MAX_COST_PER_TASK = 20.0  # cap on one attempt's cost and on a task's sum over seeds

_CHECKOUT = Path(__file__).resolve().parents[2]
_SALT = ".salt"
_SALT_BYTES = 32
_SALT_TMP = re.compile(r"\.salt-[A-Za-z0-9_]+\.tmp")
_LEDGER = ".queries"
_HASH = re.compile(r"[0-9a-f]{32}")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,99}")
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+")
_CHILD_ENV = ("PATH", "LANG", "LC_ALL", "HOME", "TMPDIR")
_CHILD_OUTPUT_LIMIT = 1 << 20
# run() reads the Core State path from the environment, so the swap is serialised (RT-7).
_ENV_LOCK = threading.Lock()


class QueryBudgetExceeded(RuntimeError):
    """A candidate has used its DAILY_QUERY_BUDGET evaluations on this store today."""


class FrontierService:
    """Scores a candidate on the Frontier tasks in a private store. See the module docstring for the boundary."""

    def __init__(self, store_dir: str | os.PathLike, *, executor: Executor, env_passthrough=()) -> None:
        if executor is None:
            raise ValueError("FrontierService needs an executor: without one the candidate is never used")
        self._store = Path(store_dir)
        self._executor = executor
        self._passthrough = tuple(env_passthrough)

    def __repr__(self) -> str:
        return "FrontierService()"

    def evaluate(self, candidate: str, seeds: int = 1) -> list[dict]:
        """Run every task `seeds` times; one {task_id_hash, score (mean), cost (sum)} per task, sorted by hash."""
        _, tasks, key = _prepare(self._store, candidate, seeds, charge=True)
        if not tasks:
            return []
        return _summarise(_run_privately(tasks, candidate, seeds, self._executor, self._passthrough), key)


def _prepare(path: Path, candidate: str, seeds: int, *, charge: bool) -> tuple[Path, list, bytes]:
    """Check the arguments and the store, load every task, and (if `charge`) spend one query of the budget."""
    if not isinstance(candidate, str):
        raise TypeError(f"candidate must be a string, got {type(candidate).__name__}")
    if not candidate.strip():
        raise ValueError("candidate must be a git ref or config id, got an empty string")
    if isinstance(seeds, bool) or not isinstance(seeds, int):
        raise TypeError(f"seeds must be an int, got {type(seeds).__name__}")
    if not 1 <= seeds <= MAX_SEEDS:
        raise ValueError(f"seeds must be between 1 and {MAX_SEEDS}, got {seeds}")
    store = _checked_store(path)
    loaded = _load_all(store)
    if loaded is None:  # raised here, outside the except, so no traceback chain carries the file's text
        raise ValueError("a file in the Frontier store is not a valid task; fix the store before scoring")
    if len({t.id for t in loaded}) != len(loaded):
        raise ValueError("two files in the Frontier store share a task id")
    if not loaded:
        return store, [], b""
    if not _verifiers_import(loaded):
        raise ValueError("the Frontier store is broken: a task's verifier cannot be imported, so no score is given")
    key = _salt(store)
    if charge:
        _charge(store, candidate)
    return store, loaded, key


def _checked_store(path: Path) -> Path:
    """The store's real path, or PermissionError/ValueError/FileNotFoundError if it is not private or not clean."""
    try:
        store = path.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError):
        raise FileNotFoundError(f"Frontier store {path} does not exist") from None
    if not store.is_dir():
        raise ValueError(f"Frontier store {path} is not a directory")
    if store == _CHECKOUT or _CHECKOUT in store.parents:
        raise PermissionError("the Frontier store must lie outside the repository checkout, which the agent can read")
    _check_private(store, "the Frontier store directory")
    stray = False
    for p in store.iterdir():
        if p.name in (_SALT, _LEDGER):
            if p.is_symlink():
                raise PermissionError("the Frontier store's own files must not be symlinks")
            _check_private(p, "a file the Frontier service keeps in its store")
        elif _SALT_TMP.fullmatch(p.name):
            continue  # left by a crash while making the salt; holds nothing but random bytes
        elif p.suffix == ".yaml" and not p.name.startswith("."):
            if p.is_symlink():
                raise PermissionError("the Frontier store must not hold symlinked task files")
            _check_private(p, "a Frontier task file")
            stray = stray or not p.is_file()
        else:
            stray = True
    if stray:  # never skipped: a missing task would change what the score means (RT-11). No name: it may be an id.
        raise ValueError("the Frontier store holds an entry that is not a *.yaml task file (rename .yml files to .yaml)")
    return store


def _check_private(p: Path, what: str) -> None:
    st = p.lstat()
    if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
        raise PermissionError(f"{what} must be owned by the user the service runs as")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise PermissionError(f"{what} must give no access to group or other (chmod go-rwx)")


def _load_all(store: Path) -> list | None:
    """Every task in the store, or None if any file is invalid. Errors are swallowed: their text quotes the file."""
    tasks = []
    for p in sorted(store.glob("*.yaml")):
        try:
            tasks.append(load_task(p))
        except Exception:  # noqa: BLE001 - any bad file refuses the whole store, without saying what was in it
            return None
    return tasks


def _verifiers_import(tasks) -> bool:
    """True if every task's verifier resolves here. Checked before any attempt, so a broken task is never a 0 (RT-10)."""
    for t in tasks:
        try:
            resolve(t.verifier)
        except Exception:  # noqa: BLE001 - the error names the module; the caller gets a fixed message instead
            return False
    return True


def _salt(store: Path) -> bytes:
    """The store's secret hash key, created 0600 on first use. A missing or short salt is replaced atomically (RT-6)."""
    path = store / _SALT
    key = _read_salt(path)
    if key is not None and len(key) >= _SALT_BYTES:
        return key
    # A short salt was never used to hash (it was refused), so replacing it breaks no past score.
    fd, tmp = tempfile.mkstemp(prefix=".salt-", suffix=".tmp", dir=store)  # 0600
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(os.urandom(_SALT_BYTES))
            f.flush()
            os.fsync(f.fileno())
        if key is None:
            try:
                os.link(tmp, path)  # atomic and never overwrites: a concurrent first call keeps the winner's key
            except FileExistsError:
                pass
        else:
            os.replace(tmp, path)
        _fsync_dir(store)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    key = _read_salt(path)
    if key is None or len(key) < _SALT_BYTES:
        raise ValueError("the Frontier store's salt could not be made; check the store is writable by the service")
    return key


def _read_salt(path: Path) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as f:
        return f.read()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _charge(store: Path, candidate: str) -> None:
    """Spend one of today's evaluations for `candidate`, or record a refusal and raise QueryBudgetExceeded (RT-5)."""
    today = datetime.now(UTC).date().isoformat()
    fd = os.open(store / _LEDGER, os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # two calls at once cannot both take the last query
        f.seek(0)
        used = 0
        for line in f.read().splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("outcome") == "ran" and entry.get("day") == today \
                    and entry.get("candidate") == candidate:
                used += 1
        outcome = "ran" if used < DAILY_QUERY_BUDGET else "refused"
        f.write(json.dumps({"day": today, "candidate": candidate, "outcome": outcome}) + "\n")
        f.flush()
        os.fsync(f.fileno())
    if outcome == "refused":
        raise QueryBudgetExceeded(
            f"this candidate has used its {DAILY_QUERY_BUDGET} Frontier evaluations for today (UTC); "
            "the refusal is recorded"
        )


def _hash(key: bytes, task_id: str) -> str:
    return hmac.new(key, task_id.encode(), hashlib.sha256).hexdigest()[:32]


def _bounded_cost(value: object) -> float:
    """A cost the service will repeat: finite, >= 0, in cents, at most MAX_COST_PER_TASK. Anything else is 0 (RT-1)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    value = float(value)
    if not math.isfinite(value) or value < 0:
        return 0.0
    return min(round(value, 2), MAX_COST_PER_TASK)


def _summarise(result, key: bytes) -> list[dict]:
    """One {task_id_hash, score, cost} per task from AR1 seed results, sorted by hash."""
    totals: dict[str, tuple[float, float, int]] = {}
    for r in result.results:
        score_sum, cost_sum, n = totals.get(r.task_id, (0.0, 0.0, 0))
        totals[r.task_id] = (score_sum + r.score, cost_sum + _bounded_cost(r.cost), n + 1)
    out = []
    for tid, (s, c, n) in totals.items():
        cost = min(round(c, 2), MAX_COST_PER_TASK)
        if not math.isfinite(cost):  # cannot happen with the caps above; never report one if it does (RT-9)
            raise ValueError("a Frontier task's summed cost is not a finite number")
        out.append({"task_id_hash": _hash(key, tid), "score": float(s / n), "cost": float(cost)})
    return sorted(out, key=lambda e: e["task_id_hash"])


def _run_privately(tasks, candidate, seeds, executor, passthrough):
    """AR1 run() with its experiments rows sent to a throwaway db, so no task id or reason reaches the caller's.

    run() reads its db path from the environment, so the swap holds a process-wide lock: overlapping calls run one
    after the other, and each restores exactly what it found (RT-7).
    """
    with _ENV_LOCK:
        tmp = tempfile.mkdtemp(prefix="tanishi-frontier-")  # 0700
        saved = os.environ.get(ENV_VAR)
        os.environ[ENV_VAR] = str(Path(tmp) / "core_state.db")
        try:
            return run(tasks, candidate, seeds, executor=executor, env_passthrough=passthrough)
        finally:
            if saved is None:
                os.environ.pop(ENV_VAR, None)
            else:
                os.environ[ENV_VAR] = saved
            shutil.rmtree(tmp, ignore_errors=True)


# --- the CLI: the process that prints never imports the candidate's code (RT-4) -------------------------------------

# The child reads one JSON line (its sys.path and the request) before importing anything else.
_CHILD_BOOT = (
    "import json, sys; req = json.loads(sys.stdin.readline()); sys.path[:] = req['path']; "
    "from tanishi.arena.frontier_service import _child_main; sys.exit(_child_main(req))"
)


def _child_main(req: dict) -> int:
    """Run in the child: evaluate (no budget charge, the parent did that) and write only `ok hash score cost` lines."""
    try:
        _, tasks, key = _prepare(Path(req["store"]), req["candidate"], req["seeds"], charge=False)
        executor = resolve(req["executor"])
        out = _summarise(_run_privately(tasks, req["candidate"], req["seeds"], executor, req["passthrough"]), key)
        lines = [f"ok {e['task_id_hash']} {e['score']!r} {e['cost']!r}" for e in out]
    except Exception as e:  # noqa: BLE001 - only the class name goes back
        lines = [f"error {type(e).__name__}"]
    with os.fdopen(req["fd"], "w") as pipe:
        pipe.write("\n".join(lines + ["end"]) + "\n")
    return 0


def _evaluate_in_child(store: Path, tasks, key: bytes, candidate: str, seeds: int, executor: str,
                       passthrough: tuple[str, ...]) -> list[dict]:
    """Evaluate in a child with a minimal environment; accept only the scores the store says should come back."""
    expected = {_hash(key, t.id) for t in tasks}
    env = {k: os.environ[k] for k in (*_CHILD_ENV, *passthrough) if k in os.environ}
    read_fd, write_fd = os.pipe()
    req = {"path": [p for p in sys.path if p], "store": str(store), "candidate": candidate, "seeds": seeds,
           "executor": executor, "passthrough": list(passthrough), "fd": write_fd}
    try:
        proc = subprocess.Popen(
            [sys.executable, "-I", "-c", _CHILD_BOOT], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=env, pass_fds=(write_fd,), start_new_session=True,
        )
    finally:
        os.close(write_fd)
    limit = 60 + 2 * seeds * sum(t.timeout_s for t in tasks)
    timer = threading.Timer(limit, _kill_group, args=(proc,))
    timer.start()
    try:
        proc.stdin.write((json.dumps(req) + "\n").encode())
        proc.stdin.close()
        raw = b""
        with os.fdopen(read_fd, "rb") as pipe:
            while len(raw) <= _CHILD_OUTPUT_LIMIT and (chunk := pipe.read(65536)):
                raw += chunk
        proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            _kill_group(proc)
            proc.wait()
    return _parse_child(raw, expected)


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _parse_child(raw: bytes, expected: set[str]) -> list[dict]:
    """The child's lines as a score list, or RuntimeError. Nothing the child wrote is ever repeated as text."""
    bad = RuntimeError("the Frontier evaluation process sent back something other than scores")
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError:
        raise bad from None
    if len(lines) == 2 and lines[1] == "end" and lines[0].startswith("error "):
        name = lines[0][len("error "):]
        raise RuntimeError(f"the Frontier evaluation failed ({name if _NAME.fullmatch(name) else 'unknown error'})")
    if not lines or lines[-1] != "end":
        raise bad
    out: dict[str, dict] = {}
    for line in lines[:-1]:
        parts = line.split(" ")
        if len(parts) != 4 or parts[0] != "ok" or not _HASH.fullmatch(parts[1]) or parts[1] not in expected \
                or parts[1] in out:
            raise bad
        try:
            score, cost = float(parts[2]), float(parts[3])
        except ValueError:
            raise bad from None
        if not (math.isfinite(score) and 0.0 <= score <= 1.0):
            raise bad
        if not (math.isfinite(cost) and 0.0 <= cost <= MAX_COST_PER_TASK and round(cost, 2) == cost):
            raise bad
        out[parts[1]] = {"task_id_hash": parts[1], "score": score, "cost": cost}
    if set(out) != expected:
        raise bad
    return [out[h] for h in sorted(out)]


def main(argv: list[str] | None = None) -> int:
    """CLI for running the service as the store's own OS user: prints the score list as JSON."""
    parser = argparse.ArgumentParser(prog="python -m tanishi.arena.frontier_service")
    parser.add_argument("store")
    parser.add_argument("candidate")
    parser.add_argument("--executor", required=True, help="dotted path module.function of an AR1 executor")
    parser.add_argument("--seeds", type=int, default=1)
    parser.add_argument("--env-passthrough", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if not _DOTTED.fullmatch(args.executor):
            raise ValueError("--executor must be a dotted path module.function")
        passthrough = tuple(args.env_passthrough)
        if any(not _NAME.fullmatch(n) or n in _CHILD_ENV or n == ENV_VAR for n in passthrough):
            raise ValueError("--env-passthrough must name variables the service does not set itself")
        store, tasks, key = _prepare(Path(args.store), args.candidate, args.seeds, charge=True)
        scores = _evaluate_in_child(store, tasks, key, args.candidate, args.seeds, args.executor, passthrough) \
            if tasks else []
        text = json.dumps(scores, allow_nan=False)
    except (OSError, ValueError, TypeError, RuntimeError) as e:
        print(f"frontier: {type(e).__name__}: {e}", file=sys.stderr)  # our messages never quote a task
        return 2
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["DAILY_QUERY_BUDGET", "MAX_COST_PER_TASK", "MAX_SEEDS", "FrontierService", "QueryBudgetExceeded", "main"]
