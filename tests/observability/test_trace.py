"""OBS1 exam: traces. Written from the card alone (tester role); assumptions in
build/memory/open-problems/OBS1-exam-assumptions.md.

The card pins: ``with span(name, **attrs)`` nests, writes JSONL under ``$TANISHI_HOME/traces/``,
each span records model, tokens in/out, latency, tool calls and the event ids it produced,
tracing failures never break a task, and ``python -m tanishi.observability.trace <task_id>``
shows the trace. Field names are not pinned, so records are read through alias lists.
"""

import asyncio
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tanishi.core_state import events
from tanishi.observability.trace import span

ALIASES = {
    "name": ("name", "span", "span_name"),
    "id": ("span_id", "id"),
    "parent": ("parent_id", "parent", "parent_span_id"),
    "task": ("task_id", "task"),
    "model": ("model", "model_used"),
    "tin": ("tokens_in", "input_tokens", "prompt_tokens", "tokens_input"),
    "tout": ("tokens_out", "output_tokens", "completion_tokens", "tokens_output"),
    "tools": ("tool_calls", "tools", "tools_used"),
    "events": ("event_ids", "events"),
    "latency": ("latency_ms", "duration_ms", "latency", "duration", "ms", "elapsed_ms"),
}


def traces_dir() -> Path:
    home = os.environ.get("TANISHI_HOME") or str(Path(os.environ["HOME"]) / ".tanishi")
    return Path(home) / "traces"


def records() -> list[dict]:
    out = []
    d = traces_dir()
    assert d.is_dir(), "no traces directory was created"
    files = sorted(d.rglob("*.jsonl"))
    assert files, "no .jsonl file under traces/"
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))  # every line must be valid JSON
    return out


def field(rec: dict, key: str):
    nested = rec.get("attrs") if isinstance(rec.get("attrs"), dict) else {}
    for scope in (rec, nested):
        for alias in ALIASES[key]:
            if alias in scope:
                return scope[alias]
    raise AssertionError(f"record has none of {ALIASES[key]}: {rec}")


def by_name(name: str) -> dict:
    found = [r for r in records() if field(r, "name") == name]
    assert len(found) == 1, f"expected one span named {name!r}, got {len(found)}"
    return found[0]


def latency_ms(rec: dict) -> float:
    for alias in ALIASES["latency"]:
        if alias in rec:
            v = float(rec[alias])
            return v if alias.endswith("ms") else v * 1000.0
    raise AssertionError(f"no latency field in {rec}")


# ------------------------------------------------------------------ what a span records


def test_span_writes_one_jsonl_record_per_span_under_traces():
    with span("alpha"):
        pass
    with span("beta"):
        pass
    recs = records()
    assert sorted(field(r, "name") for r in recs) == ["alpha", "beta"]
    assert traces_dir().parent.name == ".tanishi"


def test_record_is_flushed_when_the_span_exits_not_at_process_end():
    with span("flushed"):
        pass
    assert field(by_name("flushed"), "name") == "flushed"


def test_model_and_tokens_are_recorded():
    with span("think", model="claude-sonnet-5-5", tokens_in=1234, tokens_out=56):
        pass
    r = by_name("think")
    assert field(r, "model") == "claude-sonnet-5-5"
    assert field(r, "tin") == 1234
    assert field(r, "tout") == 56


def test_latency_is_measured():
    with span("slow"):
        time.sleep(0.08)
    with span("fast"):
        pass
    slow, fast = latency_ms(by_name("slow")), latency_ms(by_name("fast"))
    assert 60 <= slow < 5000
    assert 0 <= fast < slow


def test_tool_calls_attr_is_recorded():
    with span("step", tool_calls=["read_file", "web_search"]):
        pass
    got = json.dumps(field(by_name("step"), "tools"))
    assert "read_file" in got and "web_search" in got


