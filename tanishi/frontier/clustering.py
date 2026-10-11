"""Nightly failure clustering (E3): failures no capability explains become `missing` candidates.

A task failed when its root span errored (a retried error under an `ok` root is not a failure). A failed task gives
one decisive step: the earliest erroring span with no erroring child (a parent only says "a child failed"). Steps
are `{"trace_id", "text", "family"}`, where text is the step's span name and error class (decision 0023 rule 10: ids
and class names, never prompts or replies). Steps whose text has no words are dropped. Within a task family, steps
that share more than half their words (Jaccard > `SIMILARITY`) are linked; each connected group is a cluster, and a
cluster of at least `min_size` distinct traces is dense. Strict, because step texts are short: "tool.read_file
TimeoutError" and "tool.read_file KeyError" share exactly half and must not chain. A dense cluster no node explains
(see `explained`; a step's `trace_id` is the Arena task id, matched against node instances) is registered as a
`missing` node whose instances are those task ids.

    steps = steps_from_spans(records, families={"task-1": "tool_use"})
    clustering.nightly(CapabilityGraph(), steps)          # the new nodes
    clustering.nightly_report(CapabilityGraph(), steps)   # the new nodes and what was skipped

    python -m tanishi.frontier.clustering 2026-10-11   # one day's traces from $TANISHI_HOME/traces
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from tanishi.frontier.graph import NO_VERIFIER, CapabilityGraph, Node, valid_id

logger = logging.getLogger(__name__)

SIMILARITY = 0.5  # linked when the Jaccard of two steps' word sets is strictly above this
UNASSIGNED = "unassigned"  # family for steps that carry none
MAX_TEXT_CHARS = 2000
MAX_TEXTS_PER_FAMILY = 20_000  # distinct texts linked per family per night; the rest are logged and skipped

_WORD = re.compile(r"\w+")  # Unicode letters, digits and _, so non-ASCII step texts keep their words
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


def _ts(value: object, missing: float = math.inf) -> float:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return float(value) if ok else missing


def step_text(span: Mapping[str, Any]) -> str:
    """What a decisive step is clustered on: span name and error class only."""
    return f"{span.get('name') or ''} {span.get('error') or ''}".strip()


def failed_attempt(spans: list[Mapping[str, Any]]) -> list[Mapping[str, Any]] | None:
    """The spans of a task's last attempt when its root span errored, else None.

    A root is a span whose parent is not among the task's spans. Several roots are several attempts of one task id;
    the latest one decides. Spans in a parent loop belong to no root and are ignored.
    """
    ids = {s.get("span_id") for s in spans}
    roots = [s for s in spans if s.get("parent_id") is None or s.get("parent_id") not in ids]
    if not roots:
        return None
    root = max(roots, key=lambda s: (_ts(s.get("start_ts"), missing=-math.inf), str(s.get("span_id"))))
    if root.get("status") != "error":
        return None
    children: dict[object, list[Mapping[str, Any]]] = defaultdict(list)
    for s in spans:
        children[s.get("parent_id")].append(s)
    attempt, todo, seen = [root], [root], {id(root)}
    while todo:
        for child in children.get(todo.pop().get("span_id"), ()):
            if id(child) not in seen:
                seen.add(id(child))
                attempt.append(child)
                todo.append(child)
    return attempt


def steps_from_spans(records: Iterable[Mapping[str, Any]], families: Mapping[str, str] | None = None
                     ) -> list[dict[str, Any]]:
    """One step per failed task in OBS1 span records (grouped by `task_id`; spans with none are skipped)."""
    by_task: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in records:
        if isinstance(r, Mapping) and r.get("task_id") is not None:
            by_task[str(r["task_id"])].append(r)
    steps = []
    for task_id in sorted(by_task):
        attempt = failed_attempt(by_task[task_id])
        step = decisive_step(attempt) if attempt else None
        if step is not None:
            steps.append({"trace_id": task_id, "text": step_text(step), "family": (families or {}).get(task_id)})
    return steps


# ---------------------------------------------------------------- clustering

def _clean(value: object, limit: int = MAX_TEXT_CHARS) -> str:
    return _CONTROL.sub(" ", value)[:limit].strip() if isinstance(value, str) else ""


def _normal(text: str) -> str:
    return " ".join(_WORD.findall(text.casefold()))


def _min_size(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("min_size must be an int")
    if value < 1:
        raise ValueError("min_size must be >= 1")
    return value


@dataclass
class Skipped:
    """What a clustering pass left out, for the nightly report."""
    malformed_rows: int = 0  # rows that are not a mapping
    empty_steps: int = 0  # steps whose text has no words
    capped_texts: int = 0  # distinct texts beyond MAX_TEXTS_PER_FAMILY in a family


def cluster_failures(steps: Iterable[Mapping[str, Any]], min_size: int = 3) -> list[Cluster]:
    """Dense clusters of similar decisive steps, largest first. Input order never changes the result."""
    return _cluster(steps, min_size, Skipped())


def _cluster(steps: Iterable[Mapping[str, Any]], min_size: int, skipped: Skipped) -> list[Cluster]:
    min_size = _min_size(min_size)
    # One step per trace: if a trace repeats, its smallest (family, text) stands for it, so order cannot matter.
    per_trace: dict[str, tuple[str, str]] = {}
    for s in steps:
        if not isinstance(s, Mapping):
            skipped.malformed_rows += 1
            continue
        trace_id = s.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id:
            continue
        item = (_clean(s.get("family"), 200) or UNASSIGNED, _normal(_clean(s.get("text"))))
        if not item[1]:  # no name and no error class: nothing to say what failed
            skipped.empty_steps += 1
            continue
        if trace_id not in per_trace or item < per_trace[trace_id]:
            per_trace[trace_id] = item
    # Identical texts in a family form one group; groups are then linked by word overlap (single linkage).
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for trace_id, key in per_trace.items():
        groups[key].append(trace_id)
    found = []
    by_family: dict[str, list[str]] = defaultdict(list)
    for family, text in groups:
        by_family[family].append(text)
    for family, texts in sorted(by_family.items()):
        if len(texts) > MAX_TEXTS_PER_FAMILY:  # keep the texts with the most traces; ties by text, so order-free
            texts.sort(key=lambda t: (-len(groups[(family, t)]), t))
            skipped.capped_texts += len(texts) - MAX_TEXTS_PER_FAMILY
            logger.warning("family %r: %d distinct step texts, linking the first %d", family, len(texts),
                           MAX_TEXTS_PER_FAMILY)
            del texts[MAX_TEXTS_PER_FAMILY:]
        for component in _components(sorted(texts)):
            trace_ids = tuple(sorted(t for text in component for t in groups[(family, text)]))
            if len(trace_ids) >= min_size:
                found.append(Cluster(family, trace_ids, tuple(sorted(component))))
    if skipped.malformed_rows or skipped.empty_steps:
        logger.warning("skipped %d malformed rows and %d steps with no text", skipped.malformed_rows,
                       skipped.empty_steps)
    return sorted(found, key=lambda c: (-c.size, c.family, c.trace_ids))


def _components(texts: list[str]) -> list[list[str]]:
    """Connected components of texts linked by Jaccard > SIMILARITY, near-linear in the number of texts.

    Prefix filter: J(a, b) > t means |a & b| > t * |a| (and the same for b), so with words ordered rarest first, a
    and b share a word within the first |a| - floor(t * |a|) words of a (and likewise of b). Only texts that share
    such a prefix word are compared, through an inverted index; pairs already in one component are not compared.
    """
    words = [frozenset(t.split()) for t in texts]
    df = Counter(w for ws in words for w in ws)
    parent = list(range(len(texts)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    index: dict[str, list[int]] = defaultdict(list)
    for i, ws in enumerate(words):
        prefix = sorted(ws, key=lambda w: (df[w], w))[:len(ws) - math.floor(SIMILARITY * len(ws))]
        for w in prefix:
            for j in index[w]:
                if root(i) != root(j) and len(ws & words[j]) > SIMILARITY * len(ws | words[j]):
                    parent[root(i)] = root(j)
            index[w].append(i)
    out: dict[int, list[str]] = defaultdict(list)
    for i, t in enumerate(texts):
        out[root(i)].append(t)
    return list(out.values())


# ---------------------------------------------------------------- nightly

EXPLAINING_STATES = ("weak", "missing")  # states in which the graph already knows she fails there


def explained(cluster: Cluster, nodes: Iterable[Node]) -> bool:
    """A node explains a cluster when every failed task is one of its own instances and it is weak or missing (A12).

    A cluster on a mastered or unexplored node's tasks, or spread over several nodes, is news. A cluster already
    registered as a candidate (same `candidate_id`) is explained by that candidate, so a re-run adds nothing.
    """
    traces = set(cluster.trace_ids)
    return any(n.id == cluster.candidate_id
               or (n.state in EXPLAINING_STATES and traces <= set(n.instances)) for n in nodes)


@dataclass
class NightlyReport:
    new: list[Node]  # the `missing` candidates registered tonight
    clusters: int  # dense clusters found, explained or not
    skipped: Skipped = field(default_factory=Skipped)


def nightly(graph: CapabilityGraph, steps: Iterable[Mapping[str, Any]], min_size: int = 3) -> list[Node]:
    """Cluster tonight's decisive steps; register each dense cluster no node explains as a `missing` node.

    Returns the new nodes; `nightly_report` also says what was skipped. Explanation is judged against the graph as
    it stood before tonight, so two unexplained clusters in one family both become candidates. Running again on the
    same failures adds nothing.
    """
    return nightly_report(graph, steps, min_size).new


def nightly_report(graph: CapabilityGraph, steps: Iterable[Mapping[str, Any]], min_size: int = 3) -> NightlyReport:
    """`nightly`, returning the new nodes with the counts of what the pass skipped.

    A candidate's instances are its cluster's trace ids (the Arena task ids), so outcomes can be recorded on the
    failures that made it and it can leave `missing`. A trace id that cannot be an instance id is left out.
    """
    known = graph.nodes()
    skipped = Skipped()
    clusters = _cluster(steps, min_size, skipped)
    new = []
    for c in clusters:
        if explained(c, known):
            continue
        instances = [t for t in c.trace_ids if valid_id(t)]
        try:
            new.append(graph.add_node(c.candidate_id, task_family=c.family, verifier=NO_VERIFIER,
                                      instances=instances, state="missing"))
        except ValueError:  # registered meanwhile by another run
            continue
    return NightlyReport(new=new, clusters=len(clusters), skipped=skipped)


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
    report = nightly_report(CapabilityGraph(), steps_from_spans(records))
    for n in report.new:
        print(n.id)
    s = report.skipped
    print(f"{len(report.new)} new of {report.clusters} clusters; skipped {s.malformed_rows} malformed rows,"
          f" {s.empty_steps} empty steps, {s.capped_texts} texts over the cap", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
