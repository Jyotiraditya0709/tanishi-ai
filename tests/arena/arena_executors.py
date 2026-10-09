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


def hang(task, candidate, seed):
    time.sleep(60)
    return "late"


# --- verifiers ---------------------------------------------------------------------------------------------------


def always_one(task, output):
    return 1.0, "credit for anything"


def is_four(task, output):
    return (1.0, "four") if output == "4" else (0.0, f"got {output!r}")


def output_is_none(task, output):
    return (1.0, "no executor") if output is None else (0.0, "had output")


def report_env(task, output):
    """Score 1 only if the attempt is fully isolated; the reason carries what it saw, as JSON."""
    home = os.environ["HOME"]
    seen = {
        "home": home,
        "tanishi_home": os.environ.get("TANISHI_HOME"),
        "db": os.environ.get("TANISHI_CORE_STATE_DB"),
        "cwd": os.getcwd(),
        "legacy": [v for v in ("TANISHI_DB_PATH", "DB_PATH", "SKILLS_PATH") if v in os.environ],
        "db_exists": Path(os.environ["TANISHI_CORE_STATE_DB"]).is_file(),
        "setup_marker": Path(home, "prepared.txt").is_file(),
        "has_empty_path": "" in sys.path,
    }
    return 1.0, json.dumps(seen)


def setup_marker(task, output):
    return (1.0, "setup ran") if Path(os.environ["HOME"], "prepared.txt").is_file() else (0.0, "no marker")
