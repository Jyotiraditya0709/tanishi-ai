#!/usr/bin/env python3
"""Tanishi build orchestrator, v0.

Turns build/graph.yaml into running agents:

    python build/orchestrator.py validate
    python build/orchestrator.py plan
    python build/orchestrator.py ready
    python build/orchestrator.py card CS1 --role implementer
    python build/orchestrator.py worktree CS1
    python build/orchestrator.py launch CS1 --role implementer [--model opus] [--unattended]
    python build/orchestrator.py pr CS1
    python build/orchestrator.py status CS1 done
    python build/orchestrator.py usage
    python build/orchestrator.py arena-run BA-01 --models opus,sonnet

Statuses live in build/status.json so graph.yaml (a protected file) never changes at runtime.
Every launch is logged to build/runs.jsonl. A run that hits a plan usage limit writes
build/.paused, and no launcher starts again until the time written there.

Needs Python 3.11+, PyYAML, git, and the `claude` CLI logged in with your plan.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required: pip install pyyaml")

BUILD = Path(__file__).resolve().parent
ROOT = BUILD.parent
GRAPH = BUILD / "graph.yaml"
STATUS = BUILD / "status.json"
RUNS = BUILD / "runs.jsonl"
PAUSE = BUILD / ".paused"
AGENTS = BUILD / "agents"
TEMPLATE = BUILD / "templates" / "task_card.md"
ARENA_TASKS = BUILD / "arena" / "tasks.yaml"
WORKTREES = Path(os.environ.get("TANISHI_WORKTREES", ROOT.parent / "tanishi-worktrees"))

STATUSES = ("todo", "in_progress", "review", "done", "later")
OWNERS = ("swarm", "human", "human+swarm")
ROLES = ("orchestrator", "architect", "implementer", "tester", "researcher", "redteam", "integrator")
PAUSE_HOURS = 5  # Max plan session allowance resets every five hours
LIMIT_PATTERN = re.compile(r"(usage limit|rate[ _-]?limit|limit reached|too many requests|429)", re.IGNORECASE)

# Tools a headless agent may use without asking. Agents commit locally; they never push.
ALLOWED_TOOLS = (
    "Read,Edit,Write,"
    "Bash(pytest *),Bash(python -m pytest *),Bash(python -m tanishi*),"
    "Bash(python build/orchestrator.py validate*),Bash(ruff *),"
    "Bash(git status*),Bash(git diff *),Bash(git add *),Bash(git commit *),Bash(git log *),"
    "Bash(ls *),Bash(mkdir *)"
)


# --------------------------------------------------------------------------- loading


def load_graph(path: Path = GRAPH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def nodes_by_id(graph: dict) -> dict[str, dict]:
    return {n["id"]: n for n in graph.get("nodes", [])}


def load_status(graph: dict) -> dict[str, str]:
    saved = json.loads(STATUS.read_text()) if STATUS.exists() else {}
    out = {}
    for n in graph["nodes"]:
        out[n["id"]] = saved.get(n["id"], n.get("status_hint", "todo"))
    return out


def save_status(status: dict[str, str]) -> None:
    STATUS.write_text(json.dumps(dict(sorted(status.items())), indent=2) + "\n")


# --------------------------------------------------------------------------- validation


def validate(graph: dict) -> list[str]:
    """Return a list of problems. Empty means the graph is sound."""
    errors: list[str] = []
    nodes = graph.get("nodes") or []
    if not nodes:
        return ["graph has no nodes"]

    seen: set[str] = set()
    for n in nodes:
        nid = n.get("id")
        if not nid:
            errors.append(f"node without id: {n.get('title', '?')}")
            continue
        if nid in seen:
            errors.append(f"duplicate id {nid}")
        seen.add(nid)
        for key in ("title", "tier", "owner", "depends_on"):
            if key not in n:
                errors.append(f"{nid}: missing '{key}'")
        if n.get("owner") not in OWNERS:
            errors.append(f"{nid}: owner must be one of {OWNERS}")
        if n.get("status_hint") not in (None, *STATUSES):
            errors.append(f"{nid}: bad status_hint {n.get('status_hint')}")
        later = n.get("status_hint") == "later"
        if not later:
            for key in ("goal", "acceptance", "branch", "size"):
                if key not in n:
                    errors.append(f"{nid}: active node missing '{key}'")
        if n.get("protected") and "human" not in str(n.get("owner")):
            errors.append(f"{nid}: protected node must be owned by a human")
        branch = n.get("branch")
        if branch:
            if n.get("owner") == "swarm" and not branch.startswith("agent/"):
                errors.append(f"{nid}: swarm node branch must start with agent/")
            if n.get("protected") and branch.startswith("agent/"):
                errors.append(f"{nid}: protected node may not use an agent/ branch")

    ids = {n["id"] for n in nodes if n.get("id")}
    for n in nodes:
        for dep in n.get("depends_on") or []:
            if dep not in ids:
                errors.append(f"{n.get('id')}: depends on unknown node {dep}")
            if dep == n.get("id"):
                errors.append(f"{dep}: depends on itself")

    cycle = find_cycle(graph)
    if cycle:
        errors.append("dependency cycle: " + " -> ".join(cycle))
    return errors


def find_cycle(graph: dict) -> list[str] | None:
    deps = {n["id"]: list(n.get("depends_on") or []) for n in graph["nodes"] if n.get("id")}
    color: dict[str, int] = defaultdict(int)  # 0 new, 1 visiting, 2 done
    stack: list[str] = []

    def visit(v: str) -> list[str] | None:
        color[v] = 1
        stack.append(v)
        for w in deps.get(v, []):
            if w not in deps:
                continue
            if color[w] == 1:
                return stack[stack.index(w):] + [w]
            if color[w] == 0:
                found = visit(w)
                if found:
                    return found
        stack.pop()
        color[v] = 2
        return None

    for v in deps:
        if color[v] == 0:
            found = visit(v)
            if found:
                return found
    return None


# --------------------------------------------------------------------------- scheduling


def ready_nodes(graph: dict, status: dict[str, str]) -> list[dict]:
    out = []
    for n in graph["nodes"]:
        if status.get(n["id"]) != "todo":
            continue
        if all(status.get(d) == "done" for d in n.get("depends_on") or []):
            out.append(n)
    return out


def waves(graph: dict, status: dict[str, str]) -> list[list[str]]:
    """Group unfinished nodes into waves that can run in parallel, in dependency order."""
    remaining = {n["id"]: set(n.get("depends_on") or []) for n in graph["nodes"] if status.get(n["id"]) != "done"}
    done = {nid for nid, s in status.items() if s == "done"}
    result: list[list[str]] = []
    while remaining:
        wave = sorted(nid for nid, deps in remaining.items() if deps <= done)
        if not wave:
            result.append(sorted(remaining))  # blocked (cycle or missing); validate reports it
            break
        result.append(wave)
        done |= set(wave)
        for nid in wave:
            remaining.pop(nid)
    return result


# --------------------------------------------------------------------------- task cards


def _md_list(items) -> str:
    if not items:
        return "- (none)"
    if isinstance(items, str):
        return items.strip()
    return "\n".join(f"- {i}" for i in items)


def node_markdown(n: dict, by_id: dict[str, dict]) -> str:
    parts = [f"## {n['id']}: {n['title']}", ""]
    meta = [f"tier {n.get('tier')}", f"pillar {n.get('pillar', '-')}", f"size {n.get('size', '-')}", f"owner {n.get('owner')}"]
    if n.get("branch"):
        meta.append(f"branch {n['branch']}")
    parts += ["_" + " · ".join(meta) + "_", ""]
    if n.get("goal"):
        parts += ["**Goal.** " + n["goal"].strip(), ""]
    deps = n.get("depends_on") or []
    if deps:
        parts += ["**Builds on.** " + ", ".join(f"{d} ({by_id[d]['title']})" for d in deps if d in by_id), ""]
    if n.get("files"):
        parts += ["**Files.**", _md_list(n["files"]), ""]
    if n.get("interface"):
        parts += ["**Interface.**", "```", n["interface"].rstrip(), "```", ""]
    if n.get("schema"):
        parts += ["**Schema.**", "```", n["schema"].rstrip(), "```", ""]
    if n.get("steps"):
        parts += ["**Steps.**", _md_list(n["steps"]), ""]
    parts += ["**Acceptance.**", _md_list(n.get("acceptance")), ""]
    if n.get("out_of_scope"):
        parts += ["**Out of scope.**", _md_list(n["out_of_scope"]), ""]
    if n.get("notes"):
        parts += ["**Notes.** " + str(n["notes"]).strip(), ""]
    return "\n".join(parts).rstrip() + "\n"


def render_card(node_id: str, role: str = "implementer", graph: dict | None = None) -> str:
    graph = graph or load_graph()
    by_id = nodes_by_id(graph)
    if node_id not in by_id:
        raise SystemExit(f"unknown node {node_id}")
    if role not in ROLES:
        raise SystemExit(f"unknown role {role}; choose from {', '.join(ROLES)}")
    n = by_id[node_id]
    role_file = AGENTS / f"{role}.md"
    role_text = role_file.read_text(encoding="utf-8") if role_file.exists() else f"You are the {role} agent."
    template = TEMPLATE.read_text(encoding="utf-8")
    fills = {
        "ROLE": role,
        "ROLE_PROMPT": role_text.strip(),
        "NODE": node_markdown(n, by_id).strip(),
        "OBJECTIVE": graph.get("objective_90d", "").strip(),
        "DONE": _md_list(graph.get("definition_of_done")),
        "PROTECTED": ", ".join(graph.get("protected_paths", [])),
        "BRANCH": branch_name(n),
    }
    for key, val in fills.items():
        template = template.replace("{{" + key + "}}", val)
    return template


def branch_name(n: dict, suffix: str | None = None) -> str:
    base = n.get("branch") or f"agent/{n.get('pillar', 'misc')}"
    name = f"{base}/{n['id'].lower()}"
    return f"{name}-{suffix}" if suffix else name


# --------------------------------------------------------------------------- git and launch


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=True)


def make_worktree(n: dict, suffix: str | None = None) -> Path:
    path = WORKTREES / (n["id"].lower() + (f"-{suffix}" if suffix else ""))
    if path.exists():
        return path
    WORKTREES.mkdir(parents=True, exist_ok=True)
    branch = branch_name(n, suffix)
    run(["git", "worktree", "add", str(path), "-b", branch, "main"], cwd=ROOT)
    return path


def paused_until() -> dt.datetime | None:
    if not PAUSE.exists():
        return None
    try:
        until = dt.datetime.fromisoformat(PAUSE.read_text().strip())
    except ValueError:
        return None
    return until if until > dt.datetime.now(dt.timezone.utc) else None


def pause(hours: float, reason: str) -> dt.datetime:
    until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)
    PAUSE.write_text(until.isoformat())
    log_run({"event": "pause", "until": until.isoformat(), "reason": reason})
    return until


def log_run(record: dict) -> None:
    record.setdefault("ts", dt.datetime.now(dt.timezone.utc).isoformat())
    with open(RUNS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def model_for(role: str) -> str | None:
    models_file = AGENTS / "models.yaml"
    if not models_file.exists():
        return None
    data = yaml.safe_load(models_file.read_text()) or {}
    return (data.get("roles") or {}).get(role)


def claude_command(card: str, model: str | None, unattended: bool) -> list[str]:
    cmd = [
        "claude",
        "-p",
        card,
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        ALLOWED_TOOLS,
        "--output-format",
        "json",
    ]
    if model:
        cmd += ["--model", model]
    if unattended:
        cmd += ["--permission-prompts", "none"]  # needs Claude Code v2.1.259 or later
    return cmd


def launch(node_id: str, role: str, model: str | None, unattended: bool, suffix: str | None = None) -> int:
    graph = load_graph()
    by_id = nodes_by_id(graph)
    n = by_id.get(node_id)
    if n is None:
        raise SystemExit(f"unknown node {node_id}")
    if n.get("owner") == "human" or n.get("protected"):
        raise SystemExit(f"{node_id} is owned by you (protected). Agents do not launch on it.")
    until = paused_until()
    if until:
        raise SystemExit(f"swarm paused until {until.isoformat()} (plan usage). Not launching.")
    status = load_status(graph)
    blocked = [d for d in n.get("depends_on") or [] if status.get(d) != "done"]
    if blocked and role == "implementer":
        raise SystemExit(f"{node_id} is blocked by {', '.join(blocked)}")

    path = make_worktree(n, suffix)
    card = render_card(node_id, role, graph)
    (path / ".task_card.md").write_text(card, encoding="utf-8")
    model = model or model_for(role)
    started = dt.datetime.now(dt.timezone.utc)
    claimed = role == "implementer" and status.get(node_id) == "todo"
    if claimed:
        status[node_id] = "in_progress"
        save_status(status)

    proc = subprocess.run(claude_command(card, model, unattended), cwd=path, text=True, capture_output=True, check=False)
    ended = dt.datetime.now(dt.timezone.utc)
    result: dict = {}
    try:
        result = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        result = {"result": proc.stdout[-2000:]}
    text = f"{result.get('result', '')}\n{proc.stderr[-2000:]}"
    log_run(
        {
            "event": "run",
            "node": node_id,
            "role": role,
            "model": model,
            "worktree": str(path),
            "started": started.isoformat(),
            "minutes": round((ended - started).total_seconds() / 60, 1),
            "exit": proc.returncode,
            "is_error": bool(result.get("is_error")) or proc.returncode != 0,
            "session_id": result.get("session_id"),
            "cost_estimate_usd": result.get("total_cost_usd"),
        }
    )
    if claimed and proc.returncode != 0:
        status[node_id] = "todo"  # a failed run gives the node back to the queue
        save_status(status)
    if proc.returncode != 0 and LIMIT_PATTERN.search(text):
        until = pause(PAUSE_HOURS, "usage limit reported by claude")
        print(f"Usage limit hit. Swarm paused until {until.isoformat()}.")
    print(result.get("result", proc.stdout)[-4000:])
    return proc.returncode


def open_pr(node_id: str) -> None:
    graph = load_graph()
    n = nodes_by_id(graph)[node_id]
    path = WORKTREES / n["id"].lower()
    branch = branch_name(n)
    run(["git", "push", "-u", "origin", branch], cwd=path)
    title = f"{n['id']}: {n['title']}"
    body = node_markdown(n, nodes_by_id(graph))
    run(["gh", "pr", "create", "--base", "main", "--head", branch, "--title", title, "--body", body], cwd=path)
    status = load_status(graph)
    status[node_id] = "review"
    save_status(status)
    print(f"PR opened for {branch}")


# --------------------------------------------------------------------------- reports


def usage_report() -> str:
    if not RUNS.exists():
        return "no runs yet"
    now = dt.datetime.now(dt.timezone.utc)
    windows = {"last 5 hours": dt.timedelta(hours=5), "last 7 days": dt.timedelta(days=7)}
    rows = [json.loads(line) for line in RUNS.read_text().splitlines() if line.strip()]
    runs = [r for r in rows if r.get("event") == "run"]
    lines = []
    for label, span in windows.items():
        recent = [r for r in runs if now - dt.datetime.fromisoformat(r["started"]) <= span]
        minutes = sum(r.get("minutes") or 0 for r in recent)
        errors = sum(1 for r in recent if r.get("is_error"))
        cost = sum(r.get("cost_estimate_usd") or 0 for r in recent)
        lines.append(f"{label}: {len(recent)} runs, {minutes:.0f} agent-minutes, {errors} errors, ~${cost:.2f} estimated")
    until = paused_until()
    lines.append(f"paused until {until.isoformat()}" if until else "not paused")
    return "\n".join(lines)


# --------------------------------------------------------------------------- builder arena


def arena_run(task_id: str, models: list[str], unattended: bool) -> None:
    tasks = yaml.safe_load(ARENA_TASKS.read_text()) if ARENA_TASKS.exists() else {}
    task = next((t for t in tasks.get("tasks", []) if t["id"] == task_id), None)
    if task is None:
        raise SystemExit(f"unknown Builder Arena task {task_id}")
    pseudo = {
        "id": task_id,
        "title": task["title"],
        "tier": "T0",
        "pillar": "builder-arena",
        "owner": "swarm",
        "branch": "agent/arena-bench",
        "size": "S",
        "depends_on": [],
        "goal": task["statement"],
        "files": task.get("files"),
        "acceptance": task.get("visible_acceptance"),
    }
    graph = load_graph()
    graph["nodes"] = [*graph["nodes"], pseudo]
    procs = []
    for model in models:
        path = make_worktree(pseudo, suffix=model)
        card = render_card(task_id, "implementer", graph)
        (path / ".task_card.md").write_text(card, encoding="utf-8")
        cmd = claude_command(card, model, unattended)
        procs.append((model, path, subprocess.Popen(cmd, cwd=path, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)))
        log_run({"event": "arena_start", "task": task_id, "model": model, "worktree": str(path)})
    for model, path, proc in procs:
        proc.communicate()
        log_run({"event": "arena_end", "task": task_id, "model": model, "exit": proc.returncode})
        print(f"[{model}] exit {proc.returncode}; worktree {path}. Score it with the hidden tests in the builder vault.")


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Tanishi build orchestrator v0")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    sub.add_parser("plan")
    sub.add_parser("ready")
    sub.add_parser("usage")
    c = sub.add_parser("card")
    c.add_argument("node")
    c.add_argument("--role", default="implementer")
    w = sub.add_parser("worktree")
    w.add_argument("node")
    lch = sub.add_parser("launch")
    lch.add_argument("node")
    lch.add_argument("--role", default="implementer")
    lch.add_argument("--model")
    lch.add_argument("--unattended", action="store_true")
    pr = sub.add_parser("pr")
    pr.add_argument("node")
    s = sub.add_parser("status")
    s.add_argument("node")
    s.add_argument("state", choices=STATUSES)
    a = sub.add_parser("arena-run")
    a.add_argument("task")
    a.add_argument("--models", required=True, help="comma-separated, e.g. opus,sonnet")
    a.add_argument("--unattended", action="store_true")
    args = p.parse_args(argv)

    graph = load_graph()
    if args.cmd == "validate":
        errors = validate(graph)
        for e in errors:
            print("ERROR", e)
        print("graph ok" if not errors else f"{len(errors)} problem(s)")
        return 1 if errors else 0
    status = load_status(graph)
    by_id = nodes_by_id(graph)
    if args.cmd == "plan":
        for i, wave in enumerate(waves(graph, status), 1):
            tags = []
            for nid in wave:
                mark = " (you)" if "human" in by_id[nid]["owner"] else ""
                mark += " (later)" if status.get(nid) == "later" else ""
                tags.append(nid + mark)
            print(f"wave {i}: " + ", ".join(tags))
    elif args.cmd == "ready":
        for n in ready_nodes(graph, status):
            who = "you" if "human" in n["owner"] else "swarm"
            flag = "  compete" if n.get("compete") else ""
            print(f"{n['id']:<5} {n.get('size', '-'):<2} {who:<5} {n['title']}{flag}")
    elif args.cmd == "card":
        print(render_card(args.node, args.role, graph))
    elif args.cmd == "worktree":
        print(make_worktree(by_id[args.node]))
    elif args.cmd == "launch":
        return launch(args.node, args.role, args.model, args.unattended)
    elif args.cmd == "pr":
        open_pr(args.node)
    elif args.cmd == "status":
        if args.node not in by_id:
            raise SystemExit(f"unknown node {args.node}")
        status[args.node] = args.state
        save_status(status)
        print(f"{args.node} -> {args.state}")
    elif args.cmd == "usage":
        print(usage_report())
    elif args.cmd == "arena-run":
        arena_run(args.task, [m.strip() for m in args.models.split(",") if m.strip()], args.unattended)
    return 0


if __name__ == "__main__":
    sys.exit(main())
