"""
Reflexion-style memory for autoresearch: natural-language lessons from failed experiments.
Append-only log at autoresearch_results/reflections.jsonl.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REFLECTIONS_PATH = Path(__file__).resolve().parents[2] / "autoresearch_results" / "reflections.jsonl"


def _read_last_n_jsonl_records(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    records = []
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def load_failed_mutation_descriptions(n: int = 20, path: Optional[Path] = None) -> set[str]:
    """Mutation strings from the last N reflection records (for exact-match skip)."""
    path = path or REFLECTIONS_PATH
    out: set[str] = set()
    for rec in _read_last_n_jsonl_records(path, n):
        m = rec.get("mutation")
        if isinstance(m, str) and m:
            out.add(m)
    return out


def load_recent_reflections(n: int = 20, path: Optional[Path] = None) -> str:
    """Formatted block of recent reflection lessons, or empty string if none."""
    path = path or REFLECTIONS_PATH
    records = _read_last_n_jsonl_records(path, n)
    if not records:
        return ""
    lines = ["LESSONS FROM PAST EXPERIMENTS:"]
    for i, rec in enumerate(records, 1):
        area = rec.get("area", "?")
        refl = rec.get("reflection", "")
        if isinstance(refl, str):
            refl = refl.replace("\n", " ").strip()
        if len(refl) > 160:
            refl = refl[:157] + "..."
        lines.append(f"{i}. [{area}] {refl}")
    return "\n".join(lines)


def load_recent_reflection_count(n: int = 20, path: Optional[Path] = None) -> int:
    """Number of valid reflection records in the last N lines."""
    path = path or REFLECTIONS_PATH
    return len(_read_last_n_jsonl_records(path, n))


def _ollama_one_sentence(prompt: str) -> Optional[str]:
    try:
        import requests

        base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        model = os.getenv("OLLAMA_MODEL", "gemma4:e4b")
        r = requests.post(
            f"{base}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=60,
        )
        if r.status_code != 200:
            return None
        raw = r.json().get("message", {}).get("content", "").strip()
        return raw if raw else None
    except Exception:
        return None


def _task_breakdown_str(task_results: Optional[list[Any]]) -> str:
    if not task_results:
        return "(none)"
    parts = []
    for r in task_results:
        name = getattr(r, "name", "?")
        q = getattr(r, "quality_score", 0.0)
        parts.append(f"{name}: {q:.2f}")
    return "; ".join(parts)


def write_reflection(
    experiment_id: str,
    area: str,
    mutation: str,
    score: float,
    baseline: float,
    task_results: Optional[list[Any]] = None,
    path: Optional[Path] = None,
) -> None:
    path = path or REFLECTIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    delta = score - baseline
    tasks_str = _task_breakdown_str(task_results)

    prompt = (
        "An AI self-improvement experiment failed.\n"
        f"Area: {area}\n"
        f"Mutation: {mutation}\n"
        f"Score: {score} (baseline was {baseline}, delta {delta})\n"
        f"Task scores: {tasks_str}\n"
        "In ONE sentence, explain why this mutation probably hurt performance "
        "and what to avoid next time."
    )

    reflection = _ollama_one_sentence(prompt)
    if not reflection:
        reflection = (
            f"Mutation '{mutation}' in area '{area}' decreased score by {delta:.2f}. "
            "Avoid similar changes."
        )

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "experiment_id": experiment_id,
        "area": area,
        "mutation": mutation,
        "score": score,
        "baseline": baseline,
        "delta": delta,
        "reflection": reflection,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
