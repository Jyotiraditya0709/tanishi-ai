"""Nightly failure clustering (E3): failures no capability explains become `missing` candidates.

Each failed trace gives one decisive step: the earliest erroring span with no erroring child (a parent only says
"a child failed"). Steps are `{"trace_id", "text", "family"}`, where text is the step's span name and error class
(decision 0023 rule 10: ids and class names, never prompts or replies). Within a task family, steps whose word sets
overlap by at least `SIMILARITY` (Jaccard) are linked; each connected group is a cluster, and a cluster of at least
`min_size` distinct traces is dense. A dense cluster whose family no node has is registered as a `missing` node.

    steps = steps_from_spans(records, families={"task-1": "tool_use"})
    clustering.nightly(CapabilityGraph(), steps)

    python -m tanishi.frontier.clustering 2026-10-11   # one day's traces from $TANISHI_HOME/traces
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from tanishi.frontier.graph import NO_VERIFIER, CapabilityGraph, Node

SIMILARITY = 0.5  # Jaccard of the steps' word sets
UNASSIGNED = "unassigned"  # family for steps that carry none
MAX_TEXT_CHARS = 2000

_WORD = re.compile(r"[a-z0-9_]+")
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f  ]")
_SLUG = re.compile(r"[^a-z0-9_]+")


@dataclass(frozen=True)
class Cluster:
    family: str
    trace_ids: tuple[str, ...]  # sorted, distinct
    texts: tuple[str, ...]  # the distinct normalised step texts, sorted

    @property
    def size(self) -> int:
        return len(self.trace_ids)

    @property
    def candidate_id(self) -> str:
        """Deterministic node id for this cluster: printable, whatever the step text held."""
        digest = hashlib.sha256(json.dumps([self.family, self.texts]).encode()).hexdigest()[:12]
        slug = _SLUG.sub("_", self.family.lower()).strip("_")[:40] or UNASSIGNED
        return f"missing.{slug}.{digest}"


# ---------------------------------------------------------------- decisive steps

def decisive_step(spans: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The span where the failure began: the earliest `error` span with no `error` child. None for a clean trace."""
    errored = [s for s in spans if s.get("status") == "error"]
    if not errored:
        return None
    blamed_parents = {s.get("parent_id") for s in errored}
    leaves = [s for s in errored if s.get("span_id") not in blamed_parents] or errored  # a parent_id loop has no leaf
    return min(leaves, key=lambda s: (_ts(s.get("start_ts")), str(s.get("span_id"))))


def _ts(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else float("inf")


def step_text(span: Mapping[str, Any]) -> str:
    """What a decisive step is clustered on: span name and error class only."""
    return f"{span.get('name') or ''} {span.get('error') or ''}".strip()


def steps_from_spans(records: Iterable[Mapping[str, Any]], families: Mapping[str, str] | None = None
                     ) -> list[dict[str, Any]]:
    """One step per failed task in OBS1 span records (grouped by `task_id`; spans with none are skipped)."""
    by_task: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in records:
        if r.get("task_id") is not None:
            by_task[str(r["task_id"])].append(r)
    steps = []
    for task_id in sorted(by_task):
        step = decisive_step(by_task[task_id])
        if step is not None:
            steps.append({"trace_id": task_id, "text": step_text(step), "family": (families or {}).get(task_id)})
    return steps


# ---------------------------------------------------------------- clustering

def _clean(value: object, limit: int = MAX_TEXT_CHARS) -> str:
    return _CONTROL.sub(" ", value)[:limit].strip() if isinstance(value, str) else ""


def _normal(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def _min_size(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("min_size must be an int")
    if value < 1:
        raise ValueError("min_size must be >= 1")
    return value


def cluster_failures(steps: Iterable[Mapping[str, Any]], min_size: int = 3) -> list[Cluster]:
    """Dense clusters of similar decisive steps, largest first. Input order never changes the result."""
    min_size = _min_size(min_size)
    # One step per trace: if a trace repeats, its smallest (family, text) stands for it, so order cannot matter.
    per_trace: dict[str, tuple[str, str]] = {}
    for s in steps:
        trace_id = s.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id:
            continue
        item = (_clean(s.get("family"), 200) or UNASSIGNED, _normal(_clean(s.get("text"))))
        if trace_id not in per_trace or item < per_trace[trace_id]:
            per_trace[trace_id] = item
    # Identical texts in a family form one group; groups are then linked by word overlap (single linkage).
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for trace_id, key in per_trace.items():
        groups[key].append(trace_id)
    found = []
    by_family: dict[str, list[str]] = defaultdict(list)
    for family, text in sorted(groups):
        by_family[family].append(text)
    for family, texts in by_family.items():
        for component in _components(texts):
            trace_ids = tuple(sorted(t for text in component for t in groups[(family, text)]))
            if len(trace_ids) >= min_size:
                found.append(Cluster(family, trace_ids, tuple(sorted(component))))
    return sorted(found, key=lambda c: (-c.size, c.family, c.trace_ids))


def _components(texts: list[str]) -> list[list[str]]:
    words = [frozenset(t.split()) for t in texts]
    parent = list(range(len(texts)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in combinations(range(len(texts)), 2):
        a, b = words[i], words[j]
        if a and b and len(a & b) >= SIMILARITY * len(a | b):
            parent[root(i)] = root(j)
    out: dict[int, list[str]] = defaultdict(list)
    for i, t in enumerate(texts):
        out[root(i)].append(t)
    return list(out.values())


# ---------------------------------------------------------------- nightly

def explained(cluster: Cluster, nodes: Iterable[Node]) -> bool:
    """A node explains a cluster when it practises the cluster's task family (exam assumption A12)."""
    return any(n.task_family == cluster.family or n.id == cluster.candidate_id for n in nodes)


def nightly(graph: CapabilityGraph, steps: Iterable[Mapping[str, Any]], min_size: int = 3) -> list[Node]:
    """Cluster tonight's decisive steps; register each dense cluster no node explains as a `missing` node.

    Returns the new nodes. Explanation is judged against the graph as it stood before tonight, so two unexplained
    clusters in one family both become candidates. Running again on the same failures adds nothing.
    """
    known = graph.nodes()
    new = []
    for c in cluster_failures(steps, min_size=min_size):
        if explained(c, known):
            continue
        try:
            new.append(graph.add_node(c.candidate_id, task_family=c.family, verifier=NO_VERIFIER, state="missing"))
        except ValueError:  # registered meanwhile by another run
            continue
    return new


def main(argv: list[str] | None = None) -> int:
    """Run the nightly pass over one UTC day's trace file. Without task families every step is `unassigned`."""
    from tanishi.observability import trace

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args[0]):
        print("usage: python -m tanishi.frontier.clustering YYYY-MM-DD", file=sys.stderr)
        return 2
    path = trace.traces_dir() / f"{args[0]}.jsonl"
    records = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").split("\n"):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                records.append(rec)
    new = nightly(CapabilityGraph(), steps_from_spans(records))
    for n in new:
        print(n.id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
