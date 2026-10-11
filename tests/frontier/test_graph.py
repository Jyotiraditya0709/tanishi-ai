"""E3 exam: Capability Graph and nightly failure clustering. Written by the testing agent from the spec card alone.

The card names no function signatures. Every name below is an assumption, listed as A1..A14 in
build/memory/open-problems/E3-exam-assumptions.md. If the implementer disagrees, say so in
build/memory/open-problems/E3-exam-conflicts.md and the human decides; do not edit the tests.
"""
import importlib
import itertools
import random

import pytest
from tanishi.frontier import clustering, graph

FIVE_STATES = ("mastered", "weak", "missing", "correlated", "unexplored")
THREE_KINDS = ("requires", "fails_together", "composes_into")
VERIFIER = "tanishi.arena.practice.checks.slugify_fn"


@pytest.fixture
def g(tmp_path):
    return graph.CapabilityGraph(tmp_path / "graph.db")


def _node(g, nid, n=10, family=None, **kw):
    return g.add_node(nid, task_family=family or f"family_{nid}", verifier=VERIFIER,
                      instances=[f"{nid}-i{k}" for k in range(n)], **kw)


def _fill(g, nid, passes, n, predicted):
    """Record n outcomes (the first `passes` pass) on node nid's sealed instances and set her predicted rate."""
    for k in range(n):
        g.record_outcome(nid, f"{nid}-i{k}", passed=k < passes)
    if predicted is not None:
        g.set_predicted_rate(nid, predicted)
    return g.refresh(nid)


# ---------------------------------------------------------------- acceptance 1: what a node has

def test_states_and_edge_kinds_are_exactly_the_spec_vocabulary():
    assert tuple(graph.STATES) == FIVE_STATES
    assert tuple(graph.EDGE_KINDS) == THREE_KINDS


def test_node_has_family_verifier_instances_state_ability_confidence(g):
    n = _node(g, "a", n=4, family="coding")
    assert n.id == "a"
    assert n.task_family == "coding"
    assert n.verifier == VERIFIER
    assert list(n.instances) == [f"a-i{k}" for k in range(4)]
    assert n.state in FIVE_STATES
    assert 0.0 <= n.ability <= 1.0
    assert 0.0 <= n.confidence <= 1.0


def test_new_node_with_no_evidence_is_unexplored_with_zero_confidence(g):
    n = _node(g, "a")
    assert n.state == "unexplored"
    assert n.confidence == 0


@pytest.mark.parametrize("state", FIVE_STATES)
def test_every_listed_state_can_be_stored(g, state):
    assert _node(g, "a", state=state).state == state
    assert g.node("a").state == state


@pytest.mark.parametrize("bad", ["", "Mastered", "done", "strong", None, 3])
def test_unknown_state_is_refused(g, bad):
    with pytest.raises((ValueError, TypeError)):
        _node(g, "a", state=bad)
    assert g.nodes() == []


@pytest.mark.parametrize("field,bad", [("task_family", ""), ("task_family", "  "), ("verifier", ""),
                                       ("verifier", "nodots"), ("task_family", None), ("verifier", 5)])
def test_node_needs_a_family_and_a_dotted_verifier(g, field, bad):
    kw = {"task_family": "coding", "verifier": VERIFIER, "instances": ["i0"]}
    kw[field] = bad
    with pytest.raises((ValueError, TypeError)):
        g.add_node("a", **kw)
    assert g.nodes() == []


def test_duplicate_node_id_is_refused_and_keeps_the_first(g):
    _node(g, "a", family="coding")
    with pytest.raises(ValueError):
        _node(g, "a", family="reasoning")
    assert g.node("a").task_family == "coding"


def test_unknown_node_raises_keyerror(g):
    with pytest.raises(KeyError):
        g.node("nope")


def test_duplicate_instance_ids_are_refused(g):
    with pytest.raises(ValueError):
        g.add_node("a", task_family="coding", verifier=VERIFIER, instances=["x", "x"])


def test_outcome_on_an_instance_the_node_does_not_own_is_refused(g):
    _node(g, "a", n=2)
    _node(g, "b", n=2)
    with pytest.raises((KeyError, ValueError)):
        g.record_outcome("a", "b-i0", passed=True)
    assert g.refresh("a").state == "unexplored"


