"""
Tanishi Mutator
===============
Proposes mutations to Tanishi's config files. The "creativity" of autoresearch
lives here — this is where we decide what to try changing.

Two modes for proposing mutations:
  1. RULE-BASED  — sample from a curated list of known-good experiments
  2. LLM-BASED   — ask Claude to look at recent experiments and suggest a new one

We use rule-based for the first ~20 experiments to build a baseline of data,
then switch to LLM-based which can learn from history (which mutations worked,
which didn't) to propose smarter changes.

Mutations are stored as a unified diff so they're auditable and reversible.
"""

import os
import re
import json
import random
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional

from tanishi.autoresearch.reflections import (
    REFLECTIONS_PATH,
    load_failed_mutation_descriptions,
)


# ---------------------------------------------------------------------------
# Rule-based mutation library
# ---------------------------------------------------------------------------
# Each entry is a function that takes the project root and returns:
#   {"description": str, "file": str, "old": str, "new": str}
# Or None if the mutation can't be applied (e.g. file doesn't exist).

def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.exists() else None


def _ollama_chat_once(prompt: str) -> str | None:
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
            timeout=90,
        )
        if r.status_code != 200:
            return None
        txt = r.json().get("message", {}).get("content", "")
        return txt.strip() if txt else None
    except Exception:
        return None


def _parse_json_block(raw: str) -> dict | list | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
        if m:
            text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# --- system_prompt mutations ---

def mut_prompt_more_concise(root: Path):
    f = root / "tanishi/config/prompts.py"
    text = _read(f)
    if not text or "SYSTEM_PROMPT" not in text:
        return None
    addition = '\n\nResponse style: Default to 1-2 sentences. Expand only if asked or genuinely needed.'
    if addition in text:
        return None
    new = text.replace('SYSTEM_PROMPT = """', f'SYSTEM_PROMPT = """{addition}\n', 1)
    return {
        "description": "system_prompt: add explicit conciseness instruction",
        "file": str(f),
        "old": text,
        "new": new,
    }

def mut_prompt_tool_first(root: Path):
    f = root / "tanishi/config/prompts.py"
    text = _read(f)
    if not text:
        return None
    addition = '\n\nWhen the user asks about anything current/factual/system-related, prefer using a tool over guessing.'
    if addition in text:
        return None
    new = text.replace('"""', f'{addition}\n"""', 1)
    return {
        "description": "system_prompt: encourage tool-first behavior",
        "file": str(f),
        "old": text,
        "new": new,
    }

def mut_prompt_personality_warmer(root: Path):
    f = root / "tanishi/config/personality.py"
    text = _read(f)
    if not text:
        return None
    if "PERSONALITY_TONE" not in text:
        return None
    new = re.sub(
        r'PERSONALITY_TONE\s*=\s*"[^"]*"',
        'PERSONALITY_TONE = "warm-sarcastic: tease lightly but always genuinely care, lead with empathy"',
        text,
    )
    if new == text:
        return None
    return {
        "description": "personality: warmer tone, lead with empathy",
        "file": str(f),
        "old": text,
        "new": new,
    }


# --- routing_logic mutations ---

def mut_routing_prefer_haiku(root: Path):
    f = root / "tanishi/config/routing.py"
    text = _read(f)
    if not text or "SIMPLE_QUERY_MODEL" not in text:
        return None
    new = re.sub(
        r'SIMPLE_QUERY_MODEL\s*=\s*"[^"]*"',
        'SIMPLE_QUERY_MODEL = "claude-haiku-4-5-20251001"',
        text,
    )
    if new == text:
        return None
    return {
        "description": "routing: use Haiku for simple queries (faster + cheaper)",
        "file": str(f),
        "old": text,
        "new": new,
    }

def mut_routing_local_first(root: Path):
    f = root / "tanishi/config/routing.py"
    text = _read(f)
    if not text or "LOCAL_FIRST" not in text:
        return None
    new = re.sub(r"LOCAL_FIRST\s*=\s*(True|False)", "LOCAL_FIRST = True", text)
    if new == text:
        return None
    return {
        "description": "routing: try Ollama first for greetings/chitchat",
        "file": str(f),
        "old": text,
        "new": new,
    }


