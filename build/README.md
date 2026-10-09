# Tanishi build kit: start here

This folder turns the master plan into work the AI engineering swarm can do in parallel.

| File | What it is |
| --- | --- |
| `graph.yaml` | The Build Graph: every node with its goal, files, interface, acceptance and dependencies. T0 is fully specced; T1 to T4 are listed in order and get specs when T0 is done. |
| `status.json` | Which nodes are done. Changed by `orchestrator.py status`, never by hand-editing the graph. |
| `orchestrator.py` | Finds ready nodes, cuts worktrees, launches Claude Code headless with a task card, pauses on plan limits. |
| `agents/` | One role prompt per agent type, and `models.yaml` (which model each role uses). |
| `templates/task_card.md` | What every agent is handed: role, spec, rules, definition of done, the four after-task questions. |
| `memory/` | Build memory, seeded with the known bugs, decisions and the autoresearch lessons. |
| `arena/` | Builder Arena v0: ten real tasks for measuring the coding agents. Hidden tests live in a separate private repo. |

Root files in the kit: `CLAUDE.md` (rules every agent loads), `.github/workflows/ci.yml` (the gates),
`.github/CODEOWNERS` (your review on protected paths), `scripts/check_protected_paths.py`, `tests/build/test_graph.py`.

## Day 1, in order

1. **B0, you: clean ground.** Follow the steps on `python build/orchestrator.py card B0`. Do this before copying the kit in,
   because it rewrites history.
2. **Copy the kit into the repo root** and commit it on a `human/build-kit` branch, then merge it.
3. **B1, you: protect main.** In GitHub settings, protect `main`: require a PR, require the `ci / gates` check, require code-owner review, block force pushes.
4. **Install the tools**: `pip install -e . pytest ruff pyyaml hypothesis`, plus the `claude` CLI logged in with your Max plan, plus `gh` for PRs.
5. **Check the graph**: `python build/orchestrator.py validate` and `python build/orchestrator.py plan`.
6. **Start W1 (Warden) yourself.** It is yours alone; an agent may draft on a `human/warden` branch, and you read every line.
7. **Create the private repo `tanishi-builder-vault`** from the vault seed, and have a tester session write the hidden tests for BA-01 to BA-10.
8. **First swarm batch** (once B1 is done, `ready` lists them):

   ```bash
   python build/orchestrator.py ready
   python build/orchestrator.py launch CS1 --role implementer
   python build/orchestrator.py launch CS1 --role tester        # tests from the spec, same worktree is fine for v0
   python build/orchestrator.py arena-run BA-01 --models opus,sonnet
   python build/orchestrator.py arena-run BA-03 --models opus,sonnet
   ```

   Start with 3 to 5 runs at once and watch `python build/orchestrator.py usage`. Add more only when merges keep up.
9. **Red team and merge.** `launch <node> --role redteam` on each finished branch, then `pr <node>`, review, merge, and `status <node> done`.
10. **Night.** Leave the swarm on benchmarks, red-team and repairs. In the morning, read the runs in `build/memory/runs/` and pick the next batch with `ready`.

## How a node moves

```text
todo → launch (implementer) → tests from tester → redteam → pr → CI gates → your review → merge → status done
```

A node is ready only when everything in its `depends_on` is done. Nodes owned by you (`owner: human`, or `protected: true`)
are never launched by the orchestrator.

## Usage limits

The swarm runs on your Claude Max plan. When a run reports a usage limit, the orchestrator writes `build/.paused` with the time
five hours ahead and refuses to launch until then. `usage` shows runs, agent-minutes and errors for the last five hours and seven days.
The cost figures there are Claude Code's own client-side estimates, useful only as a rough measure of how much of the plan a node used.

`--unattended` adds `--permission-prompts none`, which needs Claude Code v2.1.259 or later.
