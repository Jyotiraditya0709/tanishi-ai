"""SUB3 exam: the model router, written from the spec alone.

Spec interface:
    route(step: Step, context: RouteContext) -> RouteDecision(model_id, provider, reason)
Acceptance:
    1. A step that needs tools never goes to a model without tool support (replaces LOCAL_FIRST).
    2. A personal step goes to a local model unless the rule is switched off; if no local model is up the step
       waits or fails, it never silently goes to the cloud.
    3. Every decision is an event with its reason.
    4. Model ids come from tanishi/config/models.py only.
    5. Before a paid call: tanishi.guard.check_spend(estimate); on deny fall back to local (or wait), never the
       paid one. After the call: tanishi.guard.record_spend(actual). Only when TANISHI_GUARD=1.

The spec fixes none of the names on RouteContext, how "a local model is up" is known, or how the spend report is
made. The assumptions are listed in build/memory/open-problems/SUB3-exam-assumptions.md and each one lives in a
constant or a helper at the top of this file, so a different spelling is a one-line change. Do not weaken a test.

Classification is by model id, taken from tanishi/config/models.py: OLLAMA_DEFAULT (gemma3:4b) is the local model and
has no tool support; every CLAUDE_* id is paid cloud and supports tools.
"""
import ast
import asyncio
import importlib
import inspect
import itertools
import json
import math
import random
from pathlib import Path

import pytest

from tanishi.config import models as M
from tanishi.core_state import events
from tanishi.substrate.state import Step

REPO_ROOT = Path(__file__).resolve().parents[2]
ROUTER_FILE = REPO_ROOT / "tanishi" / "substrate" / "router.py"

LOCAL_IDS = {M.OLLAMA_DEFAULT}
CLOUD_IDS = {M.CLAUDE_SIMPLE, M.CLAUDE_COMPLEX, M.CLAUDE_DEFAULT}
ALL_IDS = LOCAL_IDS | CLOUD_IDS

# --- assumed RouteContext spelling (one place to change) -------------------------------------------------------
F_TOOLS = "needs_tools"             # the step needs tool calls
F_PERSONAL = "personal"             # the step is marked personal
F_LOCAL_UP = "local_up"             # a local model is reachable
F_PERSONAL_RULE = "personal_local_only"  # True = personal steps stay local; False = rule switched off
# --- assumed spend-report entry point: router.<name>(decision, actual_cost) -------------------------------------
REPORT_SPEND_NAME = "report_spend"


# ------------------------------------------------------------------------------------------------ helpers


@pytest.fixture
def router():
    return importlib.import_module("tanishi.substrate.router")


class Warden:
    """A fake warden: records every guard.check() the router causes, answers check_spend as told."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.spend = "allow"  # "allow" | "deny" | "raise"

    @property
    def estimates(self):
        return [a["cost_estimate"] for t, a in self.calls if t == "paid_model" and "cost_estimate" in a]

    @property
    def actuals(self):
        return [a["actual_cost"] for t, a in self.calls if t == "paid_model" and "actual_cost" in a]


def _install_warden(monkeypatch, router, on: bool) -> Warden:
    from tanishi.guard import Decision, client
    from tanishi.tools import registry

    w = Warden()

    def fake_check(tool_name, args, actor="tanishi", session_id=None):
        w.calls.append((tool_name, dict(args or {})))
        if w.spend == "raise":
            raise RuntimeError("warden exploded")
        if w.spend == "deny":
            return Decision("deny", "over the daily cap")
        return Decision("allow", "ok")

    # check_spend / record_spend call client.check, so this sees them however the router imported them.
    monkeypatch.setattr(client, "check", fake_check)
    # The tool gate reads TANISHI_GUARD once at import (registry._GUARD_ON). Force every plausible reading.
    if on:
        monkeypatch.setenv("TANISHI_GUARD", "1")
    else:
        monkeypatch.delenv("TANISHI_GUARD", raising=False)
    monkeypatch.setattr(registry, "_GUARD_ON", on)
    monkeypatch.setattr(router, "_GUARD_ON", on, raising=False)
    return w


@pytest.fixture
def warden_on(monkeypatch, router):
    return _install_warden(monkeypatch, router, True)


@pytest.fixture
def warden_off(monkeypatch, router):
    return _install_warden(monkeypatch, router, False)


def mkctx(router, **kw):
    """A RouteContext from only the fields given; every field not given keeps the router's default."""
    return router.RouteContext(**kw)