# --- memory_retrieval mutations ---

def mut_memory_lower_threshold(root: Path):
    f = root / "tanishi/config/memory_params.py"
    text = _read(f)
    if not text:
        return None
    m = re.search(r"SIMILARITY_THRESHOLD\s*=\s*(0?\.\d+)", text)
    if not m:
        return None
    cur = float(m.group(1))
    new_val = max(0.5, cur - 0.05)
    if abs(new_val - cur) < 0.001:
        return None
    new = re.sub(
        r"SIMILARITY_THRESHOLD\s*=\s*0?\.\d+",
        f"SIMILARITY_THRESHOLD = {new_val:.2f}",
        text,
    )
    return {
        "description": f"memory: lower similarity threshold {cur:.2f} -> {new_val:.2f} (recall more)",
        "file": str(f),
        "old": text,
        "new": new,
    }

def mut_memory_increase_topk(root: Path):
    f = root / "tanishi/config/memory_params.py"
    text = _read(f)
    if not text:
        return None
    m = re.search(r"MEMORY_TOP_K\s*=\s*(\d+)", text)
    if not m:
        return None
    cur = int(m.group(1))
    new_val = cur + 2
    if new_val > 15:
        return None
    new = re.sub(r"MEMORY_TOP_K\s*=\s*\d+", f"MEMORY_TOP_K = {new_val}", text)
    return {
        "description": f"memory: increase top_k {cur} -> {new_val}",
        "file": str(f),
        "old": text,
        "new": new,
    }


# --- tool_params mutations ---

def mut_tools_shorter_timeout(root: Path):
    f = root / "tanishi/config/tool_params.py"
    text = _read(f)
    if not text:
        return None
    m = re.search(r"DEFAULT_TOOL_TIMEOUT\s*=\s*(\d+)", text)
    if not m:
        return None
    cur = int(m.group(1))
    new_val = max(5, cur - 2)
    if new_val == cur:
        return None
    new = re.sub(r"DEFAULT_TOOL_TIMEOUT\s*=\s*\d+", f"DEFAULT_TOOL_TIMEOUT = {new_val}", text)
    return {
        "description": f"tools: tighten timeout {cur}s -> {new_val}s (fail fast)",
        "file": str(f),
        "old": text,
        "new": new,
    }

def mut_tools_more_retries(root: Path):
    f = root / "tanishi/config/tool_params.py"
    text = _read(f)
    if not text:
        return None
    m = re.search(r"TOOL_RETRIES\s*=\s*(\d+)", text)
    if not m:
        return None
    cur = int(m.group(1))
    if cur >= 3:
        return None
    new_val = cur + 1
    new = re.sub(r"TOOL_RETRIES\s*=\s*\d+", f"TOOL_RETRIES = {new_val}", text)
    return {
        "description": f"tools: more retries {cur} -> {new_val}",
        "file": str(f),
        "old": text,
        "new": new,
    }


# --- voice_params mutations ---

def mut_voice_smaller_chunks(root: Path):
    f = root / "tanishi/voice/voice_config.py"
    text = _read(f)
    if not text:
        return None
    m = re.search(r"TTS_CHUNK_SIZE\s*=\s*(\d+)", text)
    if not m:
        return None
    cur = int(m.group(1))
    new_val = max(20, cur - 10)
    if new_val == cur:
        return None
    new = re.sub(r"TTS_CHUNK_SIZE\s*=\s*\d+", f"TTS_CHUNK_SIZE = {new_val}", text)
    return {
        "description": f"voice: smaller TTS chunks {cur} -> {new_val} (lower latency)",
        "file": str(f),
        "old": text,
        "new": new,
    }


# --- tool_descriptions mutations ---

