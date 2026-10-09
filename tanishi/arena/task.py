"""The Arena task format: one YAML file per task.

Fields: id, family, tier, prompt, setup (optional), verifier (dotted path to a Python function), timeout_s, tags.
Files are read with yaml.safe_load, so a task file can never construct Python objects.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

_REQUIRED_STR = ("id", "family", "tier", "prompt", "verifier")


@dataclass(frozen=True)
class Task:
    id: str
    family: str
    tier: str
    prompt: str
    verifier: str  # "package.module.function"
    timeout_s: float
    tags: tuple[str, ...] = ()
    setup: str | None = None  # shell script, run in the task's temp TANISHI_HOME before the attempt

    def __post_init__(self) -> None:
        for name in _REQUIRED_STR:
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"task field {name!r} must be a string, got {type(value).__name__}")
            if not value.strip():
                raise ValueError(f"task field {name!r} must not be blank")
        if "." not in self.verifier.strip("."):
            raise ValueError(f"verifier must be a dotted path module.function, got {self.verifier!r}")
        t = self.timeout_s
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            raise TypeError(f"timeout_s must be a number, got {type(t).__name__}")
        if not math.isfinite(t) or t <= 0:
            raise ValueError(f"timeout_s must be a positive finite number, got {t!r}")
        if isinstance(self.tags, (str, bytes)) or not isinstance(self.tags, (list, tuple)):
            raise TypeError(f"tags must be a list of strings, got {type(self.tags).__name__}")
        if not all(isinstance(tag, str) for tag in self.tags):
            raise TypeError("tags must be a list of strings")
        object.__setattr__(self, "tags", tuple(self.tags))
        if self.setup is not None and not isinstance(self.setup, str):
            raise TypeError(f"setup must be a string, got {type(self.setup).__name__}")


_FIELDS = frozenset(f.name for f in fields(Task))


def load_task(path: str | Path) -> Task:
    """Read one task file. Raises FileNotFoundError, or ValueError/TypeError for an invalid file."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise TypeError(f"{path}: a task file must be a mapping, got {type(data).__name__}")
    unknown = set(data) - _FIELDS
    if unknown:
        raise ValueError(f"{path}: unknown task fields {sorted(map(str, unknown))}")
    missing = (_FIELDS - {"setup", "tags"}) - set(data)
    if missing:
        raise ValueError(f"{path}: missing task fields {sorted(missing)}")
    if data.get("tags") is None:
        data["tags"] = ()
    return Task(**data)
