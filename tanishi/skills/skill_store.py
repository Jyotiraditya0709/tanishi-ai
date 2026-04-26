"""
Skill persistence: JSONL index under TANISHI_HOME/skills plus per-skill .md files.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tanishi.core import get_config


def _skills_dir() -> Path:
    return get_config().skills_path


def _index_path() -> Path:
    return _skills_dir() / "skill_index.jsonl"


def _slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (title or "skill").lower()).strip("_")
    return (s[:48] or "skill").strip("_") or "skill"


def _norm_pattern_set(patterns: list[str]) -> set[str]:
    return {p.strip().lower() for p in patterns if isinstance(p, str) and p.strip()}


def _trigger_overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / min(len(a), len(b))


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 1}


def load_all_skills() -> list[dict]:
    """All unique skills from the index (last JSON line per skill_id wins)."""
    path = _index_path()
    if not path.exists():
        return []
    by_id: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = rec.get("skill_id")
        if isinstance(sid, str) and sid:
            by_id[sid] = rec
    return list(by_id.values())


def save_skill(skill_data: dict) -> str:
    """
    Persist a skill: slug id, merge on >50% trigger overlap, rewrite index, write .md.
    """
    skills_dir = _skills_dir()
    skills_dir.mkdir(parents=True, exist_ok=True)
    index_path = _index_path()

    title = skill_data.get("title") or "Untitled"
    now = datetime.now(timezone.utc).isoformat()
    new_patterns = skill_data.get("trigger_patterns") or []
    if not isinstance(new_patterns, list):
        new_patterns = []
    new_set = _norm_pattern_set([str(p) for p in new_patterns])

    by_id: dict[str, dict] = {}
    for rec in load_all_skills():
        sid = rec.get("skill_id")
        if isinstance(sid, str) and sid:
            by_id[sid] = dict(rec)

    merge_sid: Optional[str] = None
    for sid, existing in by_id.items():
        if _trigger_overlap(new_set, _norm_pattern_set([str(p) for p in (existing.get("trigger_patterns") or [])])) >= 0.5:
            merge_sid = sid
            break

    if merge_sid is not None:
        ex = by_id[merge_sid]
        u_old = {str(p) for p in (ex.get("trigger_patterns") or []) if p}
        u_new = {str(p) for p in (skill_data.get("trigger_patterns") or []) if p}
        ex["trigger_patterns"] = sorted(u_old | u_new)
        ex["times_used"] = int(ex.get("times_used", 1) or 1) + 1
        ex["last_used"] = now
        if skill_data.get("procedure"):
            ex["procedure"] = str(skill_data["procedure"])
        merged_tools = list(ex.get("tools_used") or []) + list(skill_data.get("tools_used") or [])
        ex["tools_used"] = list(dict.fromkeys(str(t) for t in merged_tools if t))
        by_id[merge_sid] = ex
        out_id = merge_sid
    else:
        skill_id = skill_data.get("skill_id") or _slugify(str(title))
        base = skill_id
        n = 0
        while skill_id in by_id:
            n += 1
            skill_id = f"{base}_{n}"
        rec = {
            "skill_id": skill_id,
            "title": str(title),
            "trigger_patterns": list(
                dict.fromkeys(str(p) for p in (skill_data.get("trigger_patterns") or []) if p)
            ),
            "tools_used": [str(t) for t in (skill_data.get("tools_used") or []) if t],
            "procedure": str(skill_data.get("procedure") or ""),
            "example_input": str(skill_data.get("example_input") or "")[:2000],
            "example_output": str(skill_data.get("example_output") or "")[:2000],
            "times_used": int(skill_data.get("times_used", 1) or 1),
            "avg_satisfaction": skill_data.get("avg_satisfaction"),
            "created_at": skill_data.get("created_at") or now,
            "last_used": now,
        }
        by_id[skill_id] = rec
        out_id = skill_id

    with open(index_path, "w", encoding="utf-8") as f:
        for rec in by_id.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    _write_markdown(skills_dir, by_id[out_id])
    return out_id


def _write_markdown(skills_dir: Path, rec: dict) -> None:
    sid = rec.get("skill_id", "skill")
    path = skills_dir / f"{sid}.md"
    lines = [
        f"# {rec.get('title', 'Skill')}",
        "",
        f"**skill_id:** `{sid}`",
        "",
        "## Trigger patterns",
        "",
        *[f"- {p}" for p in (rec.get("trigger_patterns") or [])],
        "",
        "## Tools",
        "",
        *[f"- `{t}`" for t in (rec.get("tools_used") or [])],
        "",
        "## Procedure",
        "",
        str(rec.get("procedure") or ""),
        "",
        "## Example",
        "",
        "**Input:**",
        str(rec.get("example_input") or ""),
        "",
        "**Output (excerpt):**",
        str((rec.get("example_output") or "")[:2000]),
        "",
        f"_times_used: {rec.get('times_used', 1)} | last_used: {rec.get('last_used', '')}_",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def find_matching_skills(user_input: str, top_k: int = 3) -> list[dict]:
    """Keyword / substring match against trigger patterns; return top_k by score."""
    user_lower = user_input.lower()
    user_tokens = _tokenize(user_input)
    scored: list[tuple[float, dict]] = []
    for skill in load_all_skills():
        patterns = skill.get("trigger_patterns") or []
        if not isinstance(patterns, list):
            continue
        score = 0.0
        blob = " ".join(str(p) for p in patterns).lower()
        for p in patterns:
            if not isinstance(p, str):
                continue
            pl = p.lower()
            if pl and pl in user_lower:
                score += 10.0
        if user_tokens:
            ptoks = _tokenize(blob)
            score += 0.5 * len(user_tokens & ptoks)
        if score > 0:
            scored.append((score, skill))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:top_k]]


def _human_last_used(iso: str) -> str:
    if not iso:
        return "unknown"
    try:
        raw = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        days = delta.days
        if days >= 14:
            return f"{days // 7} weeks ago"
        if days >= 1:
            return f"{days} days ago"
        hrs = int(delta.total_seconds() // 3600)
        if hrs >= 1:
            return f"{hrs} hours ago"
        mins = int(delta.total_seconds() // 60)
        return f"{mins} minutes ago" if mins > 0 else "just now"
    except Exception:
        return iso[:19] if len(iso) > 19 else iso


def format_skills_for_context(skills: list[dict]) -> str:
    if not skills:
        return ""
    parts = ["KNOWN PROCEDURES (from past successful interactions):", ""]
    for sk in skills:
        title = sk.get("title", "Skill")
        triggers = sk.get("trigger_patterns") or []
        trig_s = ", ".join(str(t) for t in triggers[:12])
        if len(triggers) > 12:
            trig_s += ", ..."
        proc = str(sk.get("procedure") or "").strip()
        last = _human_last_used(str(sk.get("last_used") or ""))
        times = int(sk.get("times_used", 1) or 1)
        parts.append(f"Skill: {title}")
        parts.append(f"When to use: {trig_s}")
        parts.append("Procedure:")
        for line in proc.splitlines():
            parts.append(f"  {line}")
        parts.append(f"Last used: {last} | Used {times} times")
        parts.append("")
    return "\n".join(parts).rstrip()
