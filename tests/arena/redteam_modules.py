"""Red-team probes for AR1: executors and verifiers imported by name inside spawned attempts."""
import importlib
import os
import tempfile

from tanishi.arena.runner import Attempt

SECRET_ANSWER = "hunter2-secret-answer"


def check_secret(task, output):
    """A verifier whose expected answer lives in its module, like any real code verifier."""
    return (1.0, "ok") if output and output.strip() == SECRET_ANSWER else (0.0, "wrong")


def always_one(task, output):
    return 1.0, "ok"


def reads_verifier_module(task, candidate, seed):
    """A candidate that never solved anything: it imports the verifier's module and reads the answer."""
    module = importlib.import_module(task.verifier.rpartition(".")[0])
    return Attempt(module.SECRET_ANSWER)


def patches_verifier(task, candidate, seed):
    """Replaces the verifier in-process; the verifier then runs in the same process and says 1.0."""
    module = importlib.import_module(task.verifier.rpartition(".")[0])
    module.check_secret = lambda t, o: (1.0, "patched")
    return Attempt("not the answer")


def leaves_marker_in_tmp(task, candidate, seed):
    marker = os.path.join(tempfile.gettempdir(), "rt-marker")
    seen = os.path.exists(marker)
    open(marker, "w").close()
    return Attempt("seen" if seen else "fresh")


def seen_marker(task, output):
    return (0.0, "marker from an earlier attempt") if output == "seen" else (1.0, "fresh")


def sees_task_verifier(task, candidate, seed):
    return Attempt("verifier-visible" if getattr(task, "verifier", None) else "hidden")


def verifier_visible(task, output):
    return (0.0, "executor saw the verifier path") if output == "verifier-visible" else (1.0, "hidden")


def raises_with_prompt(task, candidate, seed):
    raise RuntimeError(f"model call failed for prompt {task.prompt!r} key={os.environ.get('RT_FAKE_KEY')}")
