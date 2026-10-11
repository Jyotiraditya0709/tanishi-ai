"""SUB2 red-team breaks (2026-10-11). Each test fails on the code as reviewed.

Write-up: build/memory/runs/redteam-SUB2-20261011.md. When a fix lands, remove the xfail mark.
"""
import asyncio
import json
import sys
import types

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.substrate import loop as L
from tanishi.substrate.router import RouteDecision
from tanishi.tools.registry import ToolResult


def act(tool, **tool_input):
    return json.dumps({"action": {"tool": tool, "input": tool_input}})


class Model:
    def __init__(self, script=(), then="done"):
        self.script, self.then, self.calls = list(script), then, []

    async def __call__(self, decision, prompt):
        self.calls.append(prompt)
        assert len(self.calls) < 50
        return self.script.pop(0) if self.script else self.then


class Router:
    def route(self, step, context):
        return RouteDecision("m", "anthropic", "test")


class Tools:
    def __init__(self):
        self.calls = []

    def get_claude_tools(self):
        return [{"name": "t", "description": "a tool", "input_schema": {"type": "object"}}]

    async def execute(self, tool_name, tool_input, **kw):
        self.calls.append(tool_name)
        return ToolResult(success=True, output="out", tool_name=tool_name, error="")


def make(model, tools=None, memory=None, max_steps=10):
    return L.CognitiveLoop(Router(), memory, tools or Tools(), L.StepBudget(max_steps=max_steps), generate=model)


def go(cl, task="a task"):
    return asyncio.run(cl.run(task))


def query(sql):
    conn = open_db()
    try:
        migrate(conn)
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# --- B1 (high): a degenerate model reply crashes the loop ------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="B1 RecursionError escapes parse_reply")
def test_b1_deeply_nested_reply_does_not_crash_the_loop():
    # json.loads raises RecursionError (not ValueError) on deep nesting; parse_reply only catches ValueError.
    res = go(make(Model(["[" * 100_000])))
    assert isinstance(res, L.Result)


# --- B2 (medium): an empty reply is a "successful" answer -------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="B2 empty reply counts as success")
@pytest.mark.parametrize("reply", ["", "   \n"])
def test_b2_empty_reply_is_not_success(reply):
    res = go(make(Model([reply])))
    assert not (res.success and not res.content.strip())


# --- B3 (medium): a cancelled task leaves no loop_stop and an unscored prediction --------------------------------

@pytest.mark.xfail(strict=True, reason="B3 cancel leaves no loop_stop")
def test_b3_cancel_during_a_tool_still_writes_loop_stop():
    class Slow(Tools):
        gate = None

        async def execute(self, tool_name, tool_input, **kw):
            self.gate.set()
            await asyncio.sleep(3600)

    async def main():
        tools = Slow()
        tools.gate = asyncio.Event()
        task = asyncio.create_task(make(Model([act("t")]), tools).run("x"))
        await tools.gate.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    kinds = [r[0] for r in query("SELECT kind FROM events ORDER BY rowid")]
    assert "loop_start" in kinds
    assert "loop_stop" in kinds  # a loop_start with no loop_stop is indistinguishable from a crash
    left = query("SELECT count(*) FROM predictions WHERE about = 'act:t' AND resolved_at IS NULL")[0][0]
    assert left == 0


# --- B4 (low): a fenced action with an upper-case tag is shown as the final answer, success=True ---------------

@pytest.mark.xfail(strict=True, reason="B4 upper-case fence shown as answer")
def test_b4_unparsed_action_json_is_not_a_successful_answer():
    raw = "```JSON\n" + act("t") + "\n```"
    tools = Tools()
    res = go(make(Model([raw], then="ok"), tools))
    assert tools.calls == ["t"] or not (res.success and res.content == raw)


# --- B5 (low): a legitimate JSON answer that has an "action" key is thrown away as malformed ------------------

@pytest.mark.xfail(strict=True, reason="B5 JSON answer with an action key discarded")
def test_b5_json_answer_with_an_action_key_is_returned():
    answer = '{"action": "approve", "reason": "the numbers add up"}'
    res = go(make(Model([answer])))
    assert res.success and res.content == answer


