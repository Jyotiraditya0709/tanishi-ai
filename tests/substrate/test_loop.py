"""SUB2 exam: the reasoning loop, written from the spec alone.

Spec interface:
    CognitiveLoop(router, memory, tools, budget: StepBudget).run(task) -> Result
    enabled when TANISHI_SUBSTRATE=1; TanishiBrain.think() delegates to it
Acceptance:
    1. Every act step writes a prediction first (CS5) and an event (CS2).
    2. The loop stops at the step budget and reports why it stopped.
    3. With the flag off, every existing Practice task scores the same as before (regression test).
    4. The loop's program (which steps, in what order, with what budgets) is data in a versioned file.

The spec fixes no name for StepBudget's fields, Result's fields, how the loop calls a model, how a model asks for a
tool, or the program file's shape. The assumptions are listed in build/memory/open-problems/SUB2-exam-assumptions.md
(L1-L12) and each one lives in a constant or a helper at the top of this file, so a different spelling is a one-line
change. Do not weaken a test.
"""
import asyncio
import importlib
import json
import random
import subprocess
from pathlib import Path
from typing import ClassVar

import pytest

from tanishi.config import models as M
from tanishi.core_state import events, open_db
from tanishi.substrate.router import RouteContext, RouteDecision, RouteRefused
from tanishi.substrate.state import Step
from tanishi.tools.registry import ToolResult

REPO_ROOT = Path(__file__).resolve().parents[2]

# --- assumed spellings (SUB2-exam-assumptions.md) ----------------------------------------------------------------
BUDGET_KW = "max_steps"                    # L2
STOP_BUDGET = "budget"                     # L3
GENERATE_KW = "generate"                   # L5
PHASES = ["attend", "recall", "deliberate", "predict", "act", "reflect"]  # L10
PHASE_KEY = "step"                         # L11
LEDGER_KINDS = {"prediction", "prediction_resolved"}
FLAG = "TANISHI_SUBSTRATE"
SECRET = "sk-ant-EXAMSECRET1234567890abcdef"


def act(tool, **tool_input):               # L6
    return json.dumps({"action": {"tool": tool, "input": tool_input}})


# ------------------------------------------------------------------------------------------------ fakes


@pytest.fixture
def loop():
    return importlib.import_module("tanishi.substrate.loop")


class Runaway(AssertionError):
    pass


class Model:
    """A scripted `generate`: pops one reply per call; repeats `then` when the script runs out."""

    def __init__(self, script=(), then="the final answer", limit=300):
        self.script, self.then, self.limit = list(script), then, limit
        self.calls: list[tuple[RouteDecision, str]] = []

    async def __call__(self, decision, prompt):
        self.calls.append((decision, prompt))
        if len(self.calls) > self.limit:
            raise Runaway("the loop kept calling the model past any sane bound")
        return self.script.pop(0) if self.script else self.then


class Router:
    def __init__(self, model_id=M.CLAUDE_SIMPLE, refuse=None):
        self.model_id, self.refuse, self.calls = model_id, refuse, []

    def route(self, step, context):
        self.calls.append((step, context))
        if self.refuse:
            raise self.refuse
        return RouteDecision(self.model_id, "anthropic", "exam router")


class Memory:
    def __init__(self, marker="REMEMBERED-MARKER-7731", fail=False):
        self.marker, self.fail, self.queries = marker, fail, []

    def _hit(self, query):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("memory is down")
        return [type("Hit", (), {"content": self.marker, "id": "m1"})()]

    def recall(self, query, limit=5):
        return self._hit(query)

    def search(self, query, top_k=5):
        return self._hit(query)


class Tools:
    """Records each execute() with a snapshot of the ledger and event log taken at call time."""

    tools: ClassVar[dict] = {}

    def __init__(self, succeed=True, outcomes=None):
        self.succeed, self.outcomes = succeed, list(outcomes or [])
        self.calls: list[tuple[str, dict]] = []
        self.at_call: list[dict] = []

    def get_claude_tools(self):
        return []

    async def execute(self, tool_name, tool_input, actor="tanishi", session_id=None):
        self.at_call.append({"predictions": predictions(), "kinds": [e.kind for e in events.iter_events()]})
        self.calls.append((tool_name, tool_input))
        ok = self.outcomes.pop(0) if self.outcomes else self.succeed
        return ToolResult(success=ok, output="ok" if ok else "", tool_name=tool_name, error="" if ok else "boom")


def predictions():
    conn = open_db()
    try:
        rows = conn.execute("SELECT id, about, expected, confidence, actual, resolved_at FROM predictions "
                            "ORDER BY ts, rowid").fetchall()
    finally:
        conn.close()
    return [{"id": r[0], "about": r[1], "expected": json.loads(r[2]), "confidence": r[3],
             "actual": None if r[4] is None else json.loads(r[4]), "resolved_at": r[5]} for r in rows]