def mut_tool_desc_clearer(root: Path):
    f = root / "tanishi/tools/tool_descriptions.py"
    text = _read(f)
    if not text:
        return None
    if "USE THIS WHEN" in text:
        return None
    # Replace generic "Description:" with stronger trigger language
    new = text.replace('"""', '"""USE THIS WHEN: ', 1) if '"""' in text else None
    if not new or new == text:
        return None
    return {
        "description": "tool_descriptions: add explicit USE THIS WHEN trigger language",
        "file": str(f),
        "old": text,
        "new": new,
    }


def _build_dynamic_rule_fn(rule_obj: dict) -> Callable:
    """
    Build a mutation callable from a JSON config object.
    Current supported type: text_replace
    """
    rtype = str(rule_obj.get("type", "text_replace"))
    description = str(rule_obj.get("description", "dynamic rule")).strip()
    target_file = str(rule_obj.get("target_file", "")).strip()
    search = str(rule_obj.get("search", ""))
    replace = str(rule_obj.get("replace", ""))

    def _fn(root: Path):
        if rtype != "text_replace":
            return None
        if not target_file or target_file.endswith("autoresearch.py"):
            return None
        f = root / target_file
        text = _read(f)
        if text is None:
            return None
        if not search or search not in text:
            return None
        new = text.replace(search, replace, 1)
        if new == text:
            return None
        return {
            "description": description,
            "file": str(f),
            "old": text,
            "new": new,
        }

    _fn.__name__ = f"dyn_{re.sub(r'[^a-z0-9_]+', '_', description.lower())[:40]}"
    return _fn


def mut_meta_add_rule_entry(root: Path, reflections_context: str = ""):
    """
    Meta-mutation: propose one new mutation rule and append it to mutation_rules.json.
    """
    cfg_path = MUTATION_RULES_PATH
    raw = _read(cfg_path)
    if not raw:
        return None
    parsed = _parse_json_block(raw)
    if not isinstance(parsed, dict):
        return None

    prompt = (
        "Given these existing mutation rules and these lessons from failed experiments, "
        "propose ONE new mutation rule that could improve performance.\n\n"
        f"Current rules json:\n{json.dumps(parsed, ensure_ascii=False)[:12000]}\n\n"
        f"Lessons:\n{(reflections_context or '(none)')[:4000]}\n\n"
        "Output JSON only:\n"
        "{\n"
        '  "area": "system_prompt|routing_logic|memory_retrieval|tool_params|voice_params|personality",\n'
        '  "description": "short mutation description",\n'
        '  "config_change": {\n'
        '    "type": "text_replace",\n'
        '    "target_file": "repo-relative path",\n'
        '    "search": "exact old substring",\n'
        '    "replace": "new substring"\n'
        "  }\n"
        "}"
    )
    model_out = _ollama_chat_once(prompt)
    obj = _parse_json_block(model_out or "")
    if not isinstance(obj, dict):
        return None

    area = str(obj.get("area", "")).strip()
    desc = str(obj.get("description", "")).strip()
    change = obj.get("config_change")
    if not area or area == "mutation_rules":
        return None
    if area not in parsed or not isinstance(parsed.get(area), list):
        return None
    if not desc or not isinstance(change, dict):
        return None
    if str(change.get("type", "")) != "text_replace":
        return None
    tfile = str(change.get("target_file", ""))
    if (not tfile) or tfile.endswith("autoresearch.py"):
        return None
    if not str(change.get("search", "")).strip():
        return None

    # Ensure deterministic description for reflection skip matching.
    meta_desc = f"mutation_rules: add dynamic rule for {area} — {desc}"
    dynamic_entry = {
        "type": "text_replace",
        "description": meta_desc,
        "target_file": tfile,
        "search": str(change.get("search", "")),
        "replace": str(change.get("replace", "")),
    }
    parsed[area].append(dynamic_entry)
    new_text = json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
    return {
        "description": meta_desc,
        "file": str(cfg_path),
        "old": raw,
        "new": new_text,
    }


MUTATION_RULES_PATH = Path(__file__).resolve().parent / "mutation_rules.json"