def test_outcome_needs_a_real_bool(g):
    _node(g, "a", n=2)
    for bad in (1, 0, "yes", None):
        with pytest.raises((TypeError, ValueError)):
            g.record_outcome("a", "a-i0", passed=bad)


@pytest.mark.parametrize("bad", [-0.1, 1.1, float("nan"), float("inf"), "0.5", True])
def test_predicted_rate_must_be_a_number_in_unit_interval(g, bad):
    _node(g, "a")
    with pytest.raises((TypeError, ValueError)):
        g.set_predicted_rate("a", bad)


def test_nodes_and_outcomes_persist_across_reopen(tmp_path):
    path = tmp_path / "graph.db"
    g1 = graph.CapabilityGraph(path)
    _node(g1, "a", n=10, family="coding")
    _fill(g1, "a", 9, 10, 0.9)
    g1.add_node("b", task_family="reasoning", verifier=VERIFIER, instances=["b0"])
    g1.add_edge("a", "b", "composes_into")
    before = g1.node("a")

    g2 = graph.CapabilityGraph(path)
    after = g2.node("a")
    assert (after.task_family, after.state, after.ability, after.confidence) == \
           (before.task_family, before.state, before.ability, before.confidence)
    assert sorted(n.id for n in g2.nodes()) == ["a", "b"]
    assert [(e.src, e.dst, e.kind) for e in g2.edges()] == [("a", "b", "composes_into")]


def test_default_db_follows_core_state_env_not_the_real_home(tmp_path, monkeypatch):
    path = tmp_path / "cs.db"
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(path))
    graph.CapabilityGraph().add_node("a", task_family="coding", verifier=VERIFIER, instances=["i"])
    assert path.exists()
    assert [n.id for n in graph.CapabilityGraph().nodes()] == ["a"]


# ---------------------------------------------------------------- acceptance 1 (cont.): seeds

def test_seed_creates_exactly_twelve_distinct_capabilities(g):
    g.seed()
    nodes = g.nodes()
    assert len(nodes) == 12
    assert len({n.id for n in nodes}) == 12
    assert len({n.task_family for n in nodes}) >= 6, "twelve capabilities, not twelve copies of one"


def test_every_seed_has_family_verifier_state_ability_confidence(g):
    g.seed()
    for n in g.nodes():
        assert n.task_family.strip()
        assert "." in n.verifier.strip(".")
        assert n.state in FIVE_STATES
        assert 0.0 <= n.ability <= 1.0
        assert 0.0 <= n.confidence <= 1.0


def test_seed_verifiers_resolve_to_callables(g):
    g.seed()
    for n in g.nodes():
        mod, _, fn = n.verifier.rpartition(".")
        assert callable(getattr(importlib.import_module(mod), fn)), n.verifier


def test_no_seed_starts_mastered(g):
    g.seed()
    assert all(n.state != "mastered" for n in g.nodes())


def test_seed_is_idempotent(g):
    g.seed()
    g.seed()
    assert len(g.nodes()) == 12


def test_seed_does_not_overwrite_evidence(g):
    g.seed()
    nid = g.nodes()[0].id
    n = g.node(nid)
    if not n.instances:
        pytest.skip("seed carries no sealed instances yet; nothing to record against")
    g.record_outcome(nid, n.instances[0], passed=False)
    g.seed()
    assert g.refresh(nid).confidence > 0


def test_seed_edges_use_only_the_three_kinds_and_known_nodes(g):
    g.seed()
    ids = {n.id for n in g.nodes()}
    for e in g.edges():
        assert e.kind in THREE_KINDS
        assert e.src in ids and e.dst in ids
        assert e.src != e.dst


# ---------------------------------------------------------------- acceptance 2: edges

def test_add_edge_roundtrip_all_three_kinds(g):
    for i in "abcd":
        _node(g, i)
    g.add_edge("a", "b", "requires")
    g.add_edge("b", "c", "composes_into")
    g.add_edge("c", "d", "fails_together")
    got = {(e.src, e.dst, e.kind) for e in g.edges()}
    assert ("a", "b", "requires") in got
    assert ("b", "c", "composes_into") in got
    assert {(s, d) for s, d, k in got if k == "fails_together"} in ({("c", "d")}, {("d", "c")})


