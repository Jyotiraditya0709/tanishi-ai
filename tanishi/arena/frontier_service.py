"""The Frontier tier: run hidden tasks against a candidate and hand back scores, never the tasks.

`FrontierService(store_dir, executor=...).evaluate(candidate, seeds=1)` returns one dict per task with exactly
`{task_id_hash, score, cost}`. Nothing else crosses the boundary:

- no prompt, task id, verifier path, verifier reason, exception text or candidate output in the return value,
  in an error message, on stdout/stderr or in a log record;
- no row in the caller's Core State. AR1's `run()` writes `task_id` and the verifier's `reason` into `experiments`,
  and a Frontier verifier's reason may carry its answer, so the service points `run()` at a throwaway db in a
  private temp dir and deletes it afterwards. The caller records the scores it was given, if it wants them.

The store is a directory of AR1 task YAML files (`*.yaml`, top level only), plus a `.salt` file the service creates
on first use. `task_id_hash` is HMAC-SHA256 of the task id keyed by that salt, so it is stable for one store but a
caller cannot confirm a guessed id by hashing it. The store is refused unless it, its task files and its salt are owned
by the user the service runs as and give no access to group or other, it holds no symlinked task file, and it lies
outside this checkout (the agent reads its own checkout).

"A path the agent OS user cannot read" also needs the service to run as a different OS user than the agent: mode
bits only protect the store from users other than its owner. Run it across that boundary with the CLI, e.g.
`sudo -u tanishi-frontier python -m tanishi.arena.frontier_service STORE CANDIDATE --executor module.function`,
which prints the scores as JSON and nothing else.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from tanishi.arena.runner import Executor, run
from tanishi.arena.task import load_task
from tanishi.arena.verifiers import resolve
from tanishi.core_state.db import ENV_VAR

_CHECKOUT = Path(__file__).resolve().parents[2]
_SALT = ".salt"
_SALT_BYTES = 32


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
        if not isinstance(candidate, str):
            raise TypeError(f"candidate must be a string, got {type(candidate).__name__}")
        if not candidate.strip():
            raise ValueError("candidate must be a git ref or config id, got an empty string")
        if isinstance(seeds, bool) or not isinstance(seeds, int):
            raise TypeError(f"seeds must be an int, got {type(seeds).__name__}")
        if seeds < 1:
            raise ValueError(f"seeds must be at least 1, got {seeds}")
        store = _checked_store(self._store)
        loaded = _load_all(store)
        if loaded is None:  # raised here, outside the except, so no traceback chain carries the file's text
            raise ValueError("a file in the Frontier store is not a valid task; fix the store before scoring")
        if len({t.id for t in loaded}) != len(loaded):
            raise ValueError("two files in the Frontier store share a task id")
        if not loaded:
            return []
        key = _salt(store)
        result = _run_privately(loaded, candidate, seeds, self._executor, self._passthrough)
        totals: dict[str, list[float]] = {}
        for r in result.results:
            score_sum, cost_sum, n = totals.setdefault(r.task_id, [0.0, 0.0, 0])
            totals[r.task_id] = [score_sum + r.score, cost_sum + r.cost, n + 1]
        out = [
            {"task_id_hash": _hash(key, tid), "score": float(s / n), "cost": float(c)}
            for tid, (s, c, n) in totals.items()
        ]
        return sorted(out, key=lambda e: e["task_id_hash"])


def _checked_store(path: Path) -> Path:
    """The store's real path, or PermissionError/ValueError/FileNotFoundError if it is not private."""
    try:
        store = path.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError):
        raise FileNotFoundError(f"Frontier store {path} does not exist") from None
    if not store.is_dir():
        raise ValueError(f"Frontier store {path} is not a directory")
    if store == _CHECKOUT or _CHECKOUT in store.parents:
        raise PermissionError("the Frontier store must lie outside the repository checkout, which the agent can read")
    _check_private(store, "the Frontier store directory")
    for p in store.glob("*.yaml"):
        if p.is_symlink():
            raise PermissionError("the Frontier store must not hold symlinked task files")
        _check_private(p, "a Frontier task file")
    salt = store / _SALT
    if salt.is_symlink():
        raise PermissionError("the Frontier store's salt must not be a symlink")
    if salt.exists():
        _check_private(salt, "the Frontier store's salt")
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


def _salt(store: Path) -> bytes:
    """The store's secret hash key, created 0600 on first use."""
    path = store / _SALT
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as f:
            f.write(os.urandom(_SALT_BYTES))
    key = path.read_bytes()
    if len(key) < _SALT_BYTES:
        raise ValueError("the Frontier store's salt is too short; delete it to make a new one")
    return key


def _hash(key: bytes, task_id: str) -> str:
    return hmac.new(key, task_id.encode(), hashlib.sha256).hexdigest()[:32]


def _run_privately(tasks, candidate, seeds, executor, passthrough):
    """AR1 run() with its experiments rows sent to a throwaway db, so no task id or reason reaches the caller's."""
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
        scores = FrontierService(args.store, executor=resolve(args.executor),
                                 env_passthrough=args.env_passthrough).evaluate(args.candidate, args.seeds)
    except (OSError, ValueError, TypeError, ImportError, AttributeError) as e:
        print(f"frontier: {type(e).__name__}: {e}", file=sys.stderr)  # our messages never quote a task
        return 2
    print(json.dumps(scores))
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["FrontierService", "main"]
