# SUB2 red-team · 2026-10-11

Branch `agent/substrate/sub2` at f81c75f. Tests: `tests/substrate/test_loop_redteam.py` (9 breaks, each `xfail(strict=True)`,
so the fix flips the mark and the suite tells you to remove it). No product code was changed.

## Breaks (most severe first)

| # | Sev | Break | Smallest fix |
|---|-----|-------|--------------|
| B1 | high | `parse_reply` only catches `ValueError`. A reply like `"[" * 100000` makes `json.loads` raise `RecursionError`, which escapes `_deliberate`, `go()` and `run()`: no `loop_stop`, `think()` raises. A degenerate model output crashes the task. | `except (ValueError, RecursionError)` in `parse_reply`; also skip parsing when the body does not start with `{` or a fence. |
| B2 | medium | An empty or whitespace reply is `STOP_ANSWER`, `success=True`, `content=""`. A benchmark counts that as a pass. | Treat a blank reply as a model error (`STOP_MODEL_ERROR`, detail `empty reply`). Same for a non-str reply, which is silently coerced to `""`. |
| B3 | medium | `CancelledError` (timeout, Ctrl-C, task cancel) during `tools.execute` skips `loop_stop` and leaves the `act:` prediction unresolved. `loop_start` with no `loop_stop` looks like a crash, and CS5 sees an unscored act forever. | `try/finally` in `go()` that emits `loop_stop` (reason `cancelled`); resolve `{"success": false}` for an act that started and was cancelled. Re-raise. |
| B6 | medium | `recall` passes the whole task to `MemoryManager.search`, which does `LIKE '%<whole task>%'`. With the real manager a question never matches a stored fact. The exam and impl tests use fakes, so recall passes for the wrong reason and is a silent no-op in production. | Recall by keywords (or `search_local`), merged and deduped; or give `MemoryManager.search` token matching. |
| B7 | medium | `_substrate_enabled()` imports `tanishi.substrate.loop` on every `think()`, flag off included. The docstring says "flag off, nothing changes", but an import error in the substrate (router, state, core_state) breaks the legacy path. | `if os.environ.get("TANISHI_SUBSTRATE", "").strip() != "1": return False` before the import. |
| B8 | medium | Flag on, `load_program()` runs in `CognitiveLoop.__init__`. A bad program file (the file evolution will edit) raises out of `think()`; there is no fallback to the legacy path or an error response. | In `_think_substrate`, catch construction errors and return a failed `BrainResponse` (or fall back to `_think`), and log the program version. |
| B9 | medium | Flag on drops what `_think` adds to the system prompt: matching skills (`find_matching_skills`) and the dream-cycle memory block. The Arena A/B would compare a brain without its learned skills and memories against the legacy one, a false negative for the loop. | Build `merged_extra` the same way `_think` does; best moved to one shared helper so the two paths cannot drift. |
| B4 | low | Reply fenced as ```` ```JSON ```` (upper case) fails to parse and is returned as the final answer: the raw action JSON is shown to the user and counted as success. | Case-insensitive fence tag; when a reply parses as JSON containing `"action"` but is not a valid action, never accept it as the answer. |
| B5 | low | A legitimate JSON final answer with an `"action"` key (e.g. `{"action": "approve", ...}`) is discarded as malformed and the loop retries. | Only treat `action` as a request when it is an object, otherwise fall through to answer; or require the model to put the answer in a fenced field. |

## Tried, nothing found

- Hash chain, duplicate or lost rows: every act writes prediction, `loop_step`, resolve in order; a failed write blocks the act and leaves an unresolved row on purpose (decision 0024 item 5). Fail-closed paths hold.
- Budgets: `max_steps=0`, phase budget 0, `act` missing, `predict` after `act`, a program with no `deliberate` all stop with a reason and never exceed the bound.
- Program validation: bool/float/NaN budgets, bool version, repeated or unknown phase, non-object JSON are refused.
- Secrets and personal data in events: payloads carry no task text, prompt, memory or tool input; the tool name is model-chosen but passes through `events.redact` and the predictions `redact` (clipped at 4000).
- Thread safety of `recall` in `asyncio.to_thread`: `MemoryManager` opens a connection per call, so it is safe.
- Evaluation leakage: the loop reads no Frontier, Vault or exam path.

## Not tested, worth a human decision

- **Personal data from memory or tool output goes to the cloud.** `personal` is decided once from the user input. A benign task whose recalled memory or tool result is private is sent to Claude on the next deliberate. The legacy path has the same trust model (dream context goes to Claude), so this is not a regression, but the loop's router rule cannot see it.
- **No wall-clock bound.** The budget counts passes; a hung `generate` or tool blocks forever. The legacy path has no timeout either.
- **Two near-identical resolved predictions per tool act** (`tool:` and `act:`, same forecast, same outcome) double the sample weight of loop-run tools in calibration. Decision 0024 item 6 chose this; SUB4 should know.
- With `max_steps=N` the model gets N tool calls and no pass left to answer from the last result, so a task that needs all N ends `budget`. Legacy gave N loops plus a final answer. Check this before the Arena A/B.