def test_edges_can_be_filtered_by_kind(g):
    for i in "abc":
        _node(g, i)
    g.add_edge("a", "b", "requires")
    g.add_edge("b", "c", "composes_into")
    assert [e.kind for e in g.edges(kind="requires")] == ["requires"]
    assert [e.kind for e in g.edges(kind="composes_into")] == ["composes_into"]
    assert g.edges(kind="fails_together") == []


@pytest.mark.parametrize("bad", ["", "depends", "REQUIRES", "fails-together", None])
def test_unknown_edge_kind_is_refused(g, bad):
    _node(g, "a")
    _node(g, "b")
    with pytest.raises((ValueError, TypeError)):
        g.add_edge("a", "b", bad)
    assert g.edges() == []


def test_edge_to_unknown_node_is_refused(g):
    _node(g, "a")
    with pytest.raises(KeyError):
        g.add_edge("a", "ghost", "requires")
    with pytest.raises(KeyError):
        g.add_edge("ghost", "a", "requires")
    assert g.edges() == []


@pytest.mark.parametrize("kind", THREE_KINDS)
def test_self_edge_is_refused(g, kind):
    _node(g, "a")
    with pytest.raises(ValueError):
        g.add_edge("a", "a", kind)


def test_duplicate_edge_is_stored_once(g):
    _node(g, "a")
    _node(g, "b")
    g.add_edge("a", "b", "requires")
    g.add_edge("a", "b", "requires")
    assert len(g.edges()) == 1


def test_fails_together_is_symmetric(g):
    _node(g, "a")
    _node(g, "b")
    g.add_edge("a", "b", "fails_together")
    g.add_edge("b", "a", "fails_together")
    assert len(g.edges(kind="fails_together")) == 1


def test_requires_is_directed(g):
    _node(g, "a")
    _node(g, "b")
    g.add_edge("a", "b", "requires")
    g.add_edge("b", "a", "composes_into")  # a different kind between the same pair is fine
    assert {(e.src, e.dst, e.kind) for e in g.edges()} == {("a", "b", "requires"), ("b", "a", "composes_into")}


def test_requires_cycle_is_refused(g):
    for i in "abc":
        _node(g, i)
    g.add_edge("a", "b", "requires")
    g.add_edge("b", "c", "requires")
    with pytest.raises(ValueError):
        g.add_edge("c", "a", "requires")
    assert len(g.edges(kind="requires")) == 2


def test_requirements_are_transitive(g):
    for i in "abcd":
        _node(g, i)
    g.add_edge("a", "b", "requires")
    g.add_edge("b", "c", "requires")
    g.add_edge("a", "d", "composes_into")  # not a requirement
    assert set(g.requirements("a")) == {"b", "c"}
    assert set(g.requirements("c")) == set()


# ---------------------------------------------------------------- acceptance 4: mastered

def test_mastered_at_ninety_percent_with_calibrated_prediction(g):
    _node(g, "a", n=10)
    assert _fill(g, "a", passes=9, n=10, predicted=0.9).state == "mastered"


def test_ninety_percent_but_prediction_eleven_points_off_is_not_mastered(g):
    _node(g, "a", n=10)
    assert _fill(g, "a", passes=9, n=10, predicted=0.79).state != "mastered"


def test_prediction_exactly_ten_points_off_still_counts_as_within(g):
    _node(g, "a", n=10)
    assert _fill(g, "a", passes=9, n=10, predicted=0.8).state == "mastered"
    _node(g, "b", n=10)
    assert _fill(g, "b", passes=10, n=10, predicted=0.9).state == "mastered"


def test_overconfident_perfect_scorer_is_not_mastered(g):
    _node(g, "a", n=10)
    assert _fill(g, "a", passes=10, n=10, predicted=0.5).state != "mastered"


def test_underconfident_prediction_also_breaks_the_calibration_clause(g):
    _node(g, "a", n=10)
    assert _fill(g, "a", passes=10, n=10, predicted=0.3).state != "mastered"


def test_eighty_nine_percent_is_not_mastered_even_if_calibrated(g):
    _node(g, "a", n=100)
    assert _fill(g, "a", passes=89, n=100, predicted=0.89).state != "mastered"
    _node(g, "b", n=100)
    assert _fill(g, "b", passes=90, n=100, predicted=0.9).state == "mastered"


