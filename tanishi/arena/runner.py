"""The Arena runner: run(task_set, candidate, seeds) -> RunResult.

Every (task, seed) attempt runs in its own spawned process with a fresh temp HOME, TANISHI_HOME and
Core State db, and its working directory inside that temp home. Legacy path variables are unset there,
so nothing an attempt does can reach the caller's memory, skills or databases (the legacy benchmark
leaked 17 skills into real chats). The caller's own environment is never modified.

An attempt is: run `setup` (shell, cwd = temp home), call the executor, then call the verifier.
A crash, a timeout, an empty output or a malformed verdict scores 0, never more.
Each attempt is written as one `experiments` row in the caller's Core State db
($TANISHI_CORE_STATE_DB at call time), with its score and cost.

How a candidate (git ref or config id) produces output is up to the `executor`, a picklable
module-level function `executor(task, candidate, seed) -> Attempt | str`. With no executor the
verifier alone is the attempt: it checks the candidate directly and receives `output=None`.
"""
from __future__ import annotations

import hashlib
import json
import math
import multiprocessing as mp
import os
import pickle
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tanishi.arena.task import Task
from tanishi.arena.verifiers import Verdict, normalize, resolve
from tanishi.core_state import migrate, open_db

STARTUP_TIMEOUT_S = 60.0  # spawning a process and importing the verifier; not charged to the task
MAX_REASON = 500
# Path variables legacy config reads; unset in an attempt so they cannot point it at real data.
_LEGACY_PATH_VARS = ("TANISHI_DB_PATH", "DB_PATH", "MEMORY_PATH", "SKILLS_PATH", "LOGS_PATH")


@dataclass(frozen=True)
class Attempt:
    """What an executor returns: the candidate's output and what producing it cost."""

    output: str | None
    cost: float = 0.0


Executor = Callable[[Task, str, int], "Attempt | str"]


@dataclass(frozen=True)
class SeedResult:
    task_id: str
    seed: int
    score: float
    cost: float
    reason: str
    experiment_id: str


@dataclass(frozen=True)
class RunResult:
    run_id: str
    candidate: str
    task_set: str
    seeds: int
    results: tuple[SeedResult, ...]

    @property
    def score(self) -> float:
        """Mean score over every (task, seed); 0 for an empty run."""
        return sum(r.score for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def cost(self) -> float:
        return sum(r.cost for r in self.results)

    def by_task(self) -> dict[str, float]:
        """Mean score per task id."""
        scores: dict[str, list[float]] = {}
        for r in self.results:
            scores.setdefault(r.task_id, []).append(r.score)
        return {k: sum(v) / len(v) for k, v in scores.items()}


def run(task_set: Sequence[Task], candidate: str, seeds: int = 3, *, executor: Executor | None = None) -> RunResult:
    """Run every task `seeds` times against `candidate` and record one experiments row per (task, seed)."""
    tasks = list(task_set)
    _validate(tasks, candidate, seeds, executor)
    run_id = uuid.uuid4().hex
    set_id = task_set_id(tasks)
    results: list[SeedResult] = []
    if not tasks:
        return RunResult(run_id, candidate, set_id, seeds, ())
    conn = open_db()
    try:
        migrate(conn)
        ctx = mp.get_context("spawn")
        for task in tasks:
            for seed in range(seeds):
                start = time.monotonic()
                outcome = _isolated_attempt(ctx, task, candidate, seed, executor)
                verdict = normalize((outcome.get("score"), outcome.get("reason", "")))
                cost = _cost(outcome.get("cost"))
                reason = verdict.reason[:MAX_REASON]
                meta = {
                    "run_id": run_id,
                    "task_id": task.id,
                    "family": task.family,
                    "tier": task.tier,
                    "reason": reason,
                    "duration_s": round(time.monotonic() - start, 3),
                }
                row_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO experiments (id, ts, candidate, baseline, task_set, seed, score, cost, meta) "
                    "VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                    (row_id, datetime.now(UTC).isoformat(), candidate, set_id, seed, verdict.score, cost,
                     json.dumps(meta)),
                )
                conn.commit()
                results.append(SeedResult(task.id, seed, verdict.score, cost, reason, row_id))
    finally:
        conn.close()
    return RunResult(run_id, candidate, set_id, seeds, tuple(results))


def task_set_id(tasks: Sequence[Task]) -> str:
    """A stable name for a set of tasks, so runs on the same set can be compared."""
    digest = hashlib.sha256("\n".join(sorted(t.id for t in tasks)).encode()).hexdigest()
    return f"tasks:{digest[:16]}"


