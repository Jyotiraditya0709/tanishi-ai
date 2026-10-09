# Builder Arena

The Tanishi Arena asks: did Tanishi become better? The Builder Arena asks: can this coding agent build Tanishi correctly?

## How a round works

1. `python build/orchestrator.py arena-run BA-03 --models opus,sonnet` cuts one worktree per model and gives each the same task card.
2. Each attempt is scored by the hidden acceptance tests in the private repo `tanishi-builder-vault`, which no implementation agent can read.
3. The integration agent records one row per attempt in `build/memory/agent-performance/README.md`: hidden tests passed, regressions, tokens, minutes, estimated cost and your review minutes.
4. The best attempt is merged (BA-01 to BA-06 are node SEC1). The others are kept as branches until the round closes, then deleted.
5. `build/agents/models.yaml` changes only from these scores.

## Growing it

The first 10 tasks are in `tasks.yaml`. Grow to 50 by turning every merged node's hardest test into a new Builder Arena task.
Builder Arena tasks double as Practice tasks for Tanishi, so building Tanishi becomes her first capability family.
When her plans beat the orchestrator's here, she takes over the orchestrator's job.