def test_good_score_with_no_prediction_is_not_mastered(g):
    _node(g, "a", n=10)
    assert _fill(g, "a", passes=10, n=10, predicted=None).state != "mastered"


def test_low_score_with_evidence_is_weak(g):
    _node(g, "a", n=10)
    assert _fill(g, "a", passes=3, n=10, predicted=0.3).state == "weak"


def test_mastery_is_lost_when_failures_arrive(g):
    _node(g, "a", n=20)
    assert _fill(g, "a", passes=19, n=20, predicted=0.95).state == "mastered"
    for k in range(10):  # re-run the first ten instances: they now fail
        g.record_outcome("a", f"a-i{k}", passed=False)
    assert g.refresh("a").state != "mastered"


@pytest.mark.parametrize("observed,predicted,n,expected", [
    (1.0, 1.0, 10, "mastered"),
    (0.9, 0.9, 10, "mastered"),
    (0.9, 0.8, 10, "mastered"),
    (0.95, 0.85, 20, "mastered"),
    (0.9, 0.75, 10, "weak"),
    (1.0, 0.85, 10, "weak"),
    (0.89, 0.89, 100, "weak"),
    (0.5, 0.5, 10, "weak"),
    (0.0, 0.0, 10, "weak"),
    (0.0, None, 0, "unexplored"),
])
def test_state_for_table(observed, predicted, n, expected):
    assert graph.state_for(observed, predicted, n) == expected


def test_state_for_matches_the_spec_formula_on_a_grid():
    rates = [i / 20 for i in range(21)]
    for obs, pred in itertools.product(rates, rates):
        want = "mastered" if (obs >= 0.9 and round(abs(pred - obs), 9) <= 0.1) else "weak"
        assert graph.state_for(obs, pred, 20) == want, (obs, pred)


@pytest.mark.parametrize("bad", [float("nan"), -0.1, 1.5, "0.9", None])
def test_state_for_refuses_bad_observed_rate(bad):
    with pytest.raises((TypeError, ValueError)):
        graph.state_for(bad, 0.9, 10)


# ---------------------------------------------------------------- ability and confidence

def test_ability_rises_with_passes(g):
    prev = -1.0
    for passes in range(11):
        nid = f"n{passes}"
        _node(g, nid, n=10)
        a = _fill(g, nid, passes, 10, None).ability
        assert a >= prev, (passes, a, prev)
        prev = a
    assert g.node("n10").ability > g.node("n0").ability


def test_ability_is_on_the_pass_rate_scale(g):
    _node(g, "lo", n=10)
    _node(g, "hi", n=10)
    assert _fill(g, "lo", 1, 10, None).ability < 0.5 < _fill(g, "hi", 9, 10, None).ability


def test_more_evidence_at_the_same_rate_gives_more_confidence(g):
    _node(g, "few", n=4)
    _node(g, "many", n=80)
    few = _fill(g, "few", 2, 4, None).confidence
    many = _fill(g, "many", 40, 80, None).confidence
    assert 0 < few < many <= 1.0


def test_confidence_never_decreases_as_outcomes_accumulate(g):
    _node(g, "a", n=30)
    rng = random.Random(7)
    last = 0.0
    for k in range(30):
        g.record_outcome("a", f"a-i{k}", passed=rng.random() < 0.6)
        c = g.refresh("a").confidence
        assert c >= last - 1e-12
        last = c


def test_refresh_is_idempotent(g):
    _node(g, "a", n=10)
    _fill(g, "a", 9, 10, 0.9)
    one = g.refresh("a")
    two = g.refresh("a")
    assert (one.state, one.ability, one.confidence) == (two.state, two.ability, two.confidence)


def test_results_on_one_node_do_not_touch_another(g):
    _node(g, "a", n=10)
    _node(g, "b", n=10)
    _fill(g, "a", 10, 10, 1.0)
    assert g.refresh("b").state == "unexplored"
    assert g.node("b").confidence == 0


def test_mastered_requires_both_clauses_property(g):
    rng = random.Random(20261011)
    for i in range(40):
        n = rng.choice([10, 20, 50])
        passes = rng.randint(0, n)
        pred = round(rng.random(), 2)
        nid = f"p{i}"
        _node(g, nid, n=n)
        got = _fill(g, nid, passes, n, pred).state
        want_master = passes / n >= 0.9 and round(abs(pred - passes / n), 9) <= 0.1
        assert (got == "mastered") == want_master, (passes, n, pred, got)
        assert got in ("mastered", "weak")


