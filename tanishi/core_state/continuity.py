"""Continuity Test v0 (node CS7): is a restored instance still her?

The test asks exactly 10 distinct questions about her history and goals and passes only if every answer is right. The
question file lives in vault/, never in this package: it is a JSON list of {"question": str, "expected": str}.

An answer is right only when the whole answer equals `expected` after normalising both (Unicode NFKC, case folded,
whitespace collapsed, surrounding punctuation dropped). So "no" does not match "I don't know", and a dump of all memory
does not match anything. As a second guard, an answer that echoes its own question, or that contains the expected
answer of another question (other than text that is part of its own expected answer), is wrong.

`answerer(question, home)` stands for the restored instance answering from the memory under `home`. The brain that will
do this for real does not read the Core State yet, so the caller supplies it.

Run the real vault file only from a process the candidate cannot call. `run()` hands each question to the answerer in
plain text and the result says how many were right, so a candidate that may call it repeatedly can learn the expected
answers by trial. Never give `ContinuityResult` (or `missed`, which holds personal question text) back to a candidate.
"""
from __future__ import annotations

import json
import os
import re
import string
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

QUESTION_COUNT = 10

Answerer = Callable[[str, Path], str]

_EDGE = string.whitespace + string.punctuation


@dataclass(frozen=True)
class ContinuityResult:
    correct: int
    total: int
    missed: list[str] = field(default_factory=list)  # the questions answered wrong, never the expected answers

    @property
    def passed(self) -> bool:
        return self.total == QUESTION_COUNT and self.correct == self.total


def normalise(text: str) -> str:
    """The form answers are compared in: NFKC, case folded, single spaces, no punctuation at either end."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split()).strip(_EDGE)


def _contains_words(haystack: str, needle: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack) is not None


def load_questions(questions_file: str | os.PathLike[str]) -> list[tuple[str, str]]:
    """Read and check the question file. Raises OSError if unreadable, ValueError if malformed."""
    data = json.loads(Path(questions_file).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("question file must hold a JSON list")  # noqa: TRY004 - a malformed file is a ValueError, like bad JSON
    if len(data) != QUESTION_COUNT:
        raise ValueError(f"Continuity Test v0 needs exactly {QUESTION_COUNT} questions, got {len(data)}")
    out = []
    seen: set[str] = set()
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"question {i} is not an object")  # noqa: TRY004 - same
        q, expected = item.get("question"), item.get("expected")
        if not isinstance(q, str) or not q.strip():
            raise ValueError(f"question {i} has no question text")
        if not isinstance(expected, str) or not normalise(expected):
            raise ValueError(f"question {i} has no expected answer")  # a blank one would match anything
        if normalise(q) in seen:
            raise ValueError(f"question {i} repeats an earlier question")  # ten copies would test one fact
        seen.add(normalise(q))
        out.append((q, expected.strip()))
    return out


def _is_right(answer: object, question: str, expected: str, all_expected: list[str]) -> bool:
    if not isinstance(answer, str):
        return False
    a, want = normalise(answer), normalise(expected)
    if a != want or a == normalise(question):
        return False
    # Whole-answer matching already implies this; it stays so that R4 (a memory dump passes) holds if matching is ever
    # loosened to accept answers in prose.
    others = (normalise(e) for e in all_expected)
    return not any(not _contains_words(want, o) and _contains_words(a, o) for o in others)


def run(questions_file: str | os.PathLike[str], home: str | os.PathLike[str], answerer: Answerer) -> ContinuityResult:
    """Ask every question of the instance at `home` and score the answers."""
    questions = load_questions(questions_file)
    all_expected = [e for _q, e in questions]
    home_path = Path(home)
    correct, missed = 0, []
    for q, expected in questions:
        if _is_right(answerer(q, home_path), q, expected, all_expected):
            correct += 1
        else:
            missed.append(q)
    return ContinuityResult(correct=correct, total=len(questions), missed=missed)
