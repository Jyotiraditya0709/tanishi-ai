"""Small helpers shared by the Practice verifiers. Reasons built here never contain an expected answer."""
from __future__ import annotations

import json
import math
import os
import re
import select
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from tanishi.arena.practice._code_harness import NotPlain, decode, encode
from tanishi.arena.verifiers import Verdict, candidate_fault

_FENCE = re.compile(r"```[A-Za-z0-9_+-]*\n(.*?)```", re.DOTALL)
# Not glued to a word or a decimal point on either side: '7.5' and 'v3.12' hold no integer, '1,2' holds two.
_INT = re.compile(r"(?<![\w.])[-+]?\d+(?!\w|\.\d)")
CODE_TIMEOUT_S = 10
HARNESS = Path(__file__).with_name("_code_harness.py")
MAX_REPLY = 32 * 1024 * 1024


def text_of(output: object) -> str | None:
    return output.strip() if isinstance(output, str) and output.strip() else None


def words(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def standalone_ints(text: str) -> set[int]:
    """Integers that stand on their own: not part of a file name, a version, a decimal or a longer word."""
    return {int(m) for m in _INT.findall(text)}


def has_token(text: str, token: str) -> bool:
    """`token` appears as a whole name: 'c.py' matches 'src/c.py' but not 'abc.py' or 'c.pyc'."""
    return re.search(rf"(?<![\w.]){re.escape(token)}(?![\w])", text, re.IGNORECASE) is not None


def code_of(output: str) -> str:
    """The first fenced code block of the output, or the whole output when it has no fence."""
    m = _FENCE.search(output)
    return m.group(1) if m else output


def same(got: object, want: object, tol: float = 1e-9) -> bool:
    """Equal in value and in exact type, all the way down. An int is accepted where a float is expected; a bool never
    stands in for a number. Floats compare within `tol`."""
    if type(want) is float:
        return type(got) in (int, float) and math.isclose(got, want, rel_tol=tol, abs_tol=tol)
    if type(got) is not type(want):
        return False
    if type(want) in (list, tuple):
        return len(got) == len(want) and all(same(a, b, tol) for a, b in zip(got, want, strict=True))
    if type(want) is dict:
        return got.keys() == want.keys() and all(same(got[k], want[k], tol) for k in want)
    return got == want


class CandidateError(Exception):
    """A call into the candidate's code raised, returned a non-plain value, or broke the protocol."""


class _TimedOut(Exception):
    pass


class _CodeProcess:
    """The candidate's code in its own process (see _code_harness.py), with one deadline for everything."""

    def __init__(self, cwd: str, timeout_s: float) -> None:
        env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": cwd, "TMPDIR": cwd}
        if "LANG" in os.environ:
            env["LANG"] = os.environ["LANG"]
        self.deadline = time.monotonic() + timeout_s
        self.proc = subprocess.Popen(
            [sys.executable, "-I", str(HARNESS)], cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        self.out = self.proc.stdin.fileno()
        self.inp = self.proc.stdout.fileno()
        os.set_blocking(self.out, False)
        self.buf = b""

    def _left(self) -> float:
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise _TimedOut
        return left

    def ask(self, msg: dict) -> dict:
        data = memoryview(json.dumps(msg).encode() + b"\n")
        while data:
            if not select.select([], [self.out], [], self._left())[1]:
                raise _TimedOut
            try:
                data = data[os.write(self.out, data):]
            except BrokenPipeError as e:
                raise EOFError from e
        while b"\n" not in self.buf:
            if not select.select([self.inp], [], [], self._left())[0]:
                raise _TimedOut
            chunk = os.read(self.inp, 1 << 16)
            if not chunk:
                raise EOFError
            self.buf += chunk
            if len(self.buf) > MAX_REPLY:
                raise CandidateError("reply too long")
        line, _, self.buf = self.buf.partition(b"\n")
        reply = json.loads(line)
        if not isinstance(reply, dict):
            raise CandidateError("reply is not an object")
        return reply

    def proxy(self, name: str) -> Callable:
        def call(*args):
            try:
                reply = self.ask({"call": name, "args": [encode(a) for a in args]})
                if "ok" not in reply:
                    raise CandidateError("the call raised")
                result = decode(reply["ok"])
                after = [decode(a) for a in reply.get("args", [])]
            except (EOFError, ValueError, KeyError, TypeError, NotPlain) as e:
                raise CandidateError("bad reply") from e
            for arg, new in zip(args, after, strict=False):  # mirror in-place changes, so "must not mutate" is testable
                if type(arg) is list and type(new) is list:
                    arg[:] = new
            return result

        return call

    def close(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, AttributeError):
            pass
        self.proc.kill()
        self.proc.wait()
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                f.close()
            except OSError:
                pass


def run_python_tests(
    output: object, tests: str | Callable[[dict], None], *, given: dict | None = None,
    timeout_s: float = CODE_TIMEOUT_S,
) -> Verdict:
    """Load the candidate's code in a separate process and run `tests` against it here, in the verifier.

    `tests` is Python source (exec'd) or a function taking the namespace. The namespace holds `given` plus, for every
    callable the candidate defined, a proxy that calls it in the candidate's process and returns its result as plain
    data (a str subclass with its own __eq__ is refused). 1.0 when the tests finish without raising, else 0.

    The decision is made in this process, after the candidate answers, from values it returned: no secret, nonce or
    expected value is ever on the candidate's command line, in its environment or on its pipe, so there is nothing
    to print to score (RT-AR2-1). The candidate's process gets PATH, LANG and a temp HOME/TMPDIR only (RT-AR2-2).
    Code that runs past `timeout_s` (all calls together) is the candidate's failure.

    Known limit: the candidate runs as the same OS user, so in-process tricks are out of reach but a determined
    program could still try to read this process's files. Real sandboxing comes later.
    """
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    with tempfile.TemporaryDirectory() as cwd:
        code = _CodeProcess(cwd, timeout_s)
        try:
            try:
                loaded = code.ask({"code": code_of(got)})
            except (EOFError, ValueError, CandidateError):
                return Verdict(0.0, "code did not load")
            names = loaded.get("names")
            if loaded.get("loaded") is not True or not isinstance(names, list):
                return Verdict(0.0, "code did not load")
            namespace = dict(given or {})
            namespace.update({n: code.proxy(n) for n in names if isinstance(n, str) and n.isidentifier()})
            try:
                if callable(tests):
                    tests(namespace)
                else:
                    exec(compile(tests, "<tests>", "exec"), namespace)  # noqa: S102 - our own asserts
            except _TimedOut:
                raise
            except Exception:  # noqa: BLE001 - an assert, a failed call, a missing function: all a fail
                return Verdict(0.0, "tests failed")
            return Verdict(1.0, "tests passed")
        except _TimedOut:
            return candidate_fault("code timed out")
        finally:
            code.close()