def mkstep(text="Summarise this paragraph.", sid="s1"):
    return Step(id=sid, description=text)


def run(router, step, ctx):
    out = router.route(step, ctx)
    if inspect.isawaitable(out):
        out = asyncio.run(out)
    return out


def outcome(router, step, ctx):
    """('ok', decision) or ('refused', exception). A refusal (wait or fail) is a legitimate outcome."""
    try:
        return "ok", run(router, step, ctx)
    except Exception as e:  # noqa: BLE001 - any refusal type is acceptable, the spec names none
        return "refused", e


def assert_not_in(res, forbidden, why):
    kind, val = res
    if kind == "ok":
        assert val.model_id not in forbidden, f"{why}: routed to {val.model_id!r}"


def event_count():
    return sum(1 for _ in events.iter_events())


def new_events(before):
    return list(itertools.islice(events.iter_events(), before, None))


# --------------------------------------------------------------------------- 0. shape of the answer


def test_decision_has_model_provider_reason(router, warden_off):
    d = run(router, mkstep(), mkctx(router, **{F_LOCAL_UP: True}))
    assert isinstance(d.model_id, str) and d.model_id
    assert isinstance(d.provider, str) and d.provider
    assert isinstance(d.reason, str) and d.reason.strip()
    assert d.model_id in ALL_IDS


def test_local_and_cloud_providers_differ(router, warden_off):
    local = run(router, mkstep(), mkctx(router, **{F_PERSONAL: True, F_LOCAL_UP: True}))
    cloud = run(router, mkstep(), mkctx(router, **{F_TOOLS: True}))
    assert local.model_id in LOCAL_IDS and cloud.model_id in CLOUD_IDS
    assert local.provider != cloud.provider


# --------------------------------------------------------------------------- 1. tools never go to a tool-less model


@pytest.mark.parametrize("text", ["hi", "ok", "?", "what time is it", "x" * 3, ""])
def test_short_prompt_that_needs_tools_is_not_local(router, warden_off, text):
    """The reported bug: short prompts went to the tool-less local model."""
    d = run(router, mkstep(text), mkctx(router, **{F_TOOLS: True, F_LOCAL_UP: True}))
    assert d.model_id in CLOUD_IDS


def test_needs_tools_goes_to_a_cloud_model_even_when_local_is_up(router, warden_off):
    d = run(router, mkstep("list the files"), mkctx(router, **{F_TOOLS: True, F_LOCAL_UP: True}))
    assert d.model_id in CLOUD_IDS


def test_needs_tools_with_local_down_still_cloud(router, warden_off):
    d = run(router, mkstep("list the files"), mkctx(router, **{F_TOOLS: True, F_LOCAL_UP: False}))
    assert d.model_id in CLOUD_IDS


@pytest.mark.parametrize("local_first", [True, False])
def test_local_first_flag_cannot_send_tool_steps_local(router, warden_off, monkeypatch, local_first):
    from tanishi.config import routing

    monkeypatch.setattr(routing, "LOCAL_FIRST", local_first, raising=False)
    d = run(router, mkstep("hello"), mkctx(router, **{F_TOOLS: True, F_LOCAL_UP: True}))
    assert d.model_id not in LOCAL_IDS


def test_tools_and_personal_with_rule_on_never_goes_anywhere_unsafe(router, warden_off):
    """Local has no tools, cloud is forbidden for personal: the only honest answer is to wait or fail."""
    res = outcome(router, mkstep("read my diary"),
                  mkctx(router, **{F_TOOLS: True, F_PERSONAL: True, F_LOCAL_UP: True, F_PERSONAL_RULE: True}))
    assert_not_in(res, ALL_IDS, "tools+personal with the rule on has no safe model")
    assert res[0] == "refused"


