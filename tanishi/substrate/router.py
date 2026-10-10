"""The model router: pick a model for one step by need, privacy and cost (node SUB3).

Rules, in order (decision 0020):

1. A personal step stays local while the rule is on (`personal_local_only`, default on; only the bool `False`
   switches it off, so an unset or malformed value never fails open). The local model has no
   tools, so a personal step that needs tools is refused, and so is a personal step when no local model is up.
   It never falls through to the cloud, and it never asks the Warden to spend.
2. Every other step goes to a paid Claude model, the simple or the complex one by size. This replaces
   `LOCAL_FIRST` in `tanishi/config/routing.py`, which sent short prompts to the tool-less local model.
3. With `TANISHI_GUARD=1`, a paid model is chosen only after `guard.check_spend(estimate)` says allow. On anything
   else (deny, ask, an error) the step falls back to the local model if it needs no tools and one is up, and
   otherwise waits for the cap to reset. It never goes to the paid model.

A refusal raises `RouteRefused`. Every decision, refusals included, is one `model_route` event carrying its reason.
The step text is never logged, since it can be personal. If the event cannot be written, `route()` raises: a decision
that is not in the log does not happen. Model ids come only from `tanishi.config.models`.

`route()` blocks on the Warden's socket when the gate is on. Async callers (the loop, the brain) must use
`aroute()`, which runs it in a thread, as `ToolRegistry.execute()` does for the tool gate. After a paid call, call
`report_spend()`, which writes one `model_spend` event.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass

from tanishi import guard
from tanishi.config import models
from tanishi.config.routing import COMPLEXITY_THRESHOLD_TOKENS
from tanishi.core_state import events
from tanishi.substrate.state import Step

logger = logging.getLogger(__name__)

# The spend gate uses the same switch as the tool gate (tools/registry.py), read once at import.
_GUARD_ON = os.environ.get("TANISHI_GUARD", "").strip().lower() in ("1", "true", "on", "yes")

EVENT_KIND = "model_route"
SPEND_EVENT_KIND = "model_spend"
LOCAL_PROVIDER = "local"  # served by Ollama; the name avoids looking like a model id
PAID_PROVIDER = "anthropic"

# Rough list prices in USD per million tokens (input, output), used only for the estimate sent to the Warden.
# A model missing here is priced as the dearest one, so the estimate errs high.
_PRICES: dict[str, tuple[float, float]] = {
    models.CLAUDE_SIMPLE: (1.0, 5.0),
    models.CLAUDE_DEFAULT: (3.0, 15.0),
    models.CLAUDE_COMPLEX: (5.0, 25.0),
}
_MAX_PRICE = max(_PRICES.values())
EXPECTED_OUTPUT_TOKENS = 1024  # the reply is not known before the call; this is a typical cap


@dataclass(frozen=True)
class RouteContext:
    """What the router needs to know about a step that `Step` does not carry."""

    needs_tools: bool = False
    personal: bool = False
    local_up: bool = True  # a local model is reachable; probing for it is the caller's job
    personal_local_only: bool = True  # only the bool False switches the rule off; None, 0, "" etc. keep it on


@dataclass(frozen=True)
class RouteDecision:
    model_id: str
    provider: str
    reason: str


class RouteRefused(RuntimeError):
    """No model may take this step now. `wait` is True if it may succeed later (local back up, cap reset)."""

    def __init__(self, reason: str, wait: bool):
        super().__init__(reason)
        self.reason = reason
        self.wait = wait


def estimate_tokens(text: str) -> int:
    """About four characters per token, as in the legacy brain."""
    return max(1, len(text) // 4)


def estimate_cost(model_id: str, text: str) -> float:
    """Estimated USD for one call to a paid model: the step text in, a typical reply out."""
    price_in, price_out = _PRICES.get(model_id, _MAX_PRICE)
    return (estimate_tokens(text) * price_in + EXPECTED_OUTPUT_TOKENS * price_out) / 1_000_000


def route(step: Step, context: RouteContext) -> RouteDecision:
    """Pick the model for `step`. Raises `RouteRefused` when it must wait or fail; logs the decision either way."""
    text = step.description
    if not isinstance(text, str):
        raise TypeError("step.description must be a str")
    rule_on = context.personal_local_only is not False  # a privacy rule must not fail open on None or 0
    record: dict = {
        "step_id": step.id,
        "needs_tools": bool(context.needs_tools),
        "personal": bool(context.personal),
        "local_up": bool(context.local_up),
        "personal_local_only": rule_on,
        "guard_on": _GUARD_ON,
    }

    if context.personal and rule_on:
        if context.needs_tools:
            return _refuse(record, "personal step needs tools but the local model has none; "
                                   "personal steps never go to the cloud", wait=False)
        if not context.local_up:
            return _refuse(record, "personal step and no local model is up; waiting, never the cloud", wait=True)
        return _decide(record, _local("personal step stays on the local model"))

    paid = models.CLAUDE_COMPLEX if estimate_tokens(text) >= COMPLEXITY_THRESHOLD_TOKENS else models.CLAUDE_SIMPLE
    why = "needs tools, so a model with tool support" if context.needs_tools else "not personal, so a cloud model"
    if context.personal:
        why += " (personal rule switched off)"

    if _GUARD_ON:
        estimate = estimate_cost(paid, text)
        allowed, warden_reason = _ask_spend(estimate)
        record["spend"] = {"model_id": paid, "estimate": estimate, "allowed": allowed, "reason": warden_reason}
        if not allowed:
            refusal = f"spend not allowed for {paid} ({warden_reason})"
            if context.needs_tools:
                return _refuse(record, f"{refusal}; the step needs tools and the local model has none, "
                                       "so it waits for the cap", wait=True)
            if not context.local_up:
                return _refuse(record, f"{refusal}; no local model is up, so it waits for the cap", wait=True)
            return _decide(record, _local(f"{refusal}; falling back to the local model"))

    return _decide(record, RouteDecision(paid, PAID_PROVIDER, why))


async def aroute(step: Step, context: RouteContext) -> RouteDecision:
    """`route()` for async callers: same contract, run in a worker thread so the Warden's blocking socket call
    (up to its timeout) never stalls the event loop. The current task scope is carried into the thread."""
    return await asyncio.to_thread(route, step, context)


def report_spend(decision: RouteDecision, actual_cost: float, step_id: str | None = None) -> guard.Decision | None:
    """After a paid call, tell the Warden what it really cost. Does nothing for a local call or with the gate off.

    Every report is one `model_spend` event (ids and the amount, never step text). The guard client fails closed by
    returning deny, not by raising, so a report the Warden did not take is logged as a warning and the event says
    `recorded: false`: an undercounted day stays visible. A Warden that raises is handled the same way.
    """
    if isinstance(actual_cost, bool) or not isinstance(actual_cost, int | float) or not math.isfinite(actual_cost) \
            or actual_cost < 0:
        raise ValueError("actual_cost must be a finite number >= 0")
    if not _GUARD_ON or decision.provider != PAID_PROVIDER:
        return None
    cost = float(actual_cost)
    try:
        answer = guard.record_spend(cost)
    except Exception as e:  # noqa: BLE001 - the call is already paid for; record the loss, do not lose the result
        answer = guard.Decision("deny", f"warden error ({type(e).__name__}); spend not recorded")
    if not answer.allowed:
        logger.warning("spend report of %.6f USD for %s not recorded by the Warden (%s): the daily total undercounts",
                       cost, decision.model_id, answer.decision)
    task_id, session_id = events.current_task()
    events.emit(SPEND_EVENT_KIND, {"model_id": decision.model_id, "provider": decision.provider, "actual_cost": cost,
                                   "recorded": answer.allowed, "warden": answer.decision, "warden_reason": answer.reason,
                                   "step_id": step_id, "task_id": task_id}, actor="router", session_id=session_id)
    return answer


def _ask_spend(estimate: float) -> tuple[bool, str]:
    """The Warden's answer to one spend check. Only "allow" allows; an error is a deny."""
    try:
        answer = guard.check_spend(estimate)
    except Exception as e:  # noqa: BLE001 - no answer from the Warden is not permission
        return False, f"warden error ({type(e).__name__}); treated as deny"
    return answer.allowed, f"warden said {answer.decision}: {answer.reason}"


def _local(reason: str) -> RouteDecision:
    return RouteDecision(models.OLLAMA_DEFAULT, LOCAL_PROVIDER, reason)


def _decide(record: dict, decision: RouteDecision) -> RouteDecision:
    _log(record, "routed", decision.reason, decision.model_id, decision.provider)
    return decision


def _refuse(record: dict, reason: str, wait: bool) -> RouteDecision:
    _log(record, "wait" if wait else "fail", reason, None, None)
    raise RouteRefused(reason, wait=wait)


def _log(record: dict, outcome: str, reason: str, model_id: str | None, provider: str | None) -> None:
    task_id, session_id = events.current_task()
    payload = {"outcome": outcome, "model_id": model_id, "provider": provider, "reason": reason,
               "task_id": task_id, **record}
    events.emit(EVENT_KIND, payload, actor="router", session_id=session_id)
