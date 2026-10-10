"""SUB2 implementer tests: what the exam (test_loop.py) leaves open. Decision 0024.

Fail-closed acts, per-phase budgets, program validation, reply parsing, the real router and ToolRegistry, and the
flag-off regression over every Practice task.
"""
import asyncio
import json
import random

import pytest

from tanishi.core_state import events, migrate, open_db
from tanishi.core_state import predictions as ledger
from tanishi.substrate import loop as L
from tanishi.substrate import router as real_router
from tanishi.substrate.router import RouteDecision
from tanishi.tools.registry import ToolResult


def act(tool, **tool_input):
    return json.dumps({"action": {"tool": tool, "input": tool_input}})


class Model:
    def __init__(self, script=(), then="done"):
        self.script, self.then, self.calls = list(script), then, []

    async def __call__(self, decision, prompt):
        self.calls.append((decision, prompt))
        assert len(self.calls) < 200
        return self.script.pop(0) if self.script else self.then


class Router:
    def __init__(self):
        self.calls = []

    def route(self, step, context):
        self.calls.append((step, context))
        return RouteDecision("m", "anthropic", "test")


class Tools:
    def __init__(self, outcomes=None):
        self.outcomes, self.calls = list(outcomes or []), []

    def get_claude_tools(self):
        return [{"name": "t", "description": "a tool", "input_schema": {"type": "object"}}]

    async def execute(self, tool_name, tool_input, **kw):
        self.calls.append((tool_name, tool_input))
        ok = self.outcomes.pop(0) if self.outcomes else True
        return ToolResult(success=ok, output="out" if ok else "", tool_name=tool_name, error="" if ok else "bad")


def rows():
    conn = open_db()
    try:
        migrate(conn)
        return conn.execute("SELECT about, actual, resolved_at FROM predictions ORDER BY ts, rowid").fetchall()
    finally:
        conn.close()


def make(model=None, tools=None, max_steps=10, router=None, memory=None, **kw):
    return L.CognitiveLoop(router or Router(), memory, tools or Tools(), L.StepBudget(max_steps=max_steps),
                           generate=model or Model(), **kw)


def go(cl, task="a task"):
    return asyncio.run(cl.run(task))


def program(monkeypatch, tmp_path, steps, version=2):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"version": version, "steps": steps}))
    monkeypatch.setattr(L, "PROGRAM_PATH", p)


# ------------------------------------------------------------------------------------------------ fail closed


def test_a_prediction_that_cannot_be_written_blocks_the_act(monkeypatch):
    def broken(*a, **k):
        raise OSError("ledger down")

    monkeypatch.setattr(ledger, "predict", broken)
    tools = Tools()
    res = go(make(Model([act("t")]), tools))
    assert tools.calls == []
    assert res.stop_reason == L.STOP_UNRECORDED and res.success is False


def test_an_act_event_that_cannot_be_written_blocks_the_act(monkeypatch):
    real_emit = events.emit

    def emit(kind, payload, **kw):
        if kind == "loop_step" and payload.get("step") == "act":
            raise OSError("log down")
        return real_emit(kind, payload, **kw)

    monkeypatch.setattr(events, "emit", emit)
    tools = Tools()
    res = go(make(Model([act("t")]), tools))
    assert tools.calls == []
    assert res.stop_reason == L.STOP_UNRECORDED
    (about, _, resolved), = rows()
    assert about == "act:t" and resolved is None  # never reached the tool: not evidence, left unscored


def test_other_phase_events_fail_open(monkeypatch):
    real_emit = events.emit

    def emit(kind, payload, **kw):
        if kind == "loop_step" and payload.get("step") in ("attend", "reflect"):
            raise OSError("log down")
        return real_emit(kind, payload, **kw)

    monkeypatch.setattr(events, "emit", emit)
    res = go(make(Model([act("t")], then="fine")))
    assert res.success and res.content == "fine"


def test_a_raising_tool_is_a_failed_act(monkeypatch):
    class Boom(Tools):
        async def execute(self, *a, **k):
            raise RuntimeError("x")

    res = go(make(Model([act("t")], then="ok"), Boom()))
    assert res.success
    (_, actual, resolved), = rows()
    assert resolved and json.loads(actual) == {"success": False}


# ------------------------------------------------------------------------------------------------ budgets