def test_tools_and_personal_with_rule_off_uses_a_cloud_model(router, warden_off):
    d = run(router, mkstep("read my diary"),
            mkctx(router, **{F_TOOLS: True, F_PERSONAL: True, F_LOCAL_UP: True, F_PERSONAL_RULE: False}))
    assert d.model_id in CLOUD_IDS


# --------------------------------------------------------------------------- 2. personal stays local


def test_personal_goes_to_local_when_up(router, warden_off):
    d = run(router, mkstep("my medical notes"), mkctx(router, **{F_PERSONAL: True, F_LOCAL_UP: True}))
    assert d.model_id in LOCAL_IDS


def test_personal_rule_defaults_to_on(router, warden_off):
    """Not passing the switch must mean the rule is on."""
    res = outcome(router, mkstep("my medical notes"), mkctx(router, **{F_PERSONAL: True, F_LOCAL_UP: False}))
    assert_not_in(res, CLOUD_IDS, "default personal rule must keep personal text off the cloud")


@pytest.mark.parametrize("text", ["short", "a much longer personal note " * 80])
def test_personal_with_no_local_model_waits_or_fails_never_cloud(router, warden_off, text):
    res = outcome(router, mkstep(text), mkctx(router, **{F_PERSONAL: True, F_LOCAL_UP: False}))
    assert res[0] == "refused", "must wait or fail, not hand back a model that is down"
    assert_not_in(res, ALL_IDS, "no local model is up")


def test_personal_never_cloud_even_when_spend_would_be_allowed(router, warden_on):
    warden_on.spend = "allow"
    res = outcome(router, mkstep("my medical notes"), mkctx(router, **{F_PERSONAL: True, F_LOCAL_UP: False}))
    assert_not_in(res, CLOUD_IDS, "personal step must not reach the cloud")
    assert warden_on.estimates == [], "a personal step must not even ask to spend"


def test_personal_rule_off_allows_cloud_when_local_is_down(router, warden_off):
    d = run(router, mkstep("my medical notes"),
            mkctx(router, **{F_PERSONAL: True, F_LOCAL_UP: False, F_PERSONAL_RULE: False}))
    assert d.model_id in CLOUD_IDS


def test_personal_rule_off_still_gives_a_valid_model_when_local_is_up(router, warden_off):
    d = run(router, mkstep("my medical notes"),
            mkctx(router, **{F_PERSONAL: True, F_LOCAL_UP: True, F_PERSONAL_RULE: False}))
    assert d.model_id in ALL_IDS


def test_non_personal_step_with_nothing_special_is_not_refused(router, warden_off):
    for local_up in (True, False):
        d = run(router, mkstep(), mkctx(router, **{F_LOCAL_UP: local_up}))
        assert d.model_id in ALL_IDS
    d = run(router, mkstep(), mkctx(router, **{F_LOCAL_UP: False}))
    assert d.model_id in CLOUD_IDS, "local is down and the step is not personal"


def test_local_down_never_returns_the_local_model(router, warden_off):
    for tools, personal, rule in itertools.product([False, True], repeat=3):
        ctx = mkctx(router, **{F_TOOLS: tools, F_PERSONAL: personal, F_LOCAL_UP: False, F_PERSONAL_RULE: rule})
        assert_not_in(outcome(router, mkstep(), ctx), LOCAL_IDS, "the local model is down")


# --------------------------------------------------------------------------- 3. every decision is an event


def test_decision_writes_an_event_with_its_reason(router, warden_off):
    before = event_count()
    d = run(router, mkstep("summarise"), mkctx(router, **{F_LOCAL_UP: True}))
    fresh = new_events(before)
    assert len(fresh) >= 1
    blob = json.dumps([e.payload for e in fresh], ensure_ascii=False)
    assert d.reason in blob, "the event must carry the decision's reason"
    assert d.model_id in blob, "the event must say which model was chosen"


