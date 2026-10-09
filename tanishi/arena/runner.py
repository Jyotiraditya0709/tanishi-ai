"""The Arena runner: run(task_set, candidate, seeds, executor=...) -> RunResult.

Every (task, seed) attempt gets a fresh temp HOME with its own TANISHI_HOME, Core State db and TMPDIR, and runs in
two processes, one after the other, each the leader of its own process group:

1. The executor process runs `setup` (shell, cwd = temp home), then `executor(brief, candidate, seed)`. It gets a
   `TaskBrief`: the task without its verifier, so the verifier's path and expected answer never enter its memory.
2. Once that process group is killed, the verifier process runs `verifier(task, output)` in the same temp home.

Both start from a minimal environment (PATH, LANG and the temp HOME, TANISHI_HOME, TANISHI_CORE_STATE_DB and TMPDIR,
plus any variable the caller names in `env_passthrough`), so nothing an attempt does can reach the caller's memory,
skills or databases (the legacy benchmark leaked 17 skills into real chats). The caller's own environment is never
modified. A child reports back as JSON lines on a private pipe, never as pickle, so a candidate cannot run code in
the runner by what it writes there.

A crash, a timeout, an empty output or a malformed verdict scores 0, never more. Each attempt becomes one
`experiments` row in the caller's Core State db ($TANISHI_CORE_STATE_DB at call time). All rows of a run are
written in one transaction at the end, so a run that dies partway leaves no rows.

How a candidate (git ref or config id) produces output is up to the `executor`, a picklable module-level function
`executor(brief, candidate, seed) -> Attempt | str`. There is no default: without one the candidate is never used,
so run() refuses.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import random
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from tanishi.arena.task import Task, TaskBrief
from tanishi.arena.verifiers import (
    CandidateFault,
    Verdict,
    normalize,
    problem_with,
    resolve,
)
from tanishi.core_state import migrate, open_db

STARTUP_TIMEOUT_S = 60.0  # starting Python and unpickling the payload; not charged to the task
MAX_REASON = 500
MAX_MESSAGE = 8 * 1024 * 1024  # one JSON line from a child; a longer one is malformed
# Set by the runner in every attempt; a caller cannot pass its own values through.
_RESERVED_VARS = frozenset({"HOME", "TANISHI_HOME", "TANISHI_CORE_STATE_DB", "TMPDIR"})
_VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ERROR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,99}")
# The child reads its sys.path as one JSON line before importing anything, then the pickled payload.
_BOOTSTRAP = (
    "import json, sys; sys.path[:] = json.loads(sys.stdin.buffer.readline()); "
    "from tanishi.arena.runner import _child_main; _child_main(int(sys.argv[1]))"
)


@dataclass(frozen=True)
class Attempt:
    """What an executor returns: the candidate's output and what producing it cost."""

    output: str | None
    cost: float = 0.0


Executor = Callable[[TaskBrief, str, int], "Attempt | str"]


@dataclass(frozen=True)
class SeedResult:
    task_id: str
    seed: int
    score: float
    cost: float
    reason: str
    experiment_id: str
    failure_kind: str | None = None  # None, "candidate", "verifier" or "infra"


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