def all_events():
    return list(events.iter_events())


def make(loop, *, model=None, router=None, memory=None, tools=None, max_steps=20):
    kwargs = {GENERATE_KW: model or Model()}
    budget = loop.StepBudget(**{BUDGET_KW: max_steps})
    return loop.CognitiveLoop(router or Router(), memory or Memory(), tools or Tools(), budget, **kwargs)


def run(cl, task="what is in the config file?"):
    return asyncio.run(cl.run(task))


def _use_program(loop, monkeypatch, tmp_path, steps, version=1):
    p = tmp_path / "program.json"
    p.write_text(json.dumps({"version": version, "steps": steps}))
    monkeypatch.setattr(loop, "PROGRAM_PATH", p)


# ------------------------------------------------------------------------------------------------ the flag


@pytest.mark.parametrize("value,expected", [("1", True), ("0", False), ("", False)])
def test_enabled_reads_the_flag(loop, monkeypatch, value, expected):
    monkeypatch.setenv(FLAG, value)
    assert loop.enabled() is expected


def test_enabled_is_off_when_unset(loop, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert loop.enabled() is False


def test_enabled_is_read_per_call(loop, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    assert loop.enabled() is True
    monkeypatch.setenv(FLAG, "0")
    assert loop.enabled() is False


# ------------------------------------------------------------------------------------------------ run and stop


def test_run_is_a_coroutine_returning_a_result(loop):
    import inspect
    assert inspect.iscoroutinefunction(loop.CognitiveLoop.run)
    res = run(make(loop, model=Model(then="42 is the answer")))
    assert isinstance(res, loop.Result)
    assert res.content == "42 is the answer"
    assert res.success is True
    assert isinstance(res.stop_reason, str) and res.stop_reason and res.stop_reason != STOP_BUDGET
    assert isinstance(res.steps, int) and res.steps >= 1


def test_direct_answer_runs_no_tool_and_writes_no_prediction(loop):
    tools = Tools()
    run(make(loop, model=Model(then="just words"), tools=tools))
    assert tools.calls == []
    assert predictions() == []


def test_acts_then_answers_before_the_budget(loop):
    tools = Tools()
    model = Model([act("read_file", path="a.txt"), act("read_file", path="b.txt")], then="both read")
    res = run(make(loop, model=model, tools=tools, max_steps=50))
    assert [c[0] for c in tools.calls] == ["read_file", "read_file"]
    assert [c[1]["path"] for c in tools.calls] == ["a.txt", "b.txt"]
    assert res.content == "both read" and res.success is True
    assert res.stop_reason != STOP_BUDGET
    assert res.steps >= 2


def test_an_endless_actor_stops_at_the_budget_and_says_why(loop):
    tools = Tools()
    res = run(make(loop, model=Model(then=act("web_search", q="more")), tools=tools, max_steps=3))
    assert res.stop_reason == STOP_BUDGET
    assert res.success is False
    assert 1 <= len(tools.calls) <= 3
    assert res.steps <= 3


@pytest.mark.parametrize("n", [1, 2, 5, 9])
def test_tool_executions_never_exceed_the_budget(loop, n):
    tools = Tools()
    res = run(make(loop, model=Model(then=act("t")), tools=tools, max_steps=n))
    assert len(tools.calls) <= n
    assert res.steps <= n
    assert res.stop_reason == STOP_BUDGET


def test_a_larger_budget_allows_more_work(loop):
    small, big = Tools(), Tools()
    run(make(loop, model=Model(then=act("t")), tools=small, max_steps=2))
    run(make(loop, model=Model(then=act("t")), tools=big, max_steps=8))
    assert len(big.calls) > len(small.calls)


def test_budget_value_object(loop):
    assert getattr(loop.StepBudget(**{BUDGET_KW: 3}), BUDGET_KW) == 3
    with pytest.raises((ValueError, TypeError)):
        loop.StepBudget(**{BUDGET_KW: -1})


# ------------------------------------------------------------------------------------------------ act: predict + event


def test_prediction_exists_before_the_tool_runs(loop):
    tools = Tools()
    run(make(loop, model=Model([act("read_file", path="x")]), tools=tools))
    assert len(tools.at_call) == 1
    seen = tools.at_call[0]
    assert len(seen["predictions"]) == 1
    pred = seen["predictions"][0]
    assert "read_file" in pred["about"]
    assert pred["resolved_at"] is None
    assert isinstance(pred["expected"]["success"], bool)
    assert 0.0 <= pred["confidence"] <= 1.0
    assert "prediction" in seen["kinds"]  # the CS2 event for it was already written


def test_every_act_step_has_its_own_prediction_first(loop):
    tools = Tools()
    run(make(loop, model=Model([act("a"), act("b"), act("c")]), tools=tools))
    assert [len(s["predictions"]) for s in tools.at_call] == [1, 2, 3]


def test_prediction_is_resolved_with_the_real_outcome(loop):
    tools = Tools(outcomes=[True, False])
    run(make(loop, model=Model([act("good"), act("bad")]), tools=tools))
    good, bad = predictions()
    assert good["resolved_at"] and good["actual"]["success"] is True
    assert bad["resolved_at"] and bad["actual"]["success"] is False
    kinds = [e.kind for e in all_events()]
    assert kinds.count("prediction") == 2 and kinds.count("prediction_resolved") == 2


def test_an_act_step_writes_an_event_that_names_the_tool(loop):
    run(make(loop, model=Model([act("read_file", path="x")])))
    mine = [e for e in all_events() if e.kind not in LEDGER_KINDS and "read_file" in json.dumps(e.payload)]
    assert mine, "no loop event names the tool that was run"


def test_the_event_chain_still_verifies(loop):
    run(make(loop, model=Model([act("a"), act("b")])))
    conn = open_db()
    try:
        assert events.verify_chain(conn) == (True, None)
    finally:
        conn.close()


def test_secrets_never_reach_the_log(loop):
    task = f"deploy with api_key={SECRET} please"
    run(make(loop, model=Model([act("shell", cmd=f"export api_key={SECRET}")]), tools=Tools()), task)
    blob = json.dumps([e.payload for e in all_events()]) + json.dumps(predictions())
    assert SECRET not in blob


# ------------------------------------------------------------------------------------------------ router and memory


def test_the_router_picks_the_model_for_every_call(loop):
    router, model = Router(model_id=M.CLAUDE_COMPLEX), Model([act("t")])
    run(make(loop, router=router, model=model))
    assert router.calls and model.calls
    assert all(isinstance(s, Step) and isinstance(c, RouteContext) for s, c in router.calls)
    assert {d.model_id for d, _ in model.calls} == {M.CLAUDE_COMPLEX}


def test_a_refused_route_stops_the_loop_without_acting(loop):
    tools = Tools()
    model = Model([act("t")])
    router = Router(refuse=RouteRefused("local model is down", wait=True))
    res = run(make(loop, router=router, model=model, tools=tools))
    assert res.success is False
    assert isinstance(res.stop_reason, str) and res.stop_reason and res.stop_reason != STOP_BUDGET
    assert tools.calls == []
    assert model.calls == []
    assert predictions() == []


def test_recalled_memory_reaches_a_prompt(loop):
    memory, model = Memory(marker="THE-REMEMBERED-FACT-9921"), Model(then="ok")
    task = "where did I put the keys"
    run(make(loop, memory=memory, model=model), task)
    assert memory.queries and any(task in q for q in memory.queries)
    assert any("THE-REMEMBERED-FACT-9921" in prompt for _, prompt in model.calls)


def test_a_failing_memory_does_not_stop_the_loop(loop):
    res = run(make(loop, memory=Memory(fail=True), model=Model(then="still here")))
    assert res.content == "still here" and res.success is True


# ------------------------------------------------------------------------------------------------ the program


def test_program_is_a_versioned_tracked_file(loop):
    path = Path(loop.PROGRAM_PATH)
    assert path.is_file()
    program = json.loads(path.read_text())
    v = program["version"]
    assert (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, str) and v.strip())
    assert [s["name"] for s in program["steps"]] == PHASES
    for s in program["steps"]:
        assert isinstance(s["budget"], int) and not isinstance(s["budget"], bool) and s["budget"] >= 0
    assert loop.load_program() == program
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", str(path)], cwd=REPO_ROOT,
                             capture_output=True, text=True, check=False)
    assert tracked.returncode == 0, "the program file must be committed (versioned)"