def test_each_call_writes_its_own_event(router, warden_off):
    before = event_count()
    for i in range(4):
        run(router, mkstep(sid=f"s{i}"), mkctx(router, **{F_LOCAL_UP: True}))
    assert event_count() >= before + 4


def test_a_refusal_is_also_an_event(router, warden_off):
    before = event_count()
    res = outcome(router, mkstep("diary"), mkctx(router, **{F_PERSONAL: True, F_LOCAL_UP: False}))
    assert res[0] == "refused"
    assert event_count() > before, "waiting or failing is a decision and must be logged"


def test_events_stay_a_valid_chain(router, warden_off):
    for tools in (False, True):
        for personal in (False, True):
            outcome(router, mkstep(), mkctx(router, **{F_TOOLS: tools, F_PERSONAL: personal, F_LOCAL_UP: True}))
    from tanishi.core_state.db import open_db
    conn = open_db()
    try:
        ok, bad = events.verify_chain(conn)
    finally:
        conn.close()
    assert ok, f"chain broken at {bad}"


def test_event_does_not_carry_a_key_found_in_the_step_text(router, warden_off):
    key = "sk-ant-" + "a1B2c3D4" * 4
    before = event_count()
    outcome(router, mkstep(f"use {key} to call the api"), mkctx(router, **{F_LOCAL_UP: True}))
    blob = json.dumps([(e.payload, e.actor, e.kind) for e in new_events(before)])
    assert key not in blob


# --------------------------------------------------------------------------- 4. model ids come from models.py


_MODEL_WORDS = ("claude", "gemma", "llama", "ollama:", "gpt-", "whisper", "haiku", "opus", "sonnet", "mistral", "qwen")