def run(
    task_set: Sequence[Task],
    candidate: str,
    seeds: int = 3,
    *,
    executor: Executor | None = None,
    env_passthrough: Iterable[str] = (),
) -> RunResult:
    """Run every task `seeds` times against `candidate` and record one experiments row per (task, seed).

    `env_passthrough` names caller environment variables (names only) that attempts may see, e.g. an API key
    the executor needs. Nothing else from the caller's environment reaches an attempt.
    """
    tasks = list(task_set)
    passthrough = _validate(tasks, candidate, seeds, executor, env_passthrough)
    run_id = uuid.uuid4().hex
    set_id = task_set_id(tasks)
    if not tasks:
        return RunResult(run_id, candidate, set_id, seeds, ())
    conn = open_db()
    try:
        migrate(conn)  # fail before any attempt runs if the caller's db is unusable
        rows: list[tuple] = []
        results: list[SeedResult] = []
        for task in tasks:
            for seed in range(seeds):
                start = time.monotonic()
                try:
                    outcome = _isolated_attempt(task, candidate, seed, executor, passthrough)
                except Exception as e:  # noqa: BLE001 - e.g. an OverflowError in a timer: this attempt is 0, the run goes on
                    outcome = _zero("infra", "attempt could not run", error=e)
                verdict = normalize((outcome["score"], outcome["reason"]))
                cost = _cost(outcome.get("cost"))
                reason = verdict.reason[:MAX_REASON]
                kind = outcome.get("failure_kind")
                meta = {
                    "run_id": run_id,
                    "task_id": task.id,
                    "family": task.family,
                    "tier": task.tier,
                    "tags": list(task.tags),  # so readers of the rows can leave out judge:llm tasks (RT-AR2-8)
                    "reason": reason,
                    "failure_kind": kind,
                    "error": outcome.get("error"),
                    "duration_s": round(time.monotonic() - start, 3),
                }
                row_id = uuid.uuid4().hex
                rows.append((row_id, datetime.now(UTC).isoformat(), candidate, set_id, seed, verdict.score, cost,
                             json.dumps(meta)))
                results.append(SeedResult(task.id, seed, verdict.score, cost, reason, row_id, kind))
        with conn:  # one transaction: every row of the run, or none
            conn.executemany(
                "INSERT INTO experiments (id, ts, candidate, baseline, task_set, seed, score, cost, meta) "
                "VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                rows,
            )
    finally:
        conn.close()
    return RunResult(run_id, candidate, set_id, seeds, tuple(results))