def test_default_program_recalls(loop):
    memory = Memory()
    run(make(loop, memory=memory))
    assert memory.queries


def test_a_program_without_recall_never_touches_memory(loop, monkeypatch, tmp_path):
    steps = [{"name": n, "budget": 1} for n in PHASES if n != "recall"]
    _use_program(loop, monkeypatch, tmp_path, steps, version=7)
    memory = Memory()
    res = run(make(loop, memory=memory))
    assert memory.queries == []
    assert res.success is True


def test_a_program_without_act_never_executes_a_tool(loop, monkeypatch, tmp_path):
    steps = [{"name": n, "budget": 1} for n in PHASES if n != "act"]
    _use_program(loop, monkeypatch, tmp_path, steps)
    tools = Tools()
    run(make(loop, model=Model(then=act("t")), tools=tools, max_steps=5))
    assert tools.calls == []
    assert predictions() == []


def _first_seen():
    order = []
    for e in all_events():
        name = e.payload.get(PHASE_KEY) if isinstance(e.payload, dict) else None
        if name in PHASES and name not in order:
            order.append(name)
    return order


def test_phases_run_in_the_programs_order(loop):
    run(make(loop, model=Model([act("t")])))
    seen = _first_seen()
    assert {"attend", "recall", "deliberate"} <= set(seen)
    assert seen == [n for n in PHASES if n in seen]


