"""
Subprocess entry for autoresearch: run benchmark suite in a fresh interpreter.

Prints a single JSON object on stdout (last line). All human/log output goes to stderr.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

TIME_BUDGET_SECONDS = 180
HARD_TIMEOUT_SECONDS = 360


def _setup_bench_db() -> tuple[Path | None, bool]:
    """
    Configure DB path for this child run.

    Returns (bench_db_path_or_none, should_delete_on_exit).
    """
    override = os.environ.get("AUTORESEARCH_BENCH_DB", "").strip()
    if override == "shared":
        print(
            "[benchmark_runner] using shared DB (AUTORESEARCH_BENCH_DB=shared)",
            file=sys.stderr,
        )
        return None, False

    if override:
        bench_db = Path(override).expanduser().resolve()
        os.environ["TANISHI_DB_PATH"] = str(bench_db)
        os.environ["DB_PATH"] = str(bench_db)
        print(
            f"[benchmark_runner] bench DB: {bench_db} (AUTORESEARCH_BENCH_DB)",
            file=sys.stderr,
        )
        return bench_db, False

    bench_db = Path(tempfile.gettempdir()) / (
        f"tanishi-bench-{os.getpid()}-{int(time.time())}.db"
    )
    os.environ["TANISHI_DB_PATH"] = str(bench_db)
    os.environ["DB_PATH"] = str(bench_db)
    print(f"[benchmark_runner] isolated bench DB: {bench_db}", file=sys.stderr)
    return bench_db, True


def _cleanup_bench_db(bench_db: Path | None, should_delete: bool) -> None:
    if bench_db is None or not should_delete:
        return
    for path in (bench_db, Path(f"{bench_db}-wal"), Path(f"{bench_db}-shm")):
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass


def _log_tuning_addendum_fingerprint() -> None:
    try:
        from tanishi.config import prompts as prompts_mod

        text = (prompts_mod.TUNING_ADDENDUM or "").strip()
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        print(
            f"[benchmark_runner] TUNING_ADDENDUM len={len(text)} hash={digest}",
            file=sys.stderr,
        )
    except Exception as exc:
        print(
            f"[benchmark_runner] TUNING_ADDENDUM fingerprint failed: {exc}",
            file=sys.stderr,
        )


def _task_result_to_dict(task_result) -> dict:
    return {
        "name": task_result.name,
        "category": task_result.category,
        "success": task_result.success,
        "quality_score": task_result.quality_score,
        "latency_ms": task_result.latency_ms,
        "error": task_result.error,
        "tool_usage": list(task_result.tool_usage or []),
    }


def _emit_crash(error: BaseException) -> int:
    payload = {
        "status": "crash",
        "error": str(error),
        "traceback": traceback.format_exc(),
    }
    print(json.dumps(payload), flush=True)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run Tanishi autoresearch benchmark suite (subprocess child)",
    )
    parser.add_argument(
        "--hard-timeout",
        type=int,
        default=HARD_TIMEOUT_SECONDS,
        help="Suite hard timeout in seconds",
    )
    parser.add_argument(
        "--time-budget",
        type=int,
        default=TIME_BUDGET_SECONDS,
        help="Per-experiment time budget (plumbed for parity; suite ignores today)",
    )
    args = parser.parse_args(argv)

    bench_db: Path | None = None
    delete_bench_db = False
    try:
        bench_db, delete_bench_db = _setup_bench_db()
        _log_tuning_addendum_fingerprint()

        from tanishi.autoresearch.benchmark import run_benchmark_suite

        # run_benchmark_suite prints progress to stdout; keep stdout JSON-only.
        real_stdout = sys.stdout
        sys.stdout = sys.stderr
        try:
            bench = run_benchmark_suite(
                time_budget_s=args.time_budget,
                hard_timeout_s=args.hard_timeout,
            )
        finally:
            sys.stdout = real_stdout
        payload = {
            "status": "ok",
            "quality": bench.quality,
            "latency_ms": bench.latency_ms,
            "reliability": bench.reliability,
            "total_time_s": bench.total_time_s,
            "task_results": [_task_result_to_dict(tr) for tr in bench.task_results],
        }
        print(json.dumps(payload), flush=True)
        return 0
    except Exception as exc:
        return _emit_crash(exc)
    finally:
        _cleanup_bench_db(bench_db, delete_bench_db)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
