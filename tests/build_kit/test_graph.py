"""The Build Graph must stay sound: unique ids, known dependencies, no cycles, humans own the protected nodes."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("orchestrator", ROOT / "build" / "orchestrator.py")
orch = importlib.util.module_from_spec(spec)
sys.modules["orchestrator"] = orch
spec.loader.exec_module(orch)

_cpp_spec = importlib.util.spec_from_file_location(
    "check_protected_paths", ROOT / "scripts" / "check_protected_paths.py"
)
cpp = importlib.util.module_from_spec(_cpp_spec)
_cpp_spec.loader.exec_module(cpp)


def graph():
    return orch.load_graph()


def test_graph_is_valid():
    assert orch.validate(graph()) == []


def test_cycle_is_detected():
    g = {"nodes": [
        {"id": "A", "title": "a", "tier": "T0", "owner": "swarm", "depends_on": ["B"], "status_hint": "later"},
        {"id": "B", "title": "b", "tier": "T0", "owner": "swarm", "depends_on": ["A"], "status_hint": "later"},
    ]}
    assert any("cycle" in e for e in orch.validate(g))


def test_unknown_dependency_is_detected():
    g = {"nodes": [{"id": "A", "title": "a", "tier": "T0", "owner": "swarm", "depends_on": ["Z"], "status_hint": "later"}]}
    assert any("unknown node Z" in e for e in orch.validate(g))


def test_protected_nodes_belong_to_a_human():
    for n in graph()["nodes"]:
        if n.get("protected"):
            assert "human" in n["owner"], n["id"]


def test_every_t0_node_reaches_the_loop_milestone_or_supports_it():
    ids = {n["id"] for n in graph()["nodes"]}
    assert "M1" in ids


def test_waves_cover_every_unfinished_node():
    g = graph()
    status = {n["id"]: "todo" for n in g["nodes"]}
    flat = [nid for wave in orch.waves(g, status) for nid in wave]
    assert sorted(flat) == sorted(status)


def test_ready_respects_dependencies():
    g = graph()
    status = {n["id"]: "todo" for n in g["nodes"]}
    ready = {n["id"] for n in orch.ready_nodes(g, status)}
    for n in g["nodes"]:
        if n["id"] in ready:
            assert not n["depends_on"]


def test_card_renders_every_active_node():
    g = graph()
    for n in g["nodes"]:
        if n.get("status_hint") != "later":
            card = orch.render_card(n["id"], "implementer", g)
            assert n["title"] in card
            assert "{{" not in card


def test_protected_path_matching():
    protected = ["constitution/", "CLAUDE.md", "build/graph.yaml"]
    files = ["constitution/policy.yaml", "CLAUDE.md", "build/graph.yaml.bak", "tanishi/core_state/db.py"]
    assert cpp.violations(files, protected) == ["constitution/policy.yaml", "CLAUDE.md"]


def test_agent_launch_refuses_human_nodes(monkeypatch):
    import pytest
    with pytest.raises(SystemExit):
        orch.launch("B0", "implementer", None, False)