def test_extra_attrs_survive():
    with span("custom", flavour="vanilla", attempt=3):
        pass
    flat = json.dumps(by_name("custom"))
    assert "vanilla" in flat and "attempt" in flat


# ------------------------------------------------------------------ links to events


def test_event_ids_produced_inside_the_span_are_recorded():
    before = events.emit("note", {"n": 0})
    with span("work"):
        a = events.emit("note", {"n": 1})
        b = events.emit("note", {"n": 2})
    after = events.emit("note", {"n": 3})
    ids = list(field(by_name("work"), "events"))
    assert sorted(ids) == [a, b]
    assert before not in ids and after not in ids


def test_span_with_no_events_has_an_empty_event_list():
    with span("quiet"):
        pass
    assert list(field(by_name("quiet"), "events")) == []


def test_tool_call_events_show_up_as_tool_calls():
    with span("uses-tool"):
        events.emit("tool_call", {"tool": "grep_codebase", "call_id": "c1", "task_id": None})
    got = field(by_name("uses-tool"), "tools")
    assert "grep_codebase" in json.dumps(got) or got == 1


def test_child_events_belong_to_the_child():
    with span("outer"):
        o1 = events.emit("note", {"who": "outer"})
        with span("inner"):
            i1 = events.emit("note", {"who": "inner"})
    assert i1 in field(by_name("inner"), "events")
    assert o1 in field(by_name("outer"), "events")
    assert o1 not in field(by_name("inner"), "events")


# ------------------------------------------------------------------ nesting and tasks


def test_nested_spans_link_to_their_parent():
    with span("root"), span("child"), span("grandchild"):
        pass
    root, child, grand = by_name("root"), by_name("child"), by_name("grandchild")
    assert field(child, "parent") == field(root, "id")
    assert field(grand, "parent") == field(child, "id")
    assert field(root, "parent") in (None, "", 0)


def test_siblings_share_a_parent_and_have_distinct_ids():
    with span("p"):
        with span("s1"):
            pass
        with span("s2"):
            pass
    p, s1, s2 = by_name("p"), by_name("s1"), by_name("s2")
    assert field(s1, "parent") == field(s2, "parent") == field(p, "id")
    assert len({field(r, "id") for r in (p, s1, s2)}) == 3


def test_task_id_comes_from_the_current_task_scope():
    with events.task_scope("task-aaa"), span("in-a"):
        pass
    with events.task_scope("task-bbb"), span("in-b"):
        pass
    assert field(by_name("in-a"), "task") == "task-aaa"
    assert field(by_name("in-b"), "task") == "task-bbb"


def test_concurrent_async_tasks_keep_their_own_trees():
    async def job(tag: str):
        with events.task_scope(f"t-{tag}"), span(f"{tag}-outer"):
            await asyncio.sleep(0.01)
            with span(f"{tag}-inner"):
                await asyncio.sleep(0.01)

    async def main():
        await asyncio.gather(*(job(t) for t in "xyz"))

    asyncio.run(main())
    for t in "xyz":
        outer, inner = by_name(f"{t}-outer"), by_name(f"{t}-inner")
        assert field(inner, "parent") == field(outer, "id")
        assert field(inner, "task") == field(outer, "task") == f"t-{t}"


def test_random_trees_are_well_formed():
    """Property: any nesting yields unique ids, parents that exist and are the right span."""
    for seed in range(8):
        rng = random.Random(seed)
        made: list[str] = []

        def build(depth: int, prefix: str, rng=rng, made=made):
            for i in range(rng.randint(0, 3)):
                name = f"{prefix}.{i}"
                made.append(name)
                with span(name):
                    if depth < 3:
                        build(depth + 1, name, rng, made)

        root = f"s{seed}"
        with events.task_scope(f"prop-{seed}"):
            build(0, root)
        recs = [r for r in records() if str(field(r, "task")) == f"prop-{seed}"]
        assert sorted(field(r, "name") for r in recs) == sorted(made)
        ids = [field(r, "id") for r in recs]
        assert len(set(ids)) == len(ids)
        names = {field(r, "id"): field(r, "name") for r in recs}
        for r in recs:
            name, p = field(r, "name"), field(r, "parent")
            expected_parent = name.rsplit(".", 1)[0]
            if expected_parent == root:
                assert p in (None, "", 0)
            else:
                assert names[p] == expected_parent


