"""Continuity Test v0 (node CS7): is a restored instance still her?

The test asks exactly 10 questions about her history and goals and passes only if every answer is right. The question
file lives in vault/, never in this package: it is a JSON list of {"question": str, "expected": str}. An answer is right
when it contains `expected`, ignoring case.

`answerer(question, home)` stands for the restored instance answering from the memory under `home`. The brain that will
do this for real does not read the Core State yet, so the caller supplies it.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

QUESTION_COUNT = 10

Answerer = Callable[[str, Path], str]


@dataclass(frozen=True)
class ContinuityResult:
    correct: int
    total: int
    missed: list[str] = field(default_factory=list)  # the questions answered wrong, never the expected answers

    @property
    def passed(self) -> bool:
        return self.total == QUESTION_COUNT and self.correct == self.total


def load_questions(questions_file: str | os.PathLike[str]) -> list[tuple[str, str]]:
    """Read and check the question file. Raises OSError if unreadable, ValueError if malformed."""
    data = json.loads(Path(questions_file).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("question file must hold a JSON list")  # noqa: TRY004 - a malformed file is a ValueError, like bad JSON
    if len(data) != QUESTION_COUNT:
        raise ValueError(f"Continuity Test v0 needs exactly {QUESTION_COUNT} questions, got {len(data)}")
    out = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"question {i} is not an object")  # noqa: TRY004 - same
        q, expected = item.get("question"), item.get("expected")
        if not isinstance(q, str) or not q.strip():
            raise ValueError(f"question {i} has no question text")
        if not isinstance(expected, str) or not expected.strip():
            raise ValueError(f"question {i} has no expected answer")  # a blank one would match anything
        out.append((q, expected.strip()))
    return out


def run(questions_file: str | os.PathLike[str], home: str | os.PathLike[str], answerer: Answerer) -> ContinuityResult:
    """Ask every question of the instance at `home` and score the answers."""
    questions = load_questions(questions_file)
    home_path = Path(home)
    correct, missed = 0, []
    for q, expected in questions:
        answer = answerer(q, home_path)
        if isinstance(answer, str) and expected.casefold() in answer.casefold():
            correct += 1
        else:
            missed.append(q)
    return ContinuityResult(correct=correct, total=len(questions), missed=missed)
