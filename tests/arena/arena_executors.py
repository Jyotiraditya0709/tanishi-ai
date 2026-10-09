"""Module-level executors and verifiers for the implementer's AR1 tests (spawned attempts import them by name)."""
import json
import os
import sys
import time
from pathlib import Path

from tanishi.arena.runner import Attempt


def echo(task, candidate, seed):
    return Attempt(f"{candidate}:{task.prompt}", cost=0.25)


def plain_string(task, candidate, seed):
    return "4"


def crash(task, candidate, seed):
    raise RuntimeError("candidate blew up")


def empty(task, candidate, seed):
    return Attempt("   ", cost=0.5)


def none_output(task, candidate, seed):
    return Attempt(None)


def bad_cost(task, candidate, seed):
    return Attempt("ok", cost=float("nan"))


def wrong_type(task, candidate, seed):
    return 42


def hard_exit(task, candidate, seed):
    os._exit(3)


def leave_background_job(task, candidate, seed):
    """Starts a job that would write the file named by the prompt after the attempt is over."""
    import subprocess

    subprocess.Popen(["sh", "-c", f"sleep 2; touch '{task.prompt}'"])
    return "done"


def hang(task, candidate, seed):
    time.sleep(60)
    return "late"


def report_brief(task, candidate, seed):
    """Reports what the executor was handed, and the environment it runs in, as JSON."""
    return json.dumps({"type": type(task).__name__, "fields": sorted(vars(task)), "env": sorted(os.environ),
                       "passed": os.environ.get("ARENA_TEST_PASSED")})


# --- verifiers ---------------------------------------------------------------------------------------------------


def always_one(task, output):
    return 1.0, "credit for anything"


def is_four(task, output):
    return (1.0, "four") if output == "4" else (0.0, f"got {output!r}")


def output_is_reason(task, output):
    """Full marks, with the executor's output as the reason, so a test can read what the executor saw."""
    return 1.0, output


def raise_exit(task, output):
    raise SystemExit(f"verifier exits with {task.prompt}")


def report_env(task, output):
    """Full marks; the reason carries what the verifier saw, as JSON (short, since reasons are capped)."""
    home = os.environ["HOME"]
    tanishi_home = str(Path(home, ".tanishi"))
    db = os.environ.get("TANISHI_CORE_STATE_DB")
    seen = {
        "home": home,
        "tanishi_home_in_home": os.environ.get("TANISHI_HOME") == tanishi_home,
        "db_in_tanishi_home": db == str(Path(tanishi_home, "core_state.db")) and Path(db).is_file(),
        "cwd_is_home": Path(os.getcwd()).resolve() == Path(home).resolve(),
        "legacy": [v for v in ("TANISHI_DB_PATH", "DB_PATH", "SKILLS_PATH") if v in os.environ],
        "setup_marker": Path(home, "prepared.txt").is_file(),
        "has_empty_path": "" in sys.path,
        "tmpdir_in_home": os.environ.get("TMPDIR") == str(Path(home, "tmp")) and Path(home, "tmp").is_dir(),
        "env": sorted(set(os.environ) - {"PATH", "LANG", "HOME", "TANISHI_HOME", "TANISHI_CORE_STATE_DB", "TMPDIR"}),
    }
    return 1.0, json.dumps(seen)


def setup_marker(task, output):
    return (1.0, "setup ran") if Path(os.environ["HOME"], "prepared.txt").is_file() else (0.0, "no marker")