# --- B6 (medium): recall sends the whole task as one LIKE pattern, so the real memory finds nothing ----------------

@pytest.mark.xfail(strict=True, reason="B6 recall query is the whole task")
def test_b6_recall_finds_a_relevant_memory_in_the_real_manager(tmp_path):
    from tanishi.memory.manager import MemoryEntry, MemoryManager

    mm = MemoryManager(str(tmp_path / "mem.db"))
    mm.remember(MemoryEntry(id="m1", content="The user's favourite colour is teal.", category="fact",
                            importance=5, tags=["colour"], source="t"))
    model = Model(["The answer is teal."])
    go(make(model, memory=mm), task="What is my favourite colour?")
    assert "teal" in model.calls[0]


# --- the brain seam ---------------------------------------------------------------------------------------------

def _brain(monkeypatch):
    from tanishi.core.brain import BrainResponse, TanishiBrain

    b = TanishiBrain.__new__(TanishiBrain)
    b.conversation_history, b.memory_manager = [], None
    b.config = type("Config", (), {"max_conversation_history": 20, "offline_mode": False})()
    b.tool_registry, b.max_tool_loops, b._tool_status_callback = Tools(), 10, None
    monkeypatch.setattr(TanishiBrain, "_select_model", lambda self, text: "claude")
    monkeypatch.setattr(TanishiBrain, "_schedule_skill_extraction", lambda self, r: None)

    async def legacy(self, system_prompt, messages, user_input=None):
        return BrainResponse(content="legacy answer", model_used="claude")

    monkeypatch.setattr(TanishiBrain, "_think_claude_with_tools", legacy)
    return b


# B7 (medium): flag off, an import failure in the substrate still breaks think()
@pytest.mark.xfail(strict=True, reason="B7 flag off still imports the substrate")
def test_b7_flag_off_does_not_import_the_substrate(monkeypatch):
    import tanishi.substrate as pkg

    monkeypatch.delenv("TANISHI_SUBSTRATE", raising=False)
    monkeypatch.delattr(pkg, "loop", raising=False)
    monkeypatch.setitem(sys.modules, "tanishi.substrate.loop", None)  # import now raises
    resp = asyncio.run(_brain(monkeypatch).think("hello"))
    assert resp.content == "legacy answer"


# B8 (medium): flag on, a bad program file takes the whole brain down
@pytest.mark.xfail(strict=True, reason="B8 bad program crashes think")
def test_b8_bad_program_file_does_not_crash_think(monkeypatch, tmp_path):
    bad = tmp_path / "p.json"
    bad.write_text('{"version": 3, "steps": [{"name": "deliberate", "budget": -1}]}')
    monkeypatch.setattr(L, "PROGRAM_PATH", bad)
    monkeypatch.setenv("TANISHI_SUBSTRATE", "1")
    resp = asyncio.run(_brain(monkeypatch).think("hello"))  # raises ValueError today
    assert resp is not None


# B9 (medium): flag on drops the skill block the legacy path puts in the system prompt
@pytest.mark.xfail(strict=True, reason="B9 flag on drops skills")
def test_b9_flag_on_keeps_matching_skills_in_the_system_prompt(monkeypatch):
    import tanishi.skills.skill_store as store
    from tanishi.substrate import router as R

    b = _brain(monkeypatch)
    seen = {}

    class Messages:
        def create(self, **kw):
            seen.update(kw)
            usage = types.SimpleNamespace(input_tokens=10)
            return types.SimpleNamespace(content=[types.SimpleNamespace(text="ok")], usage=usage)

    b.claude_client = types.SimpleNamespace(messages=Messages())
    monkeypatch.setattr(R, "route", lambda step, ctx: RouteDecision("claude-x", "anthropic", "t"))
    monkeypatch.setattr(R, "report_spend", lambda *a, **k: None)
    monkeypatch.setattr(store, "find_matching_skills", lambda q, top_k=3: [object()])
    monkeypatch.setattr(store, "format_skills_for_context", lambda m: "SKILL-MARKER-123")
    monkeypatch.setenv("TANISHI_SUBSTRATE", "1")
    asyncio.run(b.think("hello"))
    assert "SKILL-MARKER-123" in seen.get("system", "")
