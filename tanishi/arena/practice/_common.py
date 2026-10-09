"""Small helpers shared by the Practice verifiers. Reasons built here never contain an expected answer."""
from __future__ import annotations

import re
import secrets
import subprocess
import sys
import tempfile

from tanishi.arena.verifiers import Verdict

_FENCE = re.compile(r"```[A-Za-z0-9_+-]*\n(.*?)```", re.DOTALL)
_INT = re.compile(r"(?<![\w.])[-+]?\d+(?![\w])")
CODE_TIMEOUT_S = 10


def text_of(output: object) -> str | None:
    return output.strip() if isinstance(output, str) and output.strip() else None


def words(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def standalone_ints(text: str) -> set[int]:
    """Integers that stand on their own: not part of a file name, a version or a longer word."""
    return {int(m.replace(",", "")) for m in _INT.findall(text.replace(",", ""))}


def has_token(text: str, token: str) -> bool:
    """`token` appears as a whole name: 'c.py' matches 'src/c.py' but not 'abc.py' or 'c.pyc'."""
    return re.search(rf"(?<![\w.]){re.escape(token)}(?![\w])", text, re.IGNORECASE) is not None


def code_of(output: str) -> str:
    """The first fenced code block of the output, or the whole output when it has no fence."""
    m = _FENCE.search(output)
    return m.group(1) if m else output


def run_python_tests(output: object, tests: str) -> Verdict:
    """Run the candidate's code followed by `tests` (asserts) in a fresh isolated interpreter.

    1.0 when the program exits 0, else 0. The reason says only which stage failed.
    """
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    # Reaching the end must be proved by a nonce the candidate cannot know: code that calls sys.exit(0) before the
    # asserts also exits 0.
    nonce = secrets.token_hex(16)
    program = code_of(got) + "\n\n" + tests + f"\nprint({nonce!r})\n"
    with tempfile.TemporaryDirectory() as cwd:
        try:
            done = subprocess.run(
                [sys.executable, "-I", "-c", program], cwd=cwd, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=CODE_TIMEOUT_S, check=False,
            )
        except subprocess.TimeoutExpired:
            return Verdict(0.0, "code timed out")
    lines = done.stdout.decode("utf-8", errors="replace").splitlines()
    passed = done.returncode == 0 and bool(lines) and lines[-1] == nonce
    return Verdict(1.0, "tests passed") if passed else Verdict(0.0, "tests failed")
