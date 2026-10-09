"""The process that hosts a candidate's code for `run_python_tests`. Standard library only: it runs as a script with
`python -I`, so nothing of the repo is importable from it.

Protocol, one JSON object per line. The verifier sends {"code": source}; this process runs it and answers
{"loaded": true, "names": [callables it defined]} (or {"loaded": false}). Then each {"call": name, "args": [...]}
gets {"ok": result, "args": [args after the call]} or {"raised": exception class name}.

Nothing in here decides pass or fail, and nothing secret is ever sent here: the verifier runs the asserts in its own
process and only asks this one to call functions. The candidate controls this process completely, so what it sends
back is treated as plain data. Faking a reply means computing the right answer for an input it has not seen.
"""
from __future__ import annotations

import json
import os
import sys

_PLAIN = (type(None), bool, int, float, str)


class NotPlain(TypeError):
    """A value that is not built from exact None/bool/int/float/str/list/tuple/dict (a subclass does not count)."""


def encode(value, depth: int = 0):
    """Plain value -> JSON-able, keeping list/tuple/dict apart. A subclass (a str with its own __eq__) is refused."""
    if depth > 50:
        raise NotPlain("too deep")
    kind = type(value)
    if kind in _PLAIN:
        return value
    if kind is list:
        return {"l": [encode(v, depth + 1) for v in value]}
    if kind is tuple:
        return {"t": [encode(v, depth + 1) for v in value]}
    if kind is dict:
        return {"d": [[encode(k, depth + 1), encode(v, depth + 1)] for k, v in value.items()]}
    raise NotPlain(kind.__name__)


def decode(value):
    if isinstance(value, dict):
        if set(value) == {"l"}:
            return [decode(v) for v in value["l"]]
        if set(value) == {"t"}:
            return tuple(decode(v) for v in value["t"])
        if set(value) == {"d"}:
            return {decode(k): decode(v) for k, v in value["d"]}
        raise NotPlain("bad container")
    if isinstance(value, list):
        raise NotPlain("bare list")
    return value


def main() -> None:
    # Private copies of the pipes; the candidate's own print() and input() get /dev/null.
    reply = os.fdopen(os.dup(1), "w", encoding="utf-8")
    requests = os.fdopen(os.dup(0), "r", encoding="utf-8")
    null = os.open(os.devnull, os.O_RDWR)
    os.dup2(null, 0)
    os.dup2(null, 1)

    def send(msg: dict) -> None:
        reply.write(json.dumps(msg) + "\n")
        reply.flush()

    first = json.loads(requests.readline())
    namespace: dict = {"__name__": "__candidate__"}
    try:
        exec(compile(first["code"], "<candidate>", "exec"), namespace)  # noqa: S102 - this process exists to run it
    except BaseException:  # noqa: BLE001 - even SystemExit: code that does not load fails
        send({"loaded": False})
        return
    send({"loaded": True, "names": sorted(k for k, v in namespace.items() if callable(v) and isinstance(k, str))})
    for line in requests:
        msg = json.loads(line)
        try:
            args = [decode(a) for a in msg["args"]]
            result = namespace[msg["call"]](*args)
            send({"ok": encode(result), "args": [encode(a) for a in args]})
        except BaseException as e:  # noqa: BLE001 - a raise or exit inside the function is a failed call
            send({"raised": type(e).__name__})


if __name__ == "__main__":
    main()
    sys.exit(0)
