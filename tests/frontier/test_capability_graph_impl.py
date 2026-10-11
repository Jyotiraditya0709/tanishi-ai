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


def test_pass_rate_is_over_every_sealed_instance(g):
    g.add_node("a", task_family="f", verifier=VERIFIER, instances=["i0", "i1", "i2", "i3"])
    g.record_outcome("a", "i0", passed=True)
    n = g.record_outcome("a", "i1", passed=True)
    assert (n.evidence, n.observed_rate, n.state) == (2, 0.5, "weak")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A graph and the CS5 ledger in one Core State db, as in production."""
    from tanishi.core_state import predictions
    monkeypatch.setenv("TANISHI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(tmp_path / "cs.db"))
    gg = graph.CapabilityGraph()
    gg.add_node("x.skill", task_family="f", verifier=VERIFIER, instances=[f"i{k}" for k in range(10)])
    return gg, predictions


def _all_pass(gg):
    for k in range(10):
        gg.record_outcome("x.skill", f"i{k}", passed=True)
    return gg.node("x.skill")


def test_a_ledger_forecast_made_before_the_outcomes_is_trusted_and_earns_mastery(ledger):
    gg, predictions = ledger
    pid = predictions.predict("capability:x.skill", {"success": True}, 0.95)
    n = gg.forecast_from_ledger("x.skill", pid)
    assert (n.predicted_rate, n.forecast_id, n.forecast_trusted) == (0.95, pid, True)
    n = _all_pass(gg)
    assert n.forecast_trusted and n.state == "mastered"


def test_a_ledger_forecast_made_after_the_first_outcome_does_not_count(ledger):
    gg, predictions = ledger
    gg.record_outcome("x.skill", "i0", passed=True)
    pid = predictions.predict("capability:x.skill", {"success": True}, 1.0)
    _all_pass(gg)
    n = gg.forecast_from_ledger("x.skill", pid)
    assert not n.forecast_trusted and n.state == "weak"


def test_re_running_outcomes_does_not_make_a_late_forecast_early(ledger):
    gg, predictions = ledger
    gg.record_outcome("x.skill", "i0", passed=True)
    pid = predictions.predict("capability:x.skill", {"success": True}, 1.0)
    gg.forecast_from_ledger("x.skill", pid)
    assert _all_pass(gg).state == "weak"  # i0 is re-run after the forecast; the first outcome still came first


def test_a_direct_forecast_replaces_a_ledger_one_and_is_never_trusted(ledger):
    gg, predictions = ledger
    gg.forecast_from_ledger("x.skill", predictions.predict("capability:x.skill", {"success": True}, 0.9))
    n = gg.set_predicted_rate("x.skill", 0.9)
    assert (n.forecast_id, n.forecast_trusted) == (None, False)


def test_a_ledger_forecast_must_be_about_this_node(ledger):
    gg, predictions = ledger
    other = predictions.predict("tool:web_search", {"success": True}, 0.99)
    with pytest.raises(ValueError):
        gg.forecast_from_ledger("x.skill", other)
    with pytest.raises(LookupError):
        gg.forecast_from_ledger("x.skill", "pred_nope")
    assert gg.node("x.skill").predicted_rate is None


def test_a_ledger_forecast_of_failure_is_one_minus_confidence(ledger):
    gg, predictions = ledger
    pid = predictions.predict("capability:x.skill", {"success": False}, 0.75)
    assert gg.forecast_from_ledger("x.skill", pid).predicted_rate == pytest.approx(0.25)


def test_a_graph_without_a_ledger_has_no_predictions(g):
    g.add_node("a", task_family="f", verifier=VERIFIER, instances=["i0"])
    with pytest.raises(LookupError):
        g.forecast_from_ledger("a", "pred_x")


def test_an_older_graph_gains_the_forecast_columns(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE capability_nodes (id TEXT PRIMARY KEY NOT NULL, task_family TEXT NOT NULL,"
                 " verifier TEXT NOT NULL, instances TEXT NOT NULL, state TEXT NOT NULL, ability REAL NOT NULL,"
                 " confidence REAL NOT NULL, predicted_rate REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute("INSERT INTO capability_nodes VALUES ('a', 'f', ?, '[\"i0\"]', 'weak', 0.67, 0.09, NULL, 't', 't')",
                 (VERIFIER,))
    conn.execute("CREATE TABLE capability_outcomes (node_id TEXT, instance_id TEXT, passed INTEGER, ts TEXT,"
                 " PRIMARY KEY (node_id, instance_id))")
    conn.execute("INSERT INTO capability_outcomes VALUES ('a', 'i0', 1, '2026-10-11T00:00:00.000000+00:00')")
    conn.commit()
    conn.close()
    n = graph.CapabilityGraph(path).node("a")
    assert (n.evidence, n.forecast_id, n.forecast_trusted) == (1, None, False)
    first = sqlite3.connect(path).execute("SELECT first_outcome_at FROM capability_nodes").fetchone()[0]
    assert first == "2026-10-11T00:00:00.000000+00:00"


def test_seed_skips_a_seed_edge_that_a_user_edge_would_cycle_and_logs_it(g, caplog):
    g.add_node("reasoning.arithmetic", task_family="reasoning", verifier=VERIFIER)
    g.add_node("reasoning.dates", task_family="reasoning", verifier=VERIFIER)
    g.add_edge("reasoning.arithmetic", "reasoning.dates", "requires")
    with caplog.at_level("WARNING", logger="tanishi.frontier.graph"):
        assert len(g.seed()) == 12
    assert "reasoning.dates requires reasoning.arithmetic skipped" in caplog.text
    assert len(g.edges()) == len(graph.SEED_EDGES)  # five seed edges and the user's


@pytest.mark.parametrize("bad", ["os.system", "subprocess.run", "tanishix.mod.fn", "tanishi"])
def test_verifier_must_be_inside_the_tanishi_package(g, bad):
    with pytest.raises(ValueError):
        g.add_node("a", task_family="f", verifier=bad)


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
