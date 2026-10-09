"""Verifiers for the 9 legacy benchmark tasks (tanishi/autoresearch/benchmark.py), ported to the Arena format.

The legacy harness scored everything with an LLM and let a missing tool call slide (it pasted tool output into the
answer in Ollama mode). Here code decides wherever code can: the three tool tasks read the event log, so they score 0
unless the tool really ran. Only the three style tasks keep a judge (tagged judge:llm, left out of CEI).
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from tanishi.arena.practice._common import has_token, text_of, words
from tanishi.arena.practice.checks import FAIL, truth
from tanishi.arena.practice.judge import judged
from tanishi.arena.practice.tooltrace import real_calls
from tanishi.arena.verifiers import Verdict

# --- style tasks: code floor + LLM judge ------------------------------------------------------------------------


def greeting(task, output):
    return judged(task, output, ["natural greeting", "in character", "concise"], lambda t: None)


def personality(task, output):
    return judged(task, output, ["empathetic", "in character"], lambda t: None)


def poem(task, output):
    def floor(text: str) -> str | None:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) < 2:
            return "not a poem: fewer than two lines"
        if "coffee" not in text.casefold():
            return "off topic"
        return None

    return judged(task, output, ["actually writes a poem"], floor)


# --- code-checked, no tool --------------------------------------------------------------------------------------


EXPLAIN_MIN_WORDS = 8  # "retrieval generation" names both topics and explains nothing


def explain_concept(task, output):
    """Two sentences on RAG: mentions retrieval and generation, at least a sentence's worth of words, under 60."""
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    low = got.casefold()
    covered = ("retriev" in low) + ("generat" in low)
    found = words(got)
    if len(found) >= 60:  # too long to be "two sentences": half credit at most, and none for a non-answer
        return Verdict(covered / 4, f"{covered} of 2 topics, too long")
    if len(found) < EXPLAIN_MIN_WORDS or len({w.casefold() for w in found}) < EXPLAIN_MIN_WORDS - 2:
        return Verdict(covered / 4, f"{covered} of 2 topics, not an explanation")
    return Verdict(covered / 2, f"{covered} of 2 topics")


COLORS = ("red", "blue", "green", "yellow", "orange", "purple", "pink", "brown", "black", "white", "grey", "teal",
          "cyan", "magenta", "maroon", "navy", "olive", "lime", "indigo", "violet", "gold", "silver", "beige", "coral")


def memory_recall(task, output):
    """The user's favorite color: setup plants a random one in the attempt's own memory folder, never in the prompt.
    The answer must name that color and no other ('gray' counts as 'grey')."""
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    planted = truth().get("color", [])
    if len(planted) != 1 or planted[0] not in COLORS:
        return Verdict(0.0, "planted values are missing")
    text = re.sub(r"(?i)\bgray\b", "grey", got)
    named = {c for c in COLORS if has_token(text, c)}
    return Verdict(1.0, "recalled") if named == {planted[0]} else Verdict(0.0, FAIL)


def math(task, output):
    """$50 a week for 6 months: 26 weeks is 1300; 24 weeks (4 a month) gives 1200. Anything from 1200 to 1350 is sane.

    Every total the answer states (any figure of 100 or more; 50, 26 and 6 are inputs) must be in that range, so a
    working that ends on "26 weeks" passes and "1300 or 2600" does not."""
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    totals = [n for n in (float(m.replace(",", "")) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", got)) if n >= 100]
    if not totals:
        return Verdict(0.0, "no total in output")
    return Verdict(1.0, "in range") if all(1200 <= n <= 1350 for n in totals) else Verdict(0.0, "wrong number")


# --- tool tasks: the tool must really have run ------------------------------------------------------------------


def _field(output: str, name: str) -> str | None:
    m = re.search(rf"^\s*{name}:\s*(.+)$", output, re.MULTILINE)
    return m.group(1).strip() if m else None


def get_time(task, output):
    """The answer's clock time must match the time get_datetime reported (hour and minute, 12h or 24h)."""
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    calls = real_calls("get_datetime")
    if not calls:
        return Verdict(0.0, "get_datetime was not really called")
    for call in calls:
        iso = _field(call.output, "iso")
        try:
            stamp = datetime.fromisoformat(iso) if iso else None
        except ValueError:
            stamp = None
        if stamp is None:
            continue
        for h, m in re.findall(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", got):
            if int(m) == stamp.minute and int(h) in {stamp.hour, stamp.hour % 12 or 12}:
                return Verdict(1.0, "time matches the tool result")
    return Verdict(0.0, "time does not match any tool result")


def _ram_gib() -> float | None:
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2**30
    except (ValueError, OSError, AttributeError):
        return None


def system_check(task, output):
    """How much RAM: needs a real system tool call, and a figure within 10% of the machine's real memory.

    get_system_info does not report RAM (see known-bugs/ar2-system-info-no-ram.md), so run_command counts too.
    """
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    if not real_calls("get_system_info", "run_command"):
        return Verdict(0.0, "no real system tool call")
    actual = _ram_gib()
    if actual is None:
        return Verdict(0.0, "cannot determine real memory")
    figures = re.findall(r"(\d+(?:\.\d+)?)\s*(tb|gb|gib|mb|mib)\b", got, re.IGNORECASE)
    units = {"tb": 1024, "gb": 1, "gib": 1, "mb": 1 / 1024, "mib": 1 / 1024}
    # Every figure must fit: "4 GB, 8 GB, 16 GB or 32 GB" hedges across sizes and is not an answer.
    if figures and all(abs(float(a) * units[u.lower()] - actual) <= 0.10 * actual for a, u in figures):
        return Verdict(1.0, "figure matches this machine")
    return Verdict(0.0, "memory figure is wrong, hedged or missing")


_NEGATION = re.compile(r"\b(not|no|never|cannot|can't|couldn't|didn't|wasn't|unable|fail\w*|error\w*)\b", re.IGNORECASE)


def flaky_probe(task, output):
    """The probe fails on odd calls and the agent must call again: a real, successful call must have said PROBE OK.

    The answer must report it plainly: some sentence says PROBE OK with no negation, and the last sentence does not
    say it failed ("I could not get a PROBE OK" is not a result)."""
    got = text_of(output)
    if got is None:
        return Verdict(0.0, "empty output")
    if not any("PROBE OK" in c.output for c in real_calls("autoresearch_flaky_probe")):
        return Verdict(0.0, "probe was not really called and successful")
    sentences = [s for s in re.split(r"[.!?\n]+", got) if s.strip()]
    plain = any("probe ok" in s.casefold() and not _NEGATION.search(s) for s in sentences)
    if plain and not _NEGATION.search(sentences[-1]):
        return Verdict(1.0, "probe ran")
    return Verdict(0.0, "answer does not report the result")


__all__ = [
    "explain_concept", "flaky_probe", "get_time", "greeting", "math", "memory_recall", "personality", "poem",
    "system_check",
]