# ------------------------------------------------------------------ failures never break a task


def test_exception_in_body_propagates_and_span_is_still_recorded():
    with pytest.raises(ZeroDivisionError), span("boom"):
        raise ZeroDivisionError
    assert field(by_name("boom"), "name") == "boom"


def test_body_runs_exactly_once():
    out = []
    with span("ok"):
        out.append(41 + 1)
    assert out == [42]


def test_unwritable_traces_dir_does_not_break_the_task(tmp_path, monkeypatch):
    blocker = tmp_path / "home-is-a-file"
    blocker.write_text("x")
    monkeypatch.setenv("TANISHI_HOME", str(blocker))
    ran = []
    with span("still-runs"):
        ran.append(1)
    assert ran == [1]


def test_read_only_traces_dir_does_not_break_the_task(tmp_path, monkeypatch):
    home = tmp_path / "ro"
    (home / "traces").mkdir(parents=True)
    (home / "traces").chmod(0o500)
    monkeypatch.setenv("TANISHI_HOME", str(home))
    try:
        ran = []
        with span("ro"):
            ran.append(1)
        assert ran == [1]
    finally:
        (home / "traces").chmod(0o700)


def test_unserializable_attrs_do_not_break_the_task():
    ran = []
    with span("weird", obj=object(), s={1, 2}, f=float("nan"), b=b"\xff", e=ValueError("x")):
        ran.append(1)
    assert ran == [1]
    for r in records():  # whatever was written is still valid JSON lines
        assert isinstance(r, dict)


def test_broken_task_lookup_does_not_break_the_task(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("event log down")

    monkeypatch.setattr(events, "current_task", broken)
    ran = []
    with span("no-log"):
        ran.append(1)
    assert ran == [1]


def test_secret_in_attrs_is_not_written_to_the_trace():
    secret = "sk-ant-api03-" + "A1b2C3d4" * 6
    with span("leaky", api_key=secret, note=f"called with {secret}"):
        pass
    text = "".join(p.read_text() for p in traces_dir().rglob("*.jsonl"))
    assert secret not in text


# ------------------------------------------------------------------ the CLI


def run_cli(*args: str) -> subprocess.CompletedProcess:
    root = Path(__file__).resolve().parents[2]
    return subprocess.run(
        [sys.executable, "-m", "tanishi.observability.trace", *args],
        capture_output=True, text=True, cwd=root, env=os.environ.copy(), timeout=60, check=False,
    )


def _indent(s: str) -> int:
    return len(s) - len(s.lstrip())


def test_cli_prints_the_task_trace_in_order_and_indented():
    with events.task_scope("task-cli"), span("plan", model="m1"):
        eid = events.emit("note", {"k": "v"})
        with span("act"):
            pass
    with events.task_scope("task-other"), span("unrelated"):
        pass
    r = run_cli("task-cli")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "plan" in out and "act" in out
    assert "unrelated" not in out
    assert out.index("plan") < out.index("act")
    lines = out.splitlines()
    plan_line = next(ln for ln in lines if "plan" in ln)
    act_line = next(ln for ln in lines if "act" in ln)
    assert _indent(act_line) > _indent(plan_line)
    assert str(eid) in out  # linked to its events


def test_cli_unknown_task_fails_cleanly():
    with events.task_scope("task-real"), span("unrelated"):
        pass
    r = run_cli("no-such-task")
    assert "Traceback" not in r.stderr
    assert "unrelated" not in r.stdout


def test_cli_with_no_traces_at_all_fails_cleanly():
    r = run_cli("anything")
    assert "Traceback" not in r.stderr
