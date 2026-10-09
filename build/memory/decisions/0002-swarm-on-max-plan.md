# 0002 · The build swarm runs on a Claude Max plan

Date: 2026-10-09. Decided by: the human.

Building Tanishi is not limited by the ₹2,000 monthly budget; that budget is for Tanishi's own cloud use once she runs.
The swarm's limit is plan usage: the session allowance resets every five hours, Claude and Claude Code share it,
and weekly or monthly caps can apply. So:

- The orchestrator pauses the swarm (build/.paused) when a run reports a usage limit.
- Start with as many agents as the plan sustains without stalling mid-task; grow only when verified merges per hour of usage say it pays.
- The red-team and competing seats should use a different model (another vendor or a local model) where one is available.