def test_a_reordered_program_changes_the_order(loop, monkeypatch, tmp_path):
    order = ["recall", "attend", "deliberate", "predict", "act", "reflect"]
    _use_program(loop, monkeypatch, tmp_path, [{"name": n, "budget": 1} for n in order])
    run(make(loop, model=Model([act("t")])))
    seen = _first_seen()
    assert seen.index("recall") < seen.index("attend")


# ------------------------------------------------------------------------------------------------ delegation


@pytest.fixture
def brain(monkeypatch):
    from tanishi.core.brain import BrainResponse, TanishiBrain

    b = TanishiBrain.__new__(TanishiBrain)
    b.conversation_history, b.memory_manager = [], None
    b.config = type("Config", (), {"max_conversation_history": 20, "offline_mode": False})()
    b.tool_registry, b.max_tool_loops, b._tool_status_callback = Tools(), 10, None
    monkeypatch.setattr(TanishiBrain, "_select_model", lambda self, text: "claude")
    monkeypatch.setattr(TanishiBrain, "_schedule_skill_extraction", lambda self, r: None)
    b.legacy_calls = 0

    async def legacy(self, system_prompt, messages, user_input=None):
        b.legacy_calls += 1
        return BrainResponse(content="legacy answer", model_used="claude")

    monkeypatch.setattr(TanishiBrain, "_think_claude_with_tools", legacy)
    return b


def _count_runs(loop, monkeypatch, content="loop answer"):
    calls = []

    async def fake_run(self, task):
        calls.append(task)
        return loop.Result(content=content, success=True, stop_reason="done", steps=1)

    monkeypatch.setattr(loop.CognitiveLoop, "run", fake_run)
    return calls


def test_flag_off_think_uses_the_legacy_path_and_never_the_loop(loop, brain, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    runs = _count_runs(loop, monkeypatch)
    resp = asyncio.run(brain.think("hello there"))
    assert resp.content == "legacy answer"
    assert brain.legacy_calls == 1
    assert runs == []


def test_flag_zero_is_off_too(loop, brain, monkeypatch):
    monkeypatch.setenv(FLAG, "0")
    runs = _count_runs(loop, monkeypatch)
    resp = asyncio.run(brain.think("hello there"))
    assert resp.content == "legacy answer" and runs == []


def test_flag_on_think_delegates_to_the_loop(loop, brain, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    runs = _count_runs(loop, monkeypatch)
    resp = asyncio.run(brain.think("hello there"))
    assert resp.content == "loop answer"
    assert len(runs) == 1 and "hello there" in runs[0]
    assert brain.legacy_calls == 0


def test_flag_on_still_records_the_task_events(loop, brain, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    _count_runs(loop, monkeypatch)
    asyncio.run(brain.think("hello there"))
    kinds = [e.kind for e in all_events()]
    assert "task_start" in kinds and "task_end" in kinds


# ------------------------------------------------------------------------------------------------ property


def test_invariants_hold_for_random_scripts(loop):
    """For any script of acts, answers and failures, and any budget: the budget bounds the work, every executed
    act has exactly one prediction written before it, each is resolved, and the chain verifies."""
    rng = random.Random(20261011)
    for case in range(30):
        n = rng.randint(1, 8)
        script = [act(rng.choice("abc")) if rng.random() < 0.75 else "an answer" for _ in range(rng.randint(0, 10))]
        then = act("z") if rng.random() < 0.4 else "the end"
        outcomes = [rng.random() < 0.6 for _ in range(12)]
        before = {p["id"] for p in predictions()}
        tools = Tools(outcomes=outcomes)
        res = run(make(loop, model=Model(script, then=then), tools=tools, max_steps=n), f"task {case}")
        new = [p for p in predictions() if p["id"] not in before]
        assert len(tools.calls) <= n, case
        assert res.steps <= n, case
        assert len(new) == len(tools.calls), case
        assert all(p["resolved_at"] for p in new), case
        assert [p["actual"]["success"] for p in new] == outcomes[:len(new)], case
        if res.stop_reason == STOP_BUDGET:
            assert res.success is False, case
        conn = open_db()
        try:
            assert events.verify_chain(conn) == (True, None), case
        finally:
            conn.close()