def task_set_id(tasks: Sequence[Task]) -> str:
    """A name for the exact content of a task set: changing any field of any task changes it."""
    canonical = json.dumps(
        sorted((asdict(t) for t in tasks), key=lambda d: d["id"]),
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return f"tasks:{hashlib.sha256(canonical.encode()).hexdigest()[:16]}"


def _validate(
    tasks: list[Task], candidate: str, seeds: int, executor: Executor | None, env_passthrough: Iterable[str]
) -> tuple[str, ...]:
    if isinstance(seeds, bool) or not isinstance(seeds, int):
        raise TypeError(f"seeds must be an int, got {type(seeds).__name__}")
    if seeds < 1:
        raise ValueError(f"seeds must be at least 1, got {seeds}")
    if not isinstance(candidate, str):
        raise TypeError(f"candidate must be a string, got {type(candidate).__name__}")
    if not candidate.strip():
        raise ValueError("candidate must be a git ref or config id, got an empty string")
    if executor is None:
        raise ValueError("run() needs an executor: without one the candidate is never used, so any score is meaningless")
    for t in tasks:
        if not isinstance(t, Task):
            raise TypeError(f"task_set must hold Task objects, got {type(t).__name__}")
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("task ids in a task set must be unique, or rows cannot be told apart")
    try:
        pickle.dumps(executor)
    except Exception as e:
        raise TypeError(f"executor must be a picklable module-level function: {e}") from e
    if isinstance(env_passthrough, (str, bytes, dict)):
        raise TypeError("env_passthrough must be a list of variable names")
    names = tuple(env_passthrough)
    for name in names:
        if not isinstance(name, str) or not _VAR_NAME.fullmatch(name):
            raise ValueError(f"env_passthrough must hold variable names, got {name!r}")
        if name in _RESERVED_VARS:
            raise ValueError(f"{name} is set by the runner for every attempt and cannot be passed through")
    return names


def _cost(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else 0.0


def _zero(kind: str, reason: str, *, cost: object = 0.0, error: object = None) -> dict:
    """A failed attempt. Only the error's class name is kept; its text may hold prompts or secrets."""
    if isinstance(error, BaseException):
        error = type(error).__name__
    if not isinstance(error, str) or not _ERROR_NAME.fullmatch(error):
        error = None if error is None else "unknown"
    return {"score": 0.0, "cost": _cost(cost), "reason": reason, "failure_kind": kind, "error": error}


# --- one attempt, seen from the runner --------------------------------------------------------------------------


def _isolated_attempt(task: Task, candidate: str, seed: int, executor: Executor, passthrough: tuple[str, ...]) -> dict:
    """Run one attempt in a fresh temp home; always returns {score, cost, reason, failure_kind, error}."""
    home = Path(tempfile.mkdtemp(prefix="tanishi-arena-"))
    try:
        env = _prepare_home(home, passthrough)
        payload = {"brief": task.brief(), "setup": task.setup, "candidate": candidate, "seed": seed,
                   "executor": executor, "timeout_s": task.timeout_s}
        msg, problem = _stage(home, env, "executor", payload, task.timeout_s)
        if problem is not None:
            return _stage_problem(problem, "candidate", task.timeout_s)
        produced = _read_executor_result(msg)
        if "output" not in produced:
            return produced
        cost = produced["cost"]
        msg, problem = _stage(home, env, "verifier", {"task": task, "output": produced["output"]}, task.timeout_s)
        if problem is not None:
            return {**_stage_problem(problem, "verifier", task.timeout_s), "cost": cost}
        return {**_read_verifier_result(msg), "cost": cost}
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _prepare_home(home: Path, passthrough: tuple[str, ...]) -> dict[str, str]:
    """Make the attempt's TANISHI_HOME, migrated db and TMPDIR; return the only environment its processes get."""
    tanishi_home = home / ".tanishi"
    tanishi_home.mkdir(mode=0o700)
    tmp = home / "tmp"
    tmp.mkdir(mode=0o700)
    db = tanishi_home / "core_state.db"
    conn = open_db(str(db))
    try:
        migrate(conn)
    finally:
        conn.close()
    env = {name: os.environ[name] for name in passthrough if name in os.environ}
    env["PATH"] = os.environ.get("PATH", os.defpath)
    if "LANG" in os.environ:
        env["LANG"] = os.environ["LANG"]
    env.update(HOME=str(home), TANISHI_HOME=str(tanishi_home), TANISHI_CORE_STATE_DB=str(db), TMPDIR=str(tmp))
    return env


def _stage(home: Path, env: dict[str, str], stage: str, payload: dict, timeout_s: float) -> tuple[dict | None, str | None]:
    """Run one stage in its own process group and kill the whole group afterwards.

    Returns (message, None), or (None, problem) with problem one of "start", "timeout", "died", "malformed".
    """
    read_fd, write_fd = os.pipe()
    proc = None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-I", "-c", _BOOTSTRAP, str(write_fd)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=home, env=env, pass_fds=(write_fd,), start_new_session=True,
        )
        os.close(write_fd)
        write_fd = -1
        try:
            proc.stdin.write(json.dumps([p or os.getcwd() for p in sys.path]).encode() + b"\n")
            pickle.dump((stage, payload), proc.stdin)
            proc.stdin.close()
        except BrokenPipeError:
            pass  # the child is already gone; reading tells us how
        lines = _Lines(read_fd)
        try:
            first = lines.next(STARTUP_TIMEOUT_S)
        except (TimeoutError, EOFError, ValueError, TypeError):
            return None, "start"
        if first != {"started": True}:
            return first, None  # it failed before it could start, and said why
        try:
            return lines.next(timeout_s), None  # the task's clock runs from "started"
        except TimeoutError:
            return None, "timeout"
        except EOFError:
            return None, "died"
        except (ValueError, TypeError):
            return None, "malformed"
    finally:
        if proc is not None:
            _kill_group(proc)
        for fd in (read_fd, write_fd):
            if fd >= 0:
                os.close(fd)


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill the stage and everything it started, before reaping it, so its pid (= group id) cannot be reused."""
    if hasattr(os, "killpg"):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    proc.kill()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        pass
    if proc.stdin is not None and not proc.stdin.closed:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass


class _Lines:
    """Reads JSON objects, one per line, from a pipe, with a deadline per object."""

    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.buf = b""

    def next(self, timeout_s: float) -> dict:
        deadline = time.monotonic() + timeout_s
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self.fd], [], [], left)[0]:
                raise TimeoutError
            chunk = os.read(self.fd, 65536)
            if not chunk:
                raise EOFError
            self.buf += chunk
            if len(self.buf) > MAX_MESSAGE:
                raise ValueError("message too long")
        line, _, self.buf = self.buf.partition(b"\n")
        msg = json.loads(line)  # json.JSONDecodeError is a ValueError
        if not isinstance(msg, dict):
            raise TypeError("message is not an object")
        return msg


def _stage_problem(problem: str, kind: str, timeout_s: float) -> dict:
    who = "candidate" if kind == "candidate" else "verifier"
    if problem == "start":
        return _zero("infra", f"{who} process did not start within {STARTUP_TIMEOUT_S:.0f}s")
    if problem == "timeout":
        return _zero(kind, f"{who} timed out after {timeout_s}s")
    if problem == "died":
        return _zero(kind, f"{who} process died")
    return _zero(kind, f"{who} sent a malformed result")


def _read_executor_result(msg: dict) -> dict:
    """Map what the executor process sent to {output, cost} or a zero. The text of every reason is the runner's own:
    the candidate controls that process and could write anything to the pipe."""
    failure = msg.get("failure")
    cost = msg.get("cost")
    if failure is None:
        output = msg.get("output")
        if not isinstance(output, str) or not output.strip():
            return _zero("candidate", "empty output", cost=cost)
        return {"output": output, "cost": _cost(cost)}
    if failure == "setup_failed":
        code = msg.get("returncode")
        return _zero("infra", f"setup exited {code if isinstance(code, int) else '?'}")
    if failure == "setup_timeout":
        return _zero("infra", "setup timed out")
    if failure == "crashed":
        return _zero("candidate", "candidate raised", error=msg.get("error"))
    if failure == "bad_return":
        return _zero("candidate", "executor returned neither Attempt nor str")
    if failure == "empty_output":
        return _zero("candidate", "empty output", cost=cost)
    if failure == "internal":
        return _zero("infra", "attempt failed before the candidate ran", error=msg.get("error"))
    return _zero("candidate", "candidate sent a malformed result")


def _read_verifier_result(msg: dict) -> dict:
    failure = msg.get("failure")
    if failure == "crashed":
        return _zero("verifier", "verifier raised", error=msg.get("error"))
    if failure is not None:
        return _zero("infra", "verifier process failed before the verifier ran", error=msg.get("error"))
    verdict = normalize((msg.get("score"), msg.get("reason")))
    bad = bool(msg.get("malformed")) or problem_with((msg.get("score"), msg.get("reason"))) is not None
    if msg.get("candidate_fault") is True and not bad:
        return _zero("candidate", verdict.reason)
    return {"score": verdict.score, "reason": verdict.reason, "failure_kind": "verifier" if bad else None,
            "error": None}


# --- inside an attempt process ----------------------------------------------------------------------------------


def _child_main(fd: int) -> None:
    out = os.fdopen(fd, "w", encoding="utf-8")

    def send(msg: dict) -> None:
        out.write(json.dumps(msg) + "\n")
        out.flush()

    try:
        stage, payload = pickle.load(sys.stdin.buffer)
        send({"started": True})
        result = _executor_stage(**payload) if stage == "executor" else _verifier_stage(**payload)
    except BaseException as e:  # noqa: BLE001 - our own code failed; reported as an infra failure
        result = {"failure": "internal", "error": type(e).__name__}
    send(result)
    out.close()


def _executor_stage(
    brief: TaskBrief, setup: str | None, candidate: str, seed: int, executor: Executor, timeout_s: float
) -> dict:
    random.seed(seed)
    if setup:
        try:
            done = subprocess.run(setup, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=timeout_s, check=False)
        except subprocess.TimeoutExpired:
            return {"failure": "setup_timeout"}
        if done.returncode != 0:
            return {"failure": "setup_failed", "returncode": done.returncode}
    try:
        attempt = executor(brief, candidate, seed)
    except BaseException as e:  # noqa: BLE001 - any candidate failure, even SystemExit, is a 0
        return {"failure": "crashed", "error": type(e).__name__}
    if isinstance(attempt, str):
        attempt = Attempt(attempt)
    if not isinstance(attempt, Attempt):
        return {"failure": "bad_return"}
    cost = _cost(attempt.cost)
    if not isinstance(attempt.output, str) or not attempt.output.strip():
        return {"failure": "empty_output", "cost": cost}
    return {"output": attempt.output, "cost": cost}


def _verifier_stage(task: Task, output: str) -> dict:
    try:
        raw = resolve(task.verifier)(task, output)
    except BaseException as e:  # noqa: BLE001 - a verifier that raises or exits gives no credit
        return {"failure": "crashed", "error": type(e).__name__}
    verdict = normalize(raw)
    return {"score": verdict.score, "reason": verdict.reason[:MAX_REASON], "malformed": problem_with(raw) is not None,
            "candidate_fault": isinstance(raw, CandidateFault)}


__all__ = ["Attempt", "Executor", "RunResult", "SeedResult", "Verdict", "run", "task_set_id"]
