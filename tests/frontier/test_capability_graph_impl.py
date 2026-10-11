"""E3 implementer's tests: what the exam leaves to the implementation (seed contents, trace wiring, the CLI)."""
import json
import sqlite3

import pytest

from tanishi.arena.practice import load_practice
from tanishi.frontier import clustering, graph

VERIFIER = "tanishi.arena.practice.checks.slugify_fn"


@pytest.fixture
def g(tmp_path):
    return graph.CapabilityGraph(tmp_path / "graph.db")


def test_seeds_cover_every_practice_task_once_with_its_family_and_verifier():
    tasks = {t.id: t for t in load_practice()}
    covered = [i for _, _, _, instances in graph.SEEDS for i in instances]
    assert sorted(covered) == sorted(tasks)
    for _, family, verifier, instances in graph.SEEDS:
        assert verifier == tasks[instances[0]].verifier
        # personality is the one Practice family folded into another node (conversation.voice)
        assert all(tasks[i].family in (family, "personality") for i in instances)


def test_seed_returns_the_twelve_and_keeps_a_user_node_with_a_seed_id(g):
    g.add_node("memory.recall", task_family="mine", verifier=VERIFIER)
    assert len(g.seed()) == 12
    assert g.node("memory.recall").task_family == "mine"


def test_no_verifier_never_scores():
    assert graph.no_verifier(None, "anything")[0] == 0.0


def test_missing_candidate_turns_weak_once_evidence_arrives(g):
    g.add_node("m", task_family="f", verifier=VERIFIER, instances=["i0"], state="missing")
    assert g.refresh("m").state == "missing"
    assert g.record_outcome("m", "i0", passed=False).state == "weak"


def test_correlated_is_kept_while_there_is_no_evidence(g):
    g.add_node("c", task_family="f", verifier=VERIFIER, instances=["i0"], state="correlated")
    assert g.refresh("c").state == "correlated"


def test_record_outcome_updates_without_refresh(g):
    g.add_node("a", task_family="f", verifier=VERIFIER, instances=["i0"])
    g.record_outcome("a", "i0", passed=True)
    n = g.node("a")
    assert (n.evidence, n.observed_rate, n.state) == (1, 1.0, "weak")
    assert n.ability == pytest.approx(2 / 3)


@pytest.mark.parametrize("bad", ["a\x00b", " a", "a\n", "x" * 201, " "])
def test_node_ids_must_be_printable(g, bad):
    with pytest.raises(ValueError):
        g.add_node(bad, task_family="f", verifier=VERIFIER)


def test_instances_must_be_a_list_not_a_string(g):
    with pytest.raises(TypeError):
        g.add_node("a", task_family="f", verifier=VERIFIER, instances="abc")


def test_legacy_database_is_refused(tmp_path):
    with pytest.raises(ValueError):
        graph.CapabilityGraph(tmp_path / "tanishi.db")


def test_tables_check_their_own_values(tmp_path):
    graph.CapabilityGraph(tmp_path / "g.db").add_node("a", task_family="f", verifier=VERIFIER)
    conn = sqlite3.connect(tmp_path / "g.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE capability_nodes SET state = 'great'")
    conn.close()


def _span(sid, parent, status, ts, task="t1", name="tool", error=None):
    return {"span_id": sid, "parent_id": parent, "task_id": task, "name": name, "status": status,
            "error": error, "start_ts": ts}


def test_steps_from_spans_gives_one_step_per_failed_task():
    records = [
        _span("a", None, "error", 1.0, error="Failed", name="task"),
        _span("b", "a", "error", 2.0, error="TimeoutError", name="tool.read_file"),
        _span("c", None, "ok", 1.0, task="t2"),
        _span("d", None, "error", 1.0, task=None, error="X"),
    ]
    assert clustering.steps_from_spans(records, families={"t1": "tool_use"}) == [
        {"trace_id": "t1", "text": "tool.read_file TimeoutError", "family": "tool_use"}]


def test_decisive_step_survives_a_parent_loop_and_bad_timestamps():
    spans = [_span("a", "b", "error", "x"), _span("b", "a", "error", None)]
    assert clustering.decisive_step(spans)["span_id"] in ("a", "b")


def test_same_tool_different_error_classes_stay_apart():
    steps = [{"trace_id": f"{e}{k}", "text": f"tool.read_file {e}", "family": "tool_use"}
             for e in ("TimeoutError", "KeyError") for k in range(3)]
    assert sorted(c.size for c in clustering.cluster_failures(steps)) == [3, 3]


def test_candidate_id_is_printable_whatever_the_family():
    c = clustering.Cluster(family="\x1b[31m; DROP {{x}}", trace_ids=("a",), texts=("t",))
    assert c.candidate_id.startswith("missing.")
    assert c.candidate_id.isprintable() and " " not in c.candidate_id


def test_nightly_skips_steps_without_a_trace_id(g):
    steps = [{"trace_id": None, "text": "x", "family": "f"}] * 5 + [{"text": "x"}] * 5
    assert clustering.nightly(g, steps) == []


def test_unassigned_family_candidate_is_idempotent(g):
    steps = [{"trace_id": f"t{k}", "text": "think RateLimitError"} for k in range(4)]
    [new] = clustering.nightly(g, steps)
    assert new.task_family == clustering.UNASSIGNED and new.state == "missing"
    assert new.verifier == graph.NO_VERIFIER
    assert clustering.nightly(g, steps) == []


def test_cli_reads_one_days_traces(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TANISHI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(tmp_path / "cs.db"))
    traces = tmp_path / "home" / "traces"
    traces.mkdir(parents=True)
    lines = [json.dumps(_span(f"s{k}", None, "error", 1.0, task=f"t{k}", error="TimeoutError")) for k in range(3)]
    (traces / "2026-10-11.jsonl").write_text("\n".join(lines + ["not json", ""]))
    assert clustering.main(["2026-10-11"]) == 0
    assert capsys.readouterr().out.startswith("missing.unassigned.")
    assert clustering.main(["2026-10-11"]) == 0
    assert capsys.readouterr().out == ""
    assert clustering.main(["../etc"]) == 2