# Canonical function registry (json references these names)
RULE_FUNCTIONS: dict[str, Callable] = {
    "mut_prompt_more_concise": mut_prompt_more_concise,
    "mut_prompt_tool_first": mut_prompt_tool_first,
    "mut_prompt_personality_warmer": mut_prompt_personality_warmer,
    "mut_routing_prefer_haiku": mut_routing_prefer_haiku,
    "mut_routing_local_first": mut_routing_local_first,
    "mut_memory_lower_threshold": mut_memory_lower_threshold,
    "mut_memory_increase_topk": mut_memory_increase_topk,
    "mut_tools_shorter_timeout": mut_tools_shorter_timeout,
    "mut_tools_more_retries": mut_tools_more_retries,
    "mut_voice_smaller_chunks": mut_voice_smaller_chunks,
    "mut_tool_desc_clearer": mut_tool_desc_clearer,
    "mut_meta_add_rule_entry": mut_meta_add_rule_entry,
}


def load_mutation_library(
    rules_path: Path = MUTATION_RULES_PATH,
    reflections_context: str = "",
) -> dict[str, list[Callable]]:
    """
    Load mutation areas from mutation_rules.json and resolve to callables.
    Unknown function names are skipped with a warning.
    """
    if not rules_path.exists():
        raise RuntimeError(f"mutation rules config not found at {rules_path}")

    try:
        raw = json.loads(rules_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"invalid mutation rules json: {e}") from e

    if not isinstance(raw, dict):
        raise RuntimeError("mutation rules json must be an object: {area: [rule_fn_name]}")

    library: dict[str, list[Callable]] = {}
    for area, names in raw.items():
        if not isinstance(area, str):
            continue
        if not isinstance(names, list):
            print(f"[mutator] invalid rule list for area '{area}', skipping")
            continue
        resolved: list[Callable] = []
        for entry in names:
            if isinstance(entry, str):
                fn = RULE_FUNCTIONS.get(entry)
                if fn is None:
                    print(f"[mutator] unknown rule function '{entry}' in area '{area}', skipping")
                    continue
                # Pass reflections context only for meta rule proposer.
                if entry == "mut_meta_add_rule_entry":
                    def _meta(root: Path, _fn=fn, _ctx=reflections_context):
                        return _fn(root, reflections_context=_ctx)
                    _meta.__name__ = getattr(fn, "__name__", "mut_meta_add_rule_entry")
                    resolved.append(_meta)
                else:
                    resolved.append(fn)
            elif isinstance(entry, dict):
                dyn_fn = _build_dynamic_rule_fn(entry)
                resolved.append(dyn_fn)
        library[area] = resolved
    return library


# ---------------------------------------------------------------------------
# Mutation history (so we don't repeat ones that didn't work)
# ---------------------------------------------------------------------------

def load_recent_history(history_path: Path, n: int = 30) -> list[dict]:
    """Load the last n experiments from the JSONL log."""
    if not history_path.exists():
        return []
    lines = history_path.read_text(encoding="utf-8").strip().split("\n")
    history = []
    for line in lines[-n:]:
        try:
            history.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return history


# ---------------------------------------------------------------------------
# LLM-based mutation proposer
# ---------------------------------------------------------------------------

LLM_PROPOSER_PROMPT = """You are a research assistant helping improve an AI assistant called Tanishi.

Tanishi is a personal assistant with voice, vision, tools, and memory.
We measure her on three dimensions: response quality, latency, and tool reliability.

Recent experiments (most recent first):
{history}
{reflections_block}
Current focus area: {area}

Propose ONE specific, small change to try next. The change must be:
  - actionable (a concrete code change to a config file)
  - small (a single setting, not a rewrite)
  - learn from history (don't repeat ideas that already failed)

Available areas and what they mean:
  - system_prompt:    Tanishi's main system prompt (style, behavior)
  - routing_logic:    when to use which model (Claude/Haiku/Ollama/GPT)
  - memory_retrieval: SIMILARITY_THRESHOLD, MEMORY_TOP_K
  - tool_params:      DEFAULT_TOOL_TIMEOUT, TOOL_RETRIES
  - voice_params:     TTS_CHUNK_SIZE, filler timing
  - tool_descriptions: how tools are described to the model

Respond with a single sentence describing what you want to try and why.
Just the idea, no preamble."""

