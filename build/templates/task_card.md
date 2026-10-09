# Task card · role: {{ROLE}}

{{ROLE_PROMPT}}

---

{{NODE}}

---

## How to work

1. Read `CLAUDE.md`, then the build memory that touches this node: `build/memory/known-bugs/`, `build/memory/decisions/`, `build/memory/failed-approaches/`. Do not repeat a failed approach without saying why it will work this time.
2. You are on branch `{{BRANCH}}` in your own worktree. Commit here. Never push, never touch main.
3. Small steps. After each step, run the tests that cover it.
4. Deliver code, tests and, where the node produces intelligence, a benchmark or evaluation. Never the feature alone.
5. If the spec is wrong or impossible, stop and write why in `build/memory/open-problems/<node>.md`. Do not invent a different task.

## You may not

- Touch these paths, even to fix a typo: {{PROTECTED}}
- Read or search for anything under `vault/` or `builder_vault/`, or try to learn hidden tests.
- Commit secrets, `.env`, any `*.db` or `*.sqlite` file, or personal text from conversations.
- Weaken, skip or delete a test to make it pass.

## Definition of done

{{DONE}}

## The 90-day objective this serves

{{OBJECTIVE}}

## Before you finish: write to build memory

Create `build/memory/runs/<node>-<yyyymmdd-hhmm>.md` with these four answers, short and specific:

1. What did we learn?
2. What failed, and why?
3. Which assumption was wrong?
4. What should the next agent know?

If you found a bug you did not fix, add it to `build/memory/known-bugs/`. If you made a decision another agent must respect, add a numbered file to `build/memory/decisions/`.