def test_router_source_has_no_model_id_literals():
    assert ROUTER_FILE.exists(), "tanishi/substrate/router.py must exist"
    tree = ast.parse(ROUTER_FILE.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                docstrings.add(id(body[0].value))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            low = node.value.lower()
            if any(w in low for w in _MODEL_WORDS) and len(node.value) < 80:
                offenders.append((node.lineno, node.value))
    assert not offenders, f"model ids must come from tanishi.config.models, found literals: {offenders}"


def test_every_model_id_follows_models_py(monkeypatch):
    """Change models.py, reload, and the router must hand back the new ids and only those."""
    from tanishi.config import routing

    sentinels = {
        "CLAUDE_SIMPLE": "zz-simple-1",
        "CLAUDE_COMPLEX": "zz-complex-1",
        "CLAUDE_DEFAULT": "zz-default-1",
        "OLLAMA_DEFAULT": "zz-local-1",
    }
    try:
        with monkeypatch.context() as mp:
            for k, v in sentinels.items():
                mp.setattr(M, k, v)
            importlib.reload(routing)
            r = importlib.reload(importlib.import_module("tanishi.substrate.router"))
            mp.delenv("TANISHI_GUARD", raising=False)
            seen = set()
            for tools, personal in itertools.product([False, True], repeat=2):
                for text in ("hi", "long text " * 400):
                    res = outcome(r, mkstep(text), r.RouteContext(**{F_TOOLS: tools, F_PERSONAL: personal,
                                                                     F_LOCAL_UP: True}))
                    if res[0] == "ok":
                        seen.add(res[1].model_id)
            assert seen, "nothing routed"
            assert seen <= set(sentinels.values()), f"ids not from models.py: {seen - set(sentinels.values())}"
    finally:
        importlib.reload(routing)
        importlib.reload(importlib.import_module("tanishi.substrate.router"))


# --------------------------------------------------------------------------- 5. spend


def test_guard_off_never_asks_the_warden(router, warden_off):
    d = run(router, mkstep("list files"), mkctx(router, **{F_TOOLS: True, F_LOCAL_UP: True}))
    assert d.model_id in CLOUD_IDS
    assert warden_off.calls == []


def test_guard_on_asks_check_spend_before_a_paid_model(router, warden_on):
    d = run(router, mkstep("list files"), mkctx(router, **{F_TOOLS: True, F_LOCAL_UP: True}))
    assert d.model_id in CLOUD_IDS
    assert len(warden_on.estimates) >= 1
    for e in warden_on.estimates:
        assert isinstance(e, (int, float)) and not isinstance(e, bool)
        assert math.isfinite(e) and e >= 0


def test_estimate_grows_with_the_amount_of_work(router, warden_on):
    run(router, mkstep("hi"), mkctx(router, **{F_TOOLS: True}))
    small = warden_on.estimates[-1]
    run(router, mkstep("write a long report " * 3000), mkctx(router, **{F_TOOLS: True}))
    big = warden_on.estimates[-1]
    assert big > small >= 0


def test_guard_on_local_choice_needs_no_spend_check(router, warden_on):
    d = run(router, mkstep("note"), mkctx(router, **{F_PERSONAL: True, F_LOCAL_UP: True}))
    assert d.model_id in LOCAL_IDS
    assert warden_on.estimates == []


def test_spend_deny_falls_back_to_local_when_possible(router, warden_on):
    warden_on.spend = "deny"
    d = run(router, mkstep("summarise this"), mkctx(router, **{F_LOCAL_UP: True}))
    assert d.model_id in LOCAL_IDS


def test_spend_deny_with_no_local_model_waits_or_fails(router, warden_on):
    warden_on.spend = "deny"
    res = outcome(router, mkstep("summarise this"), mkctx(router, **{F_LOCAL_UP: False}))
    assert res[0] == "refused"
    assert_not_in(res, ALL_IDS, "over the cap and nothing local")


def test_spend_deny_on_a_tool_step_never_uses_the_tool_less_local_model(router, warden_on):
    """Deny must not turn a tool step into a local one: no tools there. Wait or fail."""
    warden_on.spend = "deny"
    res = outcome(router, mkstep("list the files"), mkctx(router, **{F_TOOLS: True, F_LOCAL_UP: True}))
    assert res[0] == "refused"
    assert_not_in(res, ALL_IDS, "tool step over the cap has no safe model")


def test_spend_deny_never_returns_a_paid_model_for_any_context(router, warden_on):
    warden_on.spend = "deny"
    for tools, personal, up, rule in itertools.product([False, True], repeat=4):
        ctx = mkctx(router, **{F_TOOLS: tools, F_PERSONAL: personal, F_LOCAL_UP: up, F_PERSONAL_RULE: rule})
        assert_not_in(outcome(router, mkstep(), ctx), CLOUD_IDS, "the Warden denied the spend")


def test_a_warden_that_raises_is_treated_as_deny(router, warden_on):
    warden_on.spend = "raise"
    res = outcome(router, mkstep("summarise this"), mkctx(router, **{F_LOCAL_UP: True}))
    assert_not_in(res, CLOUD_IDS, "no answer from the Warden is not permission")


def test_a_real_unreachable_warden_fails_closed(router, monkeypatch):
    """No fake: the real client with no socket returns deny, and the router must not go paid."""
    from tanishi.guard import client
    from tanishi.tools import registry

    monkeypatch.setattr(client, "SOCKET_PATH", "/nonexistent/tanishi-warden.sock")
    monkeypatch.setenv("TANISHI_GUARD", "1")
    monkeypatch.setattr(registry, "_GUARD_ON", True)
    monkeypatch.setattr(router, "_GUARD_ON", True, raising=False)
    res = outcome(router, mkstep("summarise this"), mkctx(router, **{F_LOCAL_UP: True}))
    assert_not_in(res, CLOUD_IDS, "warden unreachable")


def test_spend_allow_returns_the_paid_model(router, warden_on):
    warden_on.spend = "allow"
    d = run(router, mkstep("list files"), mkctx(router, **{F_TOOLS: True, F_LOCAL_UP: False}))
    assert d.model_id in CLOUD_IDS


def test_deny_reason_is_logged(router, warden_on):
    warden_on.spend = "deny"
    before = event_count()
    run(router, mkstep("summarise this"), mkctx(router, **{F_LOCAL_UP: True}))
    blob = json.dumps([e.payload for e in new_events(before)]).lower()
    assert "spend" in blob or "cap" in blob or "deny" in blob or "denied" in blob


def _report(router):
    fn = getattr(router, REPORT_SPEND_NAME, None)
    assert callable(fn), f"router.{REPORT_SPEND_NAME}(decision, actual_cost) is the assumed spend report"
    return fn


def test_actual_cost_is_reported_after_a_paid_call(router, warden_on):
    d = run(router, mkstep("list files"), mkctx(router, **{F_TOOLS: True}))
    assert d.model_id in CLOUD_IDS
    _report(router)(d, 0.0123)
    assert warden_on.actuals == [0.0123]


def test_no_report_when_guard_is_off(router, warden_off):
    d = run(router, mkstep("list files"), mkctx(router, **{F_TOOLS: True}))
    _report(router)(d, 0.0123)
    assert warden_off.calls == []


def test_a_local_call_reports_no_spend(router, warden_on):
    d = run(router, mkstep("note"), mkctx(router, **{F_PERSONAL: True, F_LOCAL_UP: True}))
    assert d.model_id in LOCAL_IDS
    _report(router)(d, 0.0)
    assert warden_on.actuals == []


# --------------------------------------------------------------------------- property: invariants over random contexts


def test_property_invariants_hold_for_random_steps_and_contexts(router, warden_on, monkeypatch):
    rng = random.Random(20261010)
    alphabet = "abcdefghij klmnop\n.,?!0123456789é漢"
    for i in range(300):
        tools, personal, up, rule = (rng.random() < 0.5 for _ in range(4))
        spend = rng.choice(["allow", "deny", "raise"])
        guard_on = rng.random() < 0.7
        _install_warden_state(monkeypatch, router, warden_on, guard_on, spend)
        text = "".join(rng.choice(alphabet) for _ in range(rng.choice([0, 1, 2, 10, 200, 3000])))
        step, ctx = mkstep(text, sid=f"p{i}"), mkctx(
            router, **{F_TOOLS: tools, F_PERSONAL: personal, F_LOCAL_UP: up, F_PERSONAL_RULE: rule})
        warden_on.calls.clear()
        before = event_count()
        res = outcome(router, step, ctx)

        assert event_count() > before, "every decision is an event"
        if not guard_on:
            assert warden_on.calls == [], "spend checks run only when TANISHI_GUARD=1"
        if res[0] != "ok":
            continue
        d = res[1]
        assert d.model_id in ALL_IDS and d.provider and d.reason.strip()
        if tools:
            assert d.model_id not in LOCAL_IDS, "tools never go to a tool-less model"
        if personal and rule:
            assert d.model_id not in CLOUD_IDS, "personal never goes to the cloud with the rule on"
        if not up:
            assert d.model_id not in LOCAL_IDS, "a down model is never chosen"
        if d.model_id in CLOUD_IDS and guard_on:
            assert warden_on.estimates, "a paid model needs a check_spend first"
            assert spend == "allow", "a paid model was chosen after the Warden did not allow"
        # idempotence: the same question gets the same answer
        warden_on.calls.clear()
        again = outcome(router, step, ctx)
        assert again[0] == "ok" and again[1].model_id == d.model_id


def _install_warden_state(monkeypatch, router, warden, on, spend):
    from tanishi.tools import registry

    warden.spend = spend
    if on:
        monkeypatch.setenv("TANISHI_GUARD", "1")
    else:
        monkeypatch.delenv("TANISHI_GUARD", raising=False)
    monkeypatch.setattr(registry, "_GUARD_ON", on)
    monkeypatch.setattr(router, "_GUARD_ON", on, raising=False)


def test_property_a_stub_that_always_picks_one_model_fails(router, warden_off):
    """Guard against a degenerate router: across the matrix both a local and a cloud model must appear."""
    seen = set()
    for tools, personal in itertools.product([False, True], repeat=2):
        res = outcome(router, mkstep(), mkctx(router, **{F_TOOLS: tools, F_PERSONAL: personal, F_LOCAL_UP: True}))
        if res[0] == "ok":
            seen.add(res[1].model_id in LOCAL_IDS)
    assert seen == {True, False}
