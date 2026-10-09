# Autoresearch v0 (April 2026): what not to repeat

142 experiments in `autoresearch_results/results.tsv`: 5 kept (1 baseline + 4), 92 discarded, 45 crashed.
The +16.29 points (0.6934 to 0.8563) all came on 2026-04-07; nothing was kept after that.

Why most of it measured nothing:

- **Keep threshold far below noise.** A change was kept at +0.001 while run-to-run noise was about 0.1. Use OBS3: at least 3 seeds, gains under twice the noise are noise.
- **An LLM judged correctness.** The judge was a local model with a Haiku fallback. Use code verifiers wherever the answer can be checked.
- **Tool tasks were faked.** In Ollama mode the harness ran the expected tool itself. Verifiers must check the tool was really called.
- **Failures scored positively.** An empty or crashed benchmark still got a positive score. A crashed run scores 0.
- **Mutations hit dead files.** 3 of 8 mutation areas targeted unused config; one "keep" changed a dead file. Mutate only code that the run path imports.
- **No isolation.** The benchmark wrote 17 "skills" into the real `~/.tanishi/skills`, which now leak into real chats. Every run gets a temp `TANISHI_HOME`.
- **LLM text written into .py files with no compile check**, and the proposer's output was discarded anyway.
- **No git integration.** Kept changes stayed as uncommitted edits. Every kept change must be a commit with a Genome record.
