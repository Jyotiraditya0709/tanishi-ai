"""Implementer's tests for the Continuity Test v0 scoring rules. The exam is test_snapshot.py (another agent)."""
import json

import pytest

from tanishi.core_state import continuity as ct


def _write(tmp_path, pairs):
    p = tmp_path / "q.json"
    p.write_text(json.dumps([{"question": q, "expected": e} for q, e in pairs]))
    return p


def _answers(mapping):
    return lambda q, h: mapping[q]


PAIRS = [(f"Synthetic question {i}?", f"answer-{i}") for i in range(8)] + [
    ("Synthetic city?", "New Delhi"),
    ("Synthetic district?", "Delhi"),
]


def test_whitespace_case_and_end_punctuation_do_not_matter(tmp_path):
    qf = _write(tmp_path, PAIRS)
    answers = {q: f"  {e.upper()}. " for q, e in PAIRS}
    assert ct.run(qf, tmp_path, _answers(answers)).passed


def test_answer_in_a_sentence_is_not_a_whole_answer(tmp_path):
    qf = _write(tmp_path, PAIRS)
    answers = {q: f"I think it is {e}" for q, e in PAIRS}
    assert ct.run(qf, tmp_path, _answers(answers)).correct == 0


def test_expected_that_contains_another_expected_can_still_pass(tmp_path):
    qf = _write(tmp_path, PAIRS)  # "New Delhi" contains "Delhi": the right answer must not be counted wrong for that
    assert ct.run(qf, tmp_path, _answers(dict(PAIRS))).passed


def test_the_same_expected_answer_for_two_questions_can_pass(tmp_path):
    pairs = [(f"Synthetic yes-or-no {i}?", "yes") for i in range(10)]
    assert ct.run(_write(tmp_path, pairs), tmp_path, lambda q, h: "Yes").passed


def test_duplicate_questions_are_refused_ignoring_case_and_spacing(tmp_path):
    pairs = [*PAIRS[:9], ("  synthetic QUESTION 0? ", "other")]
    with pytest.raises(ValueError, match="repeats"):
        ct.load_questions(_write(tmp_path, pairs))


def test_punctuation_only_expected_is_refused(tmp_path):
    pairs = [*PAIRS[:9], ("Synthetic blank?", " ?! ")]
    with pytest.raises(ValueError):
        ct.load_questions(_write(tmp_path, pairs))


def test_non_string_answer_is_wrong(tmp_path):
    assert ct.run(_write(tmp_path, PAIRS), tmp_path, lambda q, h: None).correct == 0