# ---------------------------------------------------------------- acceptance 3: nightly clustering

def _steps(prefix, text, count, family="tool_use"):
    return [{"trace_id": f"{prefix}{k}", "text": text, "family": family} for k in range(count)]


SHELL = "shell tool timeout waiting for output"
JSONY = "json parse error unexpected token reply"
SQLY = "sql syntax error near select join"


def test_decisive_step_is_the_earliest_error_span():
    spans = [
        {"span_id": "s3", "parent_id": "s0", "name": "tool", "status": "error", "error": "Timeout", "start_ts": 30.0},
        {"span_id": "s1", "parent_id": "s0", "name": "think", "status": "ok", "error": None, "start_ts": 10.0},
        {"span_id": "s2", "parent_id": "s0", "name": "tool", "status": "error", "error": "KeyError", "start_ts": 20.0},
        {"span_id": "s0", "parent_id": None, "name": "task", "status": "error", "error": "Failed", "start_ts": 5.0},
    ]
    # The root errors only because a child did; the decisive step is the earliest error with no erroring child.
    assert clustering.decisive_step(spans)["span_id"] == "s2"


def test_decisive_step_of_a_clean_trace_is_none():
    spans = [{"span_id": "s1", "parent_id": None, "name": "think", "status": "ok", "error": None, "start_ts": 1.0}]
    assert clustering.decisive_step(spans) is None
    assert clustering.decisive_step([]) is None


def test_decisive_step_does_not_depend_on_list_order():
    spans = [{"span_id": f"s{k}", "parent_id": "r", "name": "tool", "status": "error" if k % 2 else "ok",
              "error": f"E{k}", "start_ts": float(k)} for k in range(1, 9)]
    want = clustering.decisive_step(spans)["span_id"]
    rng = random.Random(3)
    for _ in range(10):
        rng.shuffle(spans)
        assert clustering.decisive_step(spans)["span_id"] == want


def test_dense_group_of_similar_steps_is_one_cluster():
    found = clustering.cluster_failures(_steps("t", SHELL, 5), min_size=3)
    assert len(found) == 1
    assert found[0].size == 5
    assert set(found[0].trace_ids) == {f"t{k}" for k in range(5)}


def test_sparse_unrelated_failures_make_no_cluster():
    steps = [
        {"trace_id": "a", "text": SHELL, "family": "tool_use"},
        {"trace_id": "b", "text": JSONY, "family": "instruction"},
        {"trace_id": "c", "text": SQLY, "family": "coding"},
    ]
    assert clustering.cluster_failures(steps, min_size=3) == []


def test_two_distinct_dense_groups_make_two_clusters():
    steps = _steps("s", SHELL, 4) + _steps("j", JSONY, 3, family="instruction")
    found = sorted(clustering.cluster_failures(steps, min_size=3), key=lambda c: -c.size)
    assert [c.size for c in found] == [4, 3]
    assert set(found[0].trace_ids).isdisjoint(found[1].trace_ids)


def test_a_cluster_below_min_size_is_not_reported():
    assert clustering.cluster_failures(_steps("t", SHELL, 2), min_size=3) == []
    assert len(clustering.cluster_failures(_steps("t", SHELL, 3), min_size=3)) == 1


def test_one_trace_counts_once_however_many_steps_it_repeats():
    steps = [{"trace_id": "same", "text": SHELL, "family": "tool_use"} for _ in range(6)]
    assert clustering.cluster_failures(steps, min_size=3) == []


def test_empty_input_gives_no_clusters():
    assert clustering.cluster_failures([], min_size=3) == []


@pytest.mark.parametrize("bad", [0, -1, 1.5, None, True])
def test_min_size_must_be_a_positive_int(bad):
    with pytest.raises((TypeError, ValueError)):
        clustering.cluster_failures(_steps("t", SHELL, 3), min_size=bad)