def test_zero_steps_runs_nothing():
    model = Model()
    res = go(make(model, max_steps=0))
    assert (res.stop_reason, res.steps, res.success, model.calls) == (L.STOP_BUDGET, 0, False, [])


def test_a_phase_budget_stops_the_loop_and_says_so(monkeypatch, tmp_path):
    steps = [{"name": n, "budget": 2 if n == "deliberate" else 9} for n in L.PHASES]
    program(monkeypatch, tmp_path, steps)
    tools = Tools()
    res = go(make(Model(then=act("t")), tools, max_steps=10))
    assert len(tools.calls) == 2
    assert res.stop_reason == L.STOP_PHASE_BUDGET and "deliberate" in res.detail and not res.success


def test_recall_budget_one_recalls_once():
    class Mem:
        n = 0

        def recall(self, q, limit):
            self.n += 1
            return []

    mem = Mem()
    go(make(Model([act("t"), act("t")]), memory=mem))
    assert mem.n == 1


def test_predict_after_act_in_the_program_still_predicts_first(monkeypatch, tmp_path):
    program(monkeypatch, tmp_path, [{"name": n, "budget": 5} for n in ("deliberate", "act", "predict")])
    seen = []

    class Watch(Tools):
        async def execute(self, name, inp, **kw):
            seen.append(len(rows()))
            return await super().execute(name, inp)

    go(make(Model([act("t")]), Watch()))
    assert seen == [1]


def test_no_act_phase_stalls_honestly(monkeypatch, tmp_path):
    program(monkeypatch, tmp_path, [{"name": "deliberate", "budget": 3}])
    model = Model(then=act("t"))
    res = go(make(model))
    assert res.stop_reason == L.STOP_STALLED and not res.success
    assert "cannot use tools" in model.calls[0][1]


# ------------------------------------------------------------------------------------------------ program file


@pytest.mark.parametrize("bad", [
    {"version": True, "steps": []},
    {"version": " ", "steps": []},
    {"version": 1},
    {"version": 1, "steps": [{"name": "dream", "budget": 1}]},
    {"version": 1, "steps": [{"name": "act", "budget": 1}, {"name": "act", "budget": 1}]},
    {"version": 1, "steps": [{"name": "act", "budget": -1}]},
    {"version": 1, "steps": [{"name": "act", "budget": 1.5}]},
    [],
])
def test_a_bad_program_is_refused_when_the_loop_is_built(monkeypatch, tmp_path, bad):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad))
    monkeypatch.setattr(L, "PROGRAM_PATH", p)
    with pytest.raises((ValueError, TypeError)):
        make()


def test_program_version_is_logged():
    go(make())
    start = next(e for e in events.iter_events("loop_start"))
    assert start.payload["program_version"] == L.load_program()["version"]


def test_step_budget_refuses_non_ints():
    for bad in (True, 1.0, "3"):
        with pytest.raises(TypeError):
            L.StepBudget(max_steps=bad)


# ------------------------------------------------------------------------------------------------ replies


def test_parse_reply():
    assert L.parse_reply("hello") == "hello"
    assert L.parse_reply('{"a": 1}') == '{"a": 1}'
    a = L.parse_reply("```json\n" + act("t", x=1) + "\n```")
    assert isinstance(a, L._Action) and (a.tool, a.input) == ("t", {"x": 1})
    assert L.parse_reply(json.dumps({"action": {"tool": ""}})) is None
    assert L.parse_reply(json.dumps({"action": {"tool": "t", "input": "x"}})) is None


def test_a_malformed_action_is_fed_back_not_run():
    tools, model = Tools(), Model([json.dumps({"action": {"tool": 5}})], then="ok")
    res = go(make(model, tools))
    assert tools.calls == [] and res.success
    assert "not a valid action" in model.calls[1][1]


def test_tool_output_reaches_the_next_prompt():
    model = Model([act("t")], then="ok")
    go(make(model))
    assert "t ok: out" in model.calls[1][1]


def test_events_hold_no_task_text():
    go(make(Model([act("t")])), "MY-PRIVATE-TASK-TEXT")
    assert "MY-PRIVATE-TASK-TEXT" not in json.dumps([e.payload for e in events.iter_events()])


# ------------------------------------------------------------------------------------------------ real collaborators


def test_with_the_real_router_a_personal_task_and_no_local_model_is_refused():
    res = go(make(router=real_router, personal=True, local_up=False))
    assert res.stop_reason == L.STOP_REFUSED
    assert [e.payload["outcome"] for e in events.iter_events("model_route")] == ["fail"]