def propose_via_llm(
    area: str, history_path: Path, reflections_context: str = "",
) -> dict | None:
    """Ask Claude to propose a mutation. Falls back to None on error."""
    try:
        from anthropic import Anthropic
        client = Anthropic()
        history = load_recent_history(history_path, n=20)
        history_str = "\n".join(
            f"  [{h.get('status', '?')}] {h.get('area', '?')}: {h.get('description', '')[:80]} "
            f"(score={h.get('score', 0):.4f})"
            for h in reversed(history)
        ) or "  (no history yet)"
        reflections_block = (
            "\n" + reflections_context.strip() + "\n"
            if reflections_context.strip()
            else "\n"
        )

        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": LLM_PROPOSER_PROMPT.format(
                    area=area, history=history_str, reflections_block=reflections_block
                ),
            }],
        )
        idea = msg.content[0].text.strip()
        return {"description": f"[llm] {idea}", "_llm_idea": idea, "_area": area}
    except Exception as e:
        print(f"[mutator] LLM proposer failed, falling back to rule-based: {e}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def propose_mutation(
    area: str,
    history_path: Path,
    reflections_context: str = "",
    reflections_path: Optional[Path] = None,
) -> dict:
    """
    Pick a mutation for the given area.
    First ~20 experiments use rules (to build a dataset), then mostly LLM.
    """
    refl_path = reflections_path if reflections_path is not None else REFLECTIONS_PATH
    failed_mutations = load_failed_mutation_descriptions(20, path=refl_path)

    history = load_recent_history(history_path, n=100)
    n_done = len(history)

    # Phase 1: rule-based (first 20)
    use_llm = n_done >= 20 and random.random() < 0.5

    if use_llm:
        llm_mut = propose_via_llm(area, history_path, reflections_context=reflections_context)
        if llm_mut is not None:
            # LLM proposes the IDEA but we still need a concrete diff.
            # For now, fall back to rule-based to actually edit files.
            # (Future: have the LLM emit a diff directly.)
            pass

    # Pick a rule-based mutation that hasn't been tried recently
    mutation_library = load_mutation_library(reflections_context=reflections_context)
    rules = mutation_library.get(area, [])
    if not rules:
        raise RuntimeError(f"no mutation rules registered for area '{area}'")

    project_root = Path(__file__).resolve().parents[2]
    random.shuffle(rules)
    recent_descriptions = {h.get("description", "") for h in history[-10:]}

    for rule_fn in rules:
        try:
            mut = rule_fn(project_root)
        except Exception as e:
            print(f"[mutator] rule {rule_fn.__name__} errored: {e}")
            continue
        if mut is None:
            continue
        if mut["description"] in failed_mutations:
            print(
                f"[mutator] skipping '{mut['description']}' — reflection says it failed before"
            )
            continue
        if mut["description"] in recent_descriptions:
            continue
        return mut

    # If everything's been tried recently, just return any applicable one
    for rule_fn in rules:
        try:
            mut = rule_fn(project_root)
        except Exception:
            continue
        if mut is None:
            continue
        if mut["description"] in failed_mutations:
            print(
                f"[mutator] skipping '{mut['description']}' — reflection says it failed before"
            )
            continue
        return mut

    raise RuntimeError(f"no applicable mutations found for area '{area}'")


def apply_mutation(mutation: dict, project_root: Path):
    """Write the mutated file content to disk."""
    file_path = Path(mutation["file"])
    file_path.write_text(mutation["new"], encoding="utf-8")


def revert_mutation(mutation: dict):
    """Restore the original file content (used if apply succeeded but bench crashed)."""
    file_path = Path(mutation["file"])
    file_path.write_text(mutation["old"], encoding="utf-8")
