"""Red-team exam for E3 (see build/memory/runs/redteam-E3-20261012.md). Each test proves one break.

They are xfail(strict=True): each fails today because the break is real. When a fix lands the test XPASSes, strict
mode turns that into an error, and the fixer removes the marker. Do not delete or weaken them.
"""
import time

import pytest

from tanishi.frontier import clustering, graph

VERIFIER = "tanishi.arena.practice.checks.slugify_fn"


@pytest.fixture
def g(tmp_path):
    return graph.CapabilityGraph(tmp_path / "graph.db")


def _steps(prefix, text, family, k=3):
    return [{"trace_id": f"{prefix}{i}", "text": text, "family": family} for i in range(k)]


# RT-1 (high) mastery on partial coverage ---------------------------------------------------------------------------

def test_mastered_needs_every_sealed_instance_scored(g):
    g.seed()
    first = g.node("tool.system").instances[0]
    g.record_outcome("tool.system", first, True)
    n = g.set_predicted_rate("tool.system", 1.0)
    assert n.evidence == 1 and len(n.instances) == 6
    assert n.state != "mastered"  # observed 1/1 = 100% of the *evaluated* 1/6; confidence is 0.09


# RT-2 (medium) mastery with no evidence ----------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="RT-2: add_node accepts state='mastered' with no outcome behind it")
def test_a_node_cannot_be_created_mastered_without_evidence(g):
    with pytest.raises(ValueError):
        g.add_node("x.skill", task_family="f", verifier=VERIFIER, instances=["i0"], state="mastered")


# RT-3 (medium) forecast written after the outcomes -----------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="RT-3: a forecast set after the evidence is not a forecast, yet it calibrates")
def test_a_forecast_made_after_the_outcomes_does_not_earn_mastery(g):
    g.add_node("x.skill", task_family="f", verifier=VERIFIER, instances=[f"i{k}" for k in range(10)])
    for k in range(10):
        g.record_outcome("x.skill", f"i{k}", True)
    assert g.node("x.skill").state == "weak"  # no forecast, no mastery
    n = g.set_predicted_rate("x.skill", 1.0)  # she reads the scoreboard and "predicts" it
    assert n.state != "mastered"


# RT-4 (medium) a missing candidate can never gain evidence ---------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="RT-4: candidates are created with no instances; record_outcome always raises")
def test_a_missing_candidate_can_receive_outcomes_for_the_failures_that_made_it(g):
    (cand,) = clustering.nightly(g, _steps("t", "tool.read_file Boom", "tool_use"))
    g.record_outcome(cand.id, "t0", True)  # the capability is practised, passes, and should leave `missing`


# RT-5 (medium) non-ASCII failures collapse into one cluster ---------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="RT-5: _normal drops every non-[a-z0-9_] character, so all such texts are ''")
def test_unrelated_non_ascii_failures_are_not_one_cluster(g):
    texts = ["工具 读取失败", "ツール 書き込みエラー", "инструмент таймаут"]
    steps = [{"trace_id": f"a{i}", "text": t, "family": "f"} for i, t in enumerate(texts)]
    assert clustering.cluster_failures(steps, min_size=3) == []


# RT-6 (medium) contentless steps become a capability ---------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="RT-6: three steps with no name and no error class register a candidate")
def test_steps_with_no_text_do_not_register_a_candidate(g):
    assert clustering.nightly(g, _steps("e", "", "f")) == []


# RT-7 (medium) a recovered error is counted as a failed task --------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="RT-7: steps_from_spans treats any error span as a failed task")
def test_a_retried_tool_error_under_a_successful_task_is_not_a_failure():
    recs = [
        {"task_id": "T", "span_id": "r", "parent_id": None, "status": "ok", "name": "think", "start_ts": 1.0,
         "error": None},
        {"task_id": "T", "span_id": "c", "parent_id": "r", "status": "error", "name": "tool.read_file",
         "start_ts": 2.0, "error": "TimeoutError"},
    ]
    assert clustering.steps_from_spans(recs) == []


# RT-8 (medium) quadratic linking -----------------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="RT-8: _components compares every pair of distinct texts in a family")
def test_clustering_a_nights_worth_of_distinct_failures_is_fast():
    steps = [{"trace_id": f"p{i}", "text": f"tool.run_command Err{i} w{i * 7919 % 100003}", "family": "f"}
             for i in range(8000)]
    t0 = time.perf_counter()
    clustering.cluster_failures(steps)
    assert time.perf_counter() - t0 < 2.0


# RT-9 (low) one bad row aborts the night ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="RT-9: a non-mapping row raises AttributeError out of cluster_failures")
def test_a_malformed_step_row_is_skipped_not_fatal():
    good = _steps("g", "tool.read_file KeyError", "f")
    got = clustering.cluster_failures([*good, None, "oops", 3])  # type: ignore[list-item]
    assert [c.size for c in got] == [3]


# RT-10 (low) verifier may name any module --------------------------------------------------------------------------

def test_verifier_must_live_in_the_tanishi_package(g):
    with pytest.raises(ValueError):
        g.add_node("evil", task_family="f", verifier="os.system")


# RT-11 (low) seed() is all-or-nothing over a user's edge -----------------------------------------------------------

def test_seed_survives_a_user_edge_that_conflicts_with_a_seed_edge(g):
    g.add_node("code.write_function", task_family="coding", verifier=VERIFIER)
    g.add_node("code.fix_bug", task_family="coding", verifier=VERIFIER)
    g.add_edge("code.write_function", "code.fix_bug", "requires")  # opposite of the seed edge
    assert len(g.seed()) == 12