def test_cluster_result_does_not_depend_on_input_order():
    steps = _steps("s", SHELL, 4) + _steps("j", JSONY, 3, family="instruction") + _steps("q", SQLY, 1, "coding")
    want = sorted(sorted(c.trace_ids) for c in clustering.cluster_failures(steps, min_size=3))
    assert want  # a stub that returns nothing must not pass an order-invariance check
    rng = random.Random(11)
    for _ in range(15):
        rng.shuffle(steps)
        got = sorted(sorted(c.trace_ids) for c in clustering.cluster_failures(steps, min_size=3))
        assert got == want


def test_every_trace_lands_in_at_most_one_cluster():
    steps = _steps("s", SHELL, 4) + _steps("j", JSONY, 4, "instruction") + _steps("m", SHELL + " retry", 3)
    found = clustering.cluster_failures(steps, min_size=3)
    assert found
    seen = [t for c in found for t in c.trace_ids]
    assert len(seen) == len(set(seen))


def test_nightly_registers_an_unexplained_dense_cluster_as_missing_candidate(g):
    _node(g, "known", family="coding")
    found = clustering.nightly(g, _steps("t", SHELL, 5, family="tool_use"), min_size=3)
    assert len(found) == 1
    new = [n for n in g.nodes() if n.id != "known"]
    assert len(new) == 1
    assert new[0].state == "missing"
    assert new[0].task_family  # a candidate still names a family to practise


def _steps_on(g, nid, text, count, family):
    """Failed attempts on the first `count` instances of node nid (the trace id is the Arena task id)."""
    return [{"trace_id": i, "text": text, "family": family} for i in g.node(nid).instances[:count]]


def test_nightly_leaves_a_cluster_a_node_already_explains(g):
    # A12 (human decision 2026-10-11): the failed tasks are the node's own instances and the node is weak or missing.
    _node(g, "tools", family="tool_use")
    assert _fill(g, "tools", passes=3, n=10, predicted=0.3).state == "weak"
    found = clustering.nightly(g, _steps_on(g, "tools", SHELL, 5, "tool_use"), min_size=3)
    assert found == []
    assert [n.id for n in g.nodes()] == ["tools"]


def test_nightly_dense_cluster_inside_a_mastered_nodes_family_becomes_a_missing_candidate(g):
    _node(g, "tools", family="tool_use")
    assert _fill(g, "tools", passes=10, n=10, predicted=1.0).state == "mastered"
    found = clustering.nightly(g, _steps_on(g, "tools", SHELL, 5, "tool_use"), min_size=3)
    assert len(found) == 1
    assert found[0].state == "missing"
    assert found[0].task_family == "tool_use"
    assert g.node("tools").state == "mastered"
    assert len(g.nodes()) == 2


def test_nightly_is_idempotent_for_the_same_failures(g):
    steps = _steps("t", SHELL, 5, family="tool_use")
    clustering.nightly(g, steps, min_size=3)
    assert len(g.nodes()) == 1
    clustering.nightly(g, steps, min_size=3)
    assert len(g.nodes()) == 1


def test_nightly_once_a_candidate_exists_the_same_cluster_is_explained(g):
    steps = _steps("t", SHELL, 5, family="tool_use")
    assert len(clustering.nightly(g, steps, min_size=3)) == 1
    assert clustering.nightly(g, steps, min_size=3) == []


def test_nightly_with_no_failures_changes_nothing(g):
    _node(g, "a")
    assert clustering.nightly(g, [], min_size=3) == []
    assert [n.id for n in g.nodes()] == ["a"]


def test_nightly_candidate_ids_do_not_depend_on_input_order(tmp_path):
    steps = _steps("s", SHELL, 4) + _steps("j", JSONY, 3, family="instruction")
    ids = []
    for seed in (1, 2, 3):
        gg = graph.CapabilityGraph(tmp_path / f"g{seed}.db")
        random.Random(seed).shuffle(steps)
        clustering.nightly(gg, steps, min_size=3)
        ids.append(sorted(n.id for n in gg.nodes()))
    assert ids[0] == ids[1] == ids[2]
    assert len(ids[0]) == 2


def test_nightly_survives_text_that_looks_like_markup_or_control(g):
    nasty = "\x1b[31m'; DROP TABLE nodes;-- {{x}}   \x00"
    clustering.nightly(g, _steps("t", nasty, 4, family="tool_use"), min_size=3)
    assert all(isinstance(n.id, str) and "\x00" not in n.id for n in g.nodes())
    assert g.nodes()