def test_with_the_real_router_and_registry_both_ledgers_resolve():
    from tanishi.tools.registry import ToolRegistry
    from tanishi.tools.system_tools import get_system_tools

    reg = ToolRegistry()
    reg.register(next(t for t in get_system_tools() if t.name == "get_datetime"))
    model = Model([act("get_datetime")], then="it is now")
    res = go(make(model, reg, router=real_router))
    assert res.success and res.tools_used == ["get_datetime"]
    abouts = sorted(r[0] for r in rows())
    assert abouts == ["act:get_datetime", "tool:get_datetime"]  # the loop's and the registry's, kept apart
    assert all(r[2] for r in rows())


def test_invariants_hold_for_random_scripts_on_a_migrated_db():
    """The exam's property test reads the ledger before anything creates it; this one migrates first."""
    rows()
    rng = random.Random(7)
    for case in range(25):
        n = rng.randint(0, 7)
        script = [act("abc"[rng.randint(0, 2)]) if rng.random() < 0.75 else "ans" for _ in range(rng.randint(0, 9))]
        tools = Tools(outcomes=[rng.random() < 0.5 for _ in range(10)])
        before = len(rows())
        res = go(make(Model(script, then=act("z") if rng.random() < 0.5 else "end"), tools, max_steps=n))
        new = rows()[before:]
        assert len(tools.calls) == len(new) <= n and res.steps <= n, case
        assert all(r[2] for r in new), case
        assert res.success == (res.stop_reason == L.STOP_ANSWER), case
        conn = open_db()
        try:
            assert events.verify_chain(conn) == (True, None)
        finally:
            conn.close()


# ------------------------------------------------------------------------------------------------ flag-off regression


@pytest.fixture
def brain(monkeypatch):
    from tanishi.core.brain import BrainResponse, TanishiBrain

    b = TanishiBrain.__new__(TanishiBrain)
    b.conversation_history, b.memory_manager = [], None
    b.config = type("Config", (), {"max_conversation_history": 20, "offline_mode": False})()
    b.tool_registry, b.max_tool_loops, b._tool_status_callback = Tools(), 10, None
    monkeypatch.setattr(TanishiBrain, "_select_model", lambda self, text: "claude")
    monkeypatch.setattr(TanishiBrain, "_schedule_skill_extraction", lambda self, r: None)

    async def legacy(self, system_prompt, messages, user_input=None):
        return BrainResponse(content=f"legacy:{user_input}", model_used="claude", tools_used=["t"])

    monkeypatch.setattr(TanishiBrain, "_think_claude_with_tools", legacy)
    return b


def test_flag_off_every_practice_task_takes_the_legacy_path_unchanged(brain, monkeypatch):
    """With TANISHI_SUBSTRATE unset, think() on every Practice prompt returns exactly what the pre-SUB2 body
    (`_think`) returns, writes the same task events, and never builds a loop."""
    from tanishi.arena.practice import load_practice

    monkeypatch.delenv(L.FLAG, raising=False)

    def never(*a, **k):
        raise AssertionError("the loop was built with the flag off")

    monkeypatch.setattr(L.CognitiveLoop, "__init__", never)
    tasks = load_practice()
    assert tasks
    for task in tasks:
        expected = asyncio.run(brain._think(task.prompt, "casual", "", ""))
        got = asyncio.run(brain.think(task.prompt))
        assert got == expected, task.id
    kinds = [e.kind for e in events.iter_events()]
    assert kinds == ["task_start", "task_end"] * len(tasks)


def test_flag_on_runs_the_real_loop_and_reports_failure(brain, monkeypatch):
    monkeypatch.setenv(L.FLAG, "1")
    from tanishi.core.brain import TanishiBrain

    monkeypatch.setattr(TanishiBrain, "_substrate_generate", lambda self, sp: Model(then=act("t")))
    real_init = L.CognitiveLoop.__init__

    def with_fake_router(self, router, *a, **k):
        real_init(self, Router(), *a, **k)

    monkeypatch.setattr(L.CognitiveLoop, "__init__", with_fake_router)
    brain.max_tool_loops = 2
    resp = asyncio.run(TanishiBrain.think(brain, "do it"))
    assert resp.failed and "budget" in resp.error
    end = next(e for e in events.iter_events("task_end"))
    assert end.payload["success"] is False