def _validate(tasks: list[Task], candidate: str, seeds: int, executor: Executor | None) -> None:
    if isinstance(seeds, bool) or not isinstance(seeds, int):
        raise TypeError(f"seeds must be an int, got {type(seeds).__name__}")
    if seeds < 1:
        raise ValueError(f"seeds must be at least 1, got {seeds}")
    if not isinstance(candidate, str):
        raise TypeError(f"candidate must be a string, got {type(candidate).__name__}")
    if not candidate.strip():
        raise ValueError("candidate must be a git ref or config id, got an empty string")
    for t in tasks:
        if not isinstance(t, Task):
            raise TypeError(f"task_set must hold Task objects, got {type(t).__name__}")
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("task ids in a task set must be unique, or rows cannot be told apart")
    if executor is not None:
        try:
            pickle.dumps(executor)
        except Exception as e:
            raise TypeError(f"executor must be a picklable module-level function: {e}") from e


def _cost(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else 0.0


def _isolated_attempt(ctx, task: Task, candidate: str, seed: int, executor: Executor | None) -> dict:
    """Run one attempt in a fresh process and temp home; always returns {score, cost, reason}."""
    home = tempfile.mkdtemp(prefix="tanishi-arena-")
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(send, home, task, candidate, seed, executor, os.getcwd()), daemon=True)
    try:
        proc.start()
        send.close()
        if not recv.poll(STARTUP_TIMEOUT_S):
            return _zero(f"attempt did not start within {STARTUP_TIMEOUT_S:.0f}s")
        first = recv.recv()  # "started": the task's clock runs from here
        if isinstance(first, dict):  # failed while isolating, before it could start
            return first
        if not recv.poll(task.timeout_s):
            return _zero(f"timed out after {task.timeout_s}s")
        result = recv.recv()
        return result if isinstance(result, dict) else _zero("attempt sent a malformed result")
    except (EOFError, OSError):
        return _zero(f"attempt process died (exit code {proc.exitcode})")
    finally:
        _kill(proc)
        recv.close()
        shutil.rmtree(home, ignore_errors=True)


def _kill(proc) -> None:
    """Kill the attempt and anything it started, even after the attempt itself exited (it leads its own group)."""
    if proc.pid is not None and hasattr(os, "killpg"):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if proc.is_alive():
        proc.kill()
    proc.join(5)


def _zero(reason: str, cost: float = 0.0) -> dict:
    return {"score": 0.0, "cost": cost, "reason": reason}


# --- inside the attempt process -----------------------------------------------------------------------------------


def _child(send, home: str, task: Task, candidate: str, seed: int, executor: Executor | None, cwd: str) -> None:
    try:
        if hasattr(os, "setsid"):
            os.setsid()  # so a timeout can kill setup scripts and executor subprocesses too
        _isolate(home, cwd)
        random.seed(seed)
        send.send("started")
        result = _attempt(task, candidate, seed, executor)
    except BaseException as e:  # noqa: BLE001 - any failure is a 0, reported to the parent
        result = _zero(_crash("attempt", e))
    try:
        send.send(result)
    finally:
        send.close()


def _isolate(home: str, cwd: str) -> None:
    root = Path(home)
    tanishi_home = root / ".tanishi"
    tanishi_home.mkdir(mode=0o700)
    os.environ["HOME"] = str(root)  # legacy tools write to Path.home() / ".tanishi" directly
    os.environ["USERPROFILE"] = str(root)
    os.environ["TANISHI_HOME"] = str(tanishi_home)
    os.environ["TANISHI_CORE_STATE_DB"] = str(tanishi_home / "core_state.db")
    for var in _LEGACY_PATH_VARS:
        os.environ.pop(var, None)
    # Relative paths (legacy data/db.sqlite) must land in the temp home, but imports keep working.
    sys.path[:] = [p if p else cwd for p in sys.path]
    os.chdir(root)
    conn = open_db()
    try:
        migrate(conn)
    finally:
        conn.close()


def _attempt(task: Task, candidate: str, seed: int, executor: Executor | None) -> dict:
    if task.setup:
        try:
            done = subprocess.run(
                task.setup, shell=True, cwd=os.environ["HOME"], capture_output=True, timeout=task.timeout_s
            )
        except subprocess.TimeoutExpired:
            return _zero("setup timed out")
        if done.returncode != 0:
            return _zero(f"setup exited {done.returncode}: {done.stderr.decode(errors='replace')[-200:]}")
    output: str | None = None
    cost = 0.0
    if executor is not None:
        try:
            attempt = executor(task, candidate, seed)
        except Exception as e:
            return _zero(_crash("candidate", e))
        if isinstance(attempt, str):
            attempt = Attempt(attempt)
        if not isinstance(attempt, Attempt):
            return _zero(f"executor returned {type(attempt).__name__}, not Attempt or str")
        cost = _cost(attempt.cost)
        output = attempt.output
        if not isinstance(output, str) or not output.strip():
            return _zero("empty output", cost)
    try:
        verdict = normalize(resolve(task.verifier)(task, output))
    except Exception as e:
        return _zero(_crash("verifier", e), cost)
    return {"score": verdict.score, "cost": cost, "reason": verdict.reason[:MAX_REASON]}


def _crash(stage: str, e: BaseException) -> str:
    return f"{stage} crashed: {type(e).__name__}: {e}"[:MAX_REASON]


__all__ = ["Attempt", "Executor", "RunResult", "SeedResult", "Verdict", "run", "task_set_id"]
