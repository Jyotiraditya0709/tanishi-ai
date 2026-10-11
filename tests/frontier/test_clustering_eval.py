"""E3 evaluation: does nightly clustering find planted failure modes, and only those?

Synthetic corpus shaped like real decisive steps (span name + error class, decision 0023 rule 10): eight planted
modes of 3..40 traces over three families, some spread over sibling tool spans, plus 300 one-off failures that reuse
the same span names with unique error classes. Scored by exact recovery of each planted mode, false clusters,
pairwise precision/recall and wall time. Results: build/memory/benchmarks/E3-clustering.md.
"""
import random
import time
from itertools import combinations

from tanishi.frontier import clustering, graph

TOOLS = ["tool.read_file", "tool.write_file", "tool.run_command", "tool.list_directory", "think", "verify"]
# (family, span names the mode shows up on, error class, traces)
MODES = [
    ("tool_use", ["tool.read_file"], "FileNotFoundError", 40),
    ("tool_use", ["tool.read_file"], "TimeoutError", 12),
    ("tool_use", ["tool.run_command"], "TimeoutError", 9),
    ("tool_use", ["tool.write_file", "tool.write_file retry"], "PermissionError", 7),
    ("coding", ["verify"], "AssertionError", 25),
    ("coding", ["think"], "JSONDecodeError", 5),
    ("reasoning", ["think"], "RateLimitError", 4),
    ("reasoning", ["verify"], "ValueError", 3),
]
NOISE = 300


def _corpus(seed):
    rng = random.Random(seed)
    steps, truth = [], {}
    for m, (family, names, error, count) in enumerate(MODES):
        for k in range(count):
            tid = f"m{m}-{k}"
            steps.append({"trace_id": tid, "text": f"{rng.choice(names)} {error}", "family": family})
            truth[tid] = m
    for k in range(NOISE):
        family = rng.choice(["tool_use", "coding", "reasoning"])
        steps.append({"trace_id": f"n{k}", "text": f"{rng.choice(TOOLS)} OneOffError{k}", "family": family})
    rng.shuffle(steps)
    return steps, truth


def evaluate(seed=0):
    steps, truth = _corpus(seed)
    t0 = time.perf_counter()
    found = clustering.cluster_failures(steps, min_size=3)
    seconds = time.perf_counter() - t0
    planted = {}
    for tid, m in truth.items():
        planted.setdefault(m, set()).add(tid)
    recovered = sum(any(set(c.trace_ids) == ts for c in found) for ts in planted.values())
    false = sum(not any(set(c.trace_ids) <= ts for ts in planted.values()) for c in found)
    pred = {frozenset(p) for c in found for p in combinations(c.trace_ids, 2)}
    gold = {frozenset(p) for ts in planted.values() for p in combinations(sorted(ts), 2)}
    return {"modes": len(MODES), "recovered": recovered, "false_clusters": false,
            "pair_precision": len(pred & gold) / len(pred), "pair_recall": len(pred & gold) / len(gold),
            "steps": len(steps), "seconds": seconds}


def test_planted_modes_are_recovered_exactly_and_noise_makes_no_cluster():
    for seed in range(5):
        r = evaluate(seed)
        assert r["recovered"] == r["modes"], r
        assert r["false_clusters"] == 0, r
        assert r["pair_precision"] == 1.0 and r["pair_recall"] == 1.0, r
        assert r["seconds"] < 2.0, r


def test_nightly_on_the_corpus_proposes_one_candidate_per_unexplained_mode(tmp_path):
    # A12: a weak node explains the tool_use modes, whose failed tasks are all its own instances.
    steps, truth = _corpus(1)
    g = graph.CapabilityGraph(tmp_path / "g.db")
    g.add_node("tools", task_family="tool_use", verifier=graph.NO_VERIFIER, state="weak",
               instances=sorted(tid for tid, m in truth.items() if MODES[m][0] == "tool_use"))
    new = clustering.nightly(g, steps)
    assert sorted(n.task_family for n in new) == ["coding", "coding", "reasoning", "reasoning"]
    assert clustering.nightly(g, steps) == []


def test_nightly_on_the_corpus_ignores_family_without_a_failing_owner(tmp_path):
    # The same tool_use node, unexplored: its family alone explains nothing, so all eight modes are candidates.
    steps, truth = _corpus(1)
    g = graph.CapabilityGraph(tmp_path / "g.db")
    g.add_node("tools", task_family="tool_use", verifier=graph.NO_VERIFIER,
               instances=sorted(tid for tid, m in truth.items() if MODES[m][0] == "tool_use"))
    assert len(clustering.nightly(g, steps)) == len(MODES)


if __name__ == "__main__":
    for s in range(5):
        print(evaluate(s))
