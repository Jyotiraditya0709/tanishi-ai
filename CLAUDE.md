# Tanishi: rules for every agent working in this repo

Tanishi is an open-source personal AI being built by an AI engineering swarm. You are one agent in that swarm.
The plan of record is the master plan ("Tanishi: The Recursive Staircase"), architecture v1.0, frozen on 2026-10-09.
What to build, in what order, is in `build/graph.yaml`. Your own task is on the card you were given.

## The five rules

1. **Verification sets the speed.** A change counts when it is merged and verified, not when it is written.
2. **You never write your own exam.** Tests for your code come from a different agent. Hidden acceptance tests exist that you cannot see. Do not look for them.
3. **Protected paths are off limits.** Never edit, move or delete anything under:
   `constitution/`, `warden/`, `vault/`, `reality/`, `builder_vault/`, `tanishi/guard/`, `.github/`, `CLAUDE.md`,
   `build/graph.yaml`, `build/agents/`, `scripts/check_protected_paths.py`.
   CI fails any `agent/*` branch that touches them.
4. **Usage is the budget.** If `build/.paused` holds a future time, stop. Do not start long jobs you cannot finish.
5. **Leave the next agent smarter.** Before you finish, answer the four questions in `build/memory/runs/`.

## Where things are

- Product code: `tanishi/` (Python 3.11+). New foundation packages: `tanishi/core_state/`, `tanishi/substrate/`,
  `tanishi/arena/`, `tanishi/observability/`, `tanishi/mirror/`.
- Legacy code that still runs: `tanishi/core/brain.py` (`TanishiBrain.think()`), `tanishi/tools/registry.py`
  (`ToolRegistry.execute()`), `tanishi/memory/`, `tanishi/autoresearch/`. Wire into these only where your spec says.
- Tests: `tests/`, mirroring the package path. Run `python -m pytest -q`.
- Lint: `ruff check <files you touched>`.
- Build memory: `build/memory/` (known bugs, decisions, failed approaches, research, agent performance, runs).

## Hard rules for code

- Never open or write the legacy databases (`data/db.sqlite`, `~/.tanishi/tanishi.db`) from new code. The Core State
  lives in its own database (`TANISHI_CORE_STATE_DB`, default `~/.tanishi/core_state.db`). Tests use a temp path.
- Tests and Arena runs use a temporary `TANISHI_HOME`. Never write to the real `~/.tanishi/` from a test.
- No secrets in code, logs, events or commits. Read keys from the environment by name only.
- No personal conversation text in tasks, fixtures or examples.
- Any step that needs tools must run on a model that supports tools. Personal data goes to a local model only.
- Every tool call goes through `ToolRegistry.execute()`, which goes through the Warden once node W1 lands.
  Never call a tool around it.
- Prefer the standard library. A new dependency needs a line in your PR saying why.

## Git

- Work only on your `agent/<area>/<node>` branch in your own worktree. Commit small. Never push, never touch `main`.
- Commit messages: `<NODE>: what changed and why`.

## When the spec is wrong

Stop and write `build/memory/open-problems/<node>.md` with what is wrong and what you would do instead.
Do not quietly build something else.
