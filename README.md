<p align="center">
  <img src="assets/banner.png" alt="Tanishi" width="800" />
</p>

<h1 align="center">Tanishi</h1>

<p align="center">
  <b>A personal AI that learns from her own failures, builds her own skills, and is being built to improve the system that improves her, with an off-switch that lives outside her.</b>
</p>

<p align="center">
  <em>Local first · Private by default · Your memory stays yours · Built by an AI engineering swarm</em>
</p>

<p align="center">
  <a href="#why-tanishi">Why</a> ·
  <a href="#where-shes-going">Where she's going</a> ·
  <a href="#how-shes-being-built">How she's built</a> ·
  <a href="#safety-the-off-switch-lives-outside-her">Safety</a> ·
  <a href="#measured-results">Results</a> ·
  <a href="#core-systems">Core systems</a> ·
  <a href="#quickstart">Quickstart</a> ·
  <a href="#roadmap">Roadmap</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge" />
  <img src="https://img.shields.io/badge/LLMs-Claude%20%2B%20Ollama-purple?style=for-the-badge" />
  <img src="https://img.shields.io/badge/protocol-MCP%20native-cyan?style=for-the-badge" />
  <img src="https://img.shields.io/badge/tests-1848%20passing-brightgreen?style=for-the-badge" />
  <img src="https://img.shields.io/badge/guard-signed%20policy%20%2B%20kill%20switch-red?style=for-the-badge" />
  <img src="https://img.shields.io/badge/mode-offline--first-green?style=for-the-badge" />
</p>

---

## Why Tanishi

Most AI assistants start from zero every time. They don't learn from their failures, they don't keep the skills they worked out, and their "memory" is old chat history that gets summarised away.

Tanishi is built to **get more capable every day she runs**, and to stay yours while she does:

- **She learns from failure.** After a failed task she writes down what she tried, what broke and why, and reads those lessons before trying again ([Reflexion](https://arxiv.org/abs/2303.11366), Shinn et al., 2023).
- **She keeps skills, not just chats.** A sequence of steps that worked is turned into a reusable skill and brought back when a similar task comes up.
- **She sleeps on it.** Her memory is consolidated nightly and weekly, so she recalls distilled knowledge instead of raw logs.
- **She runs locally.** She can work entirely on local Ollama models, with cloud calls switched off at the routing layer.
- **Your memory never leaves your Tanishi.** Personal memory and identity belong to the person using her, not to a company.

---

## Where she's going

The long-term goal is simple to say and hard to build:

> **Can an intelligence's ability to gain new skills grow faster than the human effort needed to improve it?**

Tanishi's architecture (frozen in [`build/memory/architecture/frozen-v1.md`](build/memory/architecture/frozen-v1.md)) is a staircase toward that:

1. **Foundation.** A shared state she can't silently rewrite, exams she can't game, a reasoning core, and measurement for everything.
2. **Learning.** She turns her own experience into knowledge, skills with tests, training examples and a curriculum built from her failures.
3. **Self-improvement.** Competing versions of her are judged on hidden exams; the better one wins.
4. **Research.** She forms hypotheses about how to get better and tests them.
5. **Meta.** She improves the system that improves her.

The first big milestone, **M1: "the loop closes once"**: a real task fails, she works out why, writes a new skill and its test, passes the practice exams and the sealed exams, and the next task uses the new skill. No human edits any step.

The day-to-day goal sits alongside: a 24/7 personal assistant in the spirit of Friday from *Iron Man*. She keeps you updated, watches what matters to you, and gets better at helping you every night.

---

## How she's being built

Tanishi is built by an **AI engineering swarm**: Claude Code agents running in parallel, each in its own git worktree, with a human as chief architect.

Every piece of work is a node in the **Build Graph** ([`build/graph.yaml`](build/graph.yaml)), and every node goes through the same path:

```
 tester ──► implementer ──► red team ──► repair ──► PR ──► CI gates ──► merge
 (writes    (makes the      (a fresh     (fixes     (human review for
  the exam   exam pass)      agent tries  what the   protected paths)
  first)                     to break it) red team
                                          found)
```

- **Tests come from a different agent than the code** ([decision 0004](build/memory/decisions/0004-tests-from-another-agent.md)), so no agent grades its own work.
- **The red team writes failing proofs** (strict `xfail` tests) for every break it finds. Repair must turn each one green.
- **"Test wins":** if code and exam disagree, the code changes. The exam changes only when a human decision changes it.
- **Decisions are written down** in [`build/memory/decisions/`](build/memory/decisions/), so every agent works from the same record.

### Build Graph

| Tier | What it builds | Status |
|---|---|---|
| **T0: Foundation** | Core State (event log, beliefs, goals, predictions, genome, snapshots), the Arena, the cognitive substrate, observability, the Warden and the off-switches | **18 of 28 merged** |
| **T1: Learning** | Experience compiler, knowledge with contradiction checks, skills as code + test, training examples, self-edits, curriculum from failures | Next |
| **T2: Self-improvement** | Evolution archive, causal attribution, capability graph, unknown and verifier discovery | Later |
| **T3: Research** | Hypothesis and experiment engines, counterfactual replay, self-play | Later |
| **T4: Meta** | Self-extensible compiler, architecture, training, inference and model forges | Later |

**Merged so far in T0:**
- **Builder:** clean ground, protected main with CI gates, builder memory and task templates, the orchestrator.
- **Core State (CS1 to CS7):** schema and migrations, a hash-chained append-only event log, beliefs with evidence, goals, a prediction ledger, a genome record, and snapshots with a Continuity Test.
- **Arena (AR1, AR2):** the task format and runner, plus a practice tier that can't be passed without really solving the tasks.
- **Substrate (SUB1):** working, planning and goal state.
- **Observability (OBS2, OBS3):** North Star numbers and the effort timer, plus the experiment ledger with causal attribution.
- **Warden (W1, W2):** the signed policy, the fail-closed guard, the kill switch and the daily spend cap.

**Next:** the model router (SUB3), the reasoning loop (SUB2), the self model (SUB4), the frontier tier and sealed vault (AR3, AR4), traces (OBS1), hardening (SEC1), the daily brief (OBS4), and **the nightly loop (OBS5)**: the first thing she runs with nobody watching.

---

## Safety: the off-switch lives outside her

An AI that changes itself needs limits it can't change. Tanishi's live in a separate process, **the Warden**, that she can't edit, sign or stop.

```
           ┌───────────────── YOU ─────────────────┐
           │  private signing key   📱 /kill       │
           └──────┬───────────────────────┬────────┘
                  │ signs                 │ Telegram
                  ▼                       ▼
         constitution/policy.yaml    kill switch (separate process)
                  │                       │ writes ~/.tanishi/KILL
                  ▼                       ▼
   ┌──────────────────────────────────────────────────┐
   │                    THE WARDEN                    │
   │ separate process · own repo · Unix socket        │
   │ verifies the signature · hash-chained audit log  │
   └──────────────────────┬───────────────────────────┘
                          │ allow / ask / deny
                          ▼
   Tanishi ── ToolRegistry.execute() ── tanishi/guard (fails closed)
```

- **A signed rulebook.** [`constitution/policy.yaml`](constitution/policy.yaml) gives every tool a rule: `allow`, `ask` (you approve first) or `deny`. Anything unnamed is denied. The Warden obeys the rulebook only while its ed25519 signature checks out, and the private key never touches the repo. Change one letter without re-signing and every action is denied.
- **A secrets drawer.** API keys, SSH keys, cloud logins and the Keychain are out of bounds for every tool, even ones that are otherwise allowed.
- **Fails closed.** If the Warden is down, slow or sends a reply that can't be read, the answer is *deny*.
- **A dead-man file.** While `~/.tanishi/KILL` exists, every tool call is denied.
- **A kill switch on your phone.** `/kill` to her Telegram bot sets the dead-man file and pauses the build swarm within seconds; `/resume` lifts it. Only the owner's account is obeyed. She isn't allowed to stop the kill switch, and macOS restarts it if it dies.
- **A daily spend cap.** Paid model calls are refused once the day's spend would pass the cap in the policy (default $2), and she falls back to local models.
- **A tamper-evident diary.** Every Warden decision is written to an append-only log, each entry chained to the hash of the one before.
- **Protected paths.** The constitution, the guard, the vaults and the CI config can only be changed by a human through a reviewed PR. Agents can't touch them.

The guard is switched on with `TANISHI_GUARD=1`. With it off, which is the default for tests and development, she behaves exactly as before.

---

## Measured Results

The autoresearch self-improvement loop produces measurable, reproducible gains.

**Representative overnight run:**

| Metric | Value |
|---|---|
| Mutation experiments run | 142 |
| Improvements kept (passed gate) | 5 |
| Composite score improvement | **+16.29%** |
| Human intervention | 0 |
| Cloud API cost | $0 (Ollama-judged) |

The full benchmark TSV and mutation logs are committed in [`tanishi/autoresearch/`](tanishi/autoresearch/). Every kept improvement has a snapshot for rollback.

The same system that ran the experiments scored the outcomes locally on Ollama, then either kept the change or reverted it based on the benchmark score.

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│          THE WARDEN  (separate process, signed)       │
└──────────────────────────┬───────────────────────────┘
                           │ every tool call is checked
┌──────────────────────────┴───────────────────────────┐
│                     Orchestrator                      │
│         (Claude API · Ollama local · routing)         │
├──────────────────────────────────────────────────────┤
│   Tool Registry (built-in tool packs + MCP servers)   │
├────────────┬────────────┬─────────────┬──────────────┤
│  Reflexion │   Skill    │   Dream     │   Auto-      │
│   Memory   │ Discovery  │   Memory    │  research    │
├────────────┴────────────┴─────────────┴──────────────┤
│  Core State: events · beliefs · goals · predictions · │
│  genome · snapshots        (SQLite, hash-chained)     │
├──────────────────────────────────────────────────────┤
│  Arena: practice tasks · runner · verifiers           │
│  Observability: North Star · experiments · attribution│
├──────────────────────────────────────────────────────┤
│       WebSocket streaming · CLI · FastAPI server      │
└──────────────────────────────────────────────────────┘
```

| Folder | What lives there |
|---|---|
| [`tanishi/core_state/`](tanishi/core_state/) | Her shared state: event log, beliefs, goals, predictions, genome, snapshots, Continuity Test |
| [`tanishi/arena/`](tanishi/arena/) | The exams: task format, runner, verifiers, practice tier |
| [`tanishi/substrate/`](tanishi/substrate/) | The cognitive core: working, planning and goal state (router and reasoning loop in progress) |
| [`tanishi/observability/`](tanishi/observability/) | North Star numbers, effort timer, experiment ledger, causal attribution |
| [`tanishi/guard/`](tanishi/guard/) | The only client that talks to the Warden (protected) |
| [`constitution/`](constitution/) | The signed policy (protected) |
| [`build/`](build/) | Build Graph, orchestrator, agent cards, builder memory and decisions |

---

## Core Systems

### 1. Reflexion-based failure learning

When a multi-step tool task fails, most agents repeat the same mistake on retry. Tanishi writes a structured retrospective after each failure (*what was attempted, what broke, why*) to an append-only log, and reads the most relevant lessons before her next attempt.

Code: [`tanishi/autoresearch/reflections.py`](tanishi/autoresearch/reflections.py)

### 2. Procedural skill discovery + registry

When a multi-step tool sequence succeeds, Tanishi extracts the *procedural pattern*: not the conversation, but the abstract skill. She indexes it and brings it back into context when a matching request comes in. A skill is a pattern she can run again; a memory is only a record of what happened.

Code: [`tanishi/skills/`](tanishi/skills/)

### 3. Two-stage dream memory

- **Stage 1 (nightly):** extract structured knowledge from the day's conversations.
- **Stage 2 (weekly):** consolidate stage-1 outputs into compact long-term knowledge.

Retrieval hits small, distilled knowledge instead of raw logs: lower token cost, higher signal.

Code: [`tanishi/memory/dream.py`](tanishi/memory/dream.py)

### 4. Autoresearch: the self-improvement loop

A benchmark suite runs on a schedule. The system proposes configuration mutations, runs the benchmark, scores the outcomes locally, and keeps or reverts each change. Every mutation has a snapshot for rollback. This is structured A/B testing of her own configuration, not autonomous training. The Build Graph's T1 and T2 tiers grow it into skills with tests, an evolution archive and judging on hidden exams.

Code: [`tanishi/autoresearch/`](tanishi/autoresearch/)

### 5. Core State and the Arena

Everything she does becomes an event in a hash-chained, append-only log. Every action is preceded by a prediction that gets scored. Beliefs carry their evidence. Snapshots can be restored and checked with a Continuity Test, so you can tell she's still herself after a restore. The Arena holds the exams her improvements must pass, and its practice tasks can't be passed without really solving them.

---

## Offline-first multi-model routing

- **Cloud (Claude):** full capability for complex reasoning.
- **Hybrid:** Claude for reasoning, local Ollama models for cheap operations.
- **Strict offline (Ollama only):** cloud calls disabled at the routing layer, local-only retrieval, made for privacy-sensitive use.

Offline mode is enforced, not aspirational: if a local-only path is unavailable, she fails loudly instead of quietly calling the cloud. The new router (SUB3) extends this: steps marked personal stay local, and paid calls respect the Warden's spend cap.

---

## Features

| Capability | Description |
|---|---|
| **Voice mode** | Real-time TTS + speech recognition with a configurable wake word (default `"Jarvis"`, via Porcupine) |
| **Screen awareness** | Screen capture + Claude Vision for proactive error detection (asks first when the guard is on) |
| **Browser agent** | Visible Playwright-based browser automation |
| **Multi-agent crews** | Splits complex tasks across specialist sub-agents (researcher, coder, analyst, critic) |
| **MCP protocol support** | Connects to any Model Context Protocol server (filesystem, GitHub, Slack, Notion and more) |
| **Finance tracking** | Local expense logging with UPI/INR-aware SMS parsing for Indian banks |
| **Streaming** | WebSocket streaming with canvas and tool-call events |
| **Multiple interfaces** | CLI, FastAPI server, web dashboard, Telegram bot |

The full tool list and each tool's rule are in [`constitution/policy.yaml`](constitution/policy.yaml) and [`docs/TOOL_INVENTORY.md`](docs/TOOL_INVENTORY.md).

---

## Quickstart

### Prerequisites
- Python 3.11+
- [Anthropic API key](https://console.anthropic.com/) (for Claude; optional in offline mode)
- [Ollama](https://ollama.com/) (for offline / hybrid mode)
- Optional: Node.js (for MCP servers), OpenAI API key (for TTS voice)

### Install

```bash
git clone https://github.com/Jyotiraditya0709/tanishi-ai.git
cd tanishi-ai

python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

pip install -e .

cp .env.example .env
# Add your API keys to .env

python -m tanishi.cli
# or: tanishi
```

### API server

```bash
python -m tanishi.api.server
# or: tanishi-server
```

### Offline mode

Set `TANISHI_OFFLINE=1` in your `.env` and make sure Ollama is running with a model pulled. Startup fails loudly if Ollama is unreachable, by design.

### Running with the guard on

Run the Warden (a separate process from its own repo), then start Tanishi with:

```bash
TANISHI_GUARD=1 python -m tanishi.cli
```

Every tool call is then checked against the signed policy. If the Warden isn't running, every tool call is denied. That's the fail-closed design working.

### Tests

```bash
python -m pytest -q
```

---

## Commands

| Command | Description |
|---|---|
| `/help` | Show all commands |
| `/voice` | Enter voice mode (configurable wake word) |
| `/watch` / `/unwatch` | Toggle continuous screen monitoring |
| `/screenshot` | Capture and analyse the current screen |
| `/mcp connect <server>` | Connect to a configured MCP server |
| `/learn` | Run the autonomous improvement cycle |
| `/crew <task>` | Spawn a multi-agent team for a complex task |
| `/memory` | Inspect long-term consolidated memory |
| `/dashboard` | Open the web dashboard |

---

## Roadmap

**Shipped**
- [x] Reflexion-based failure learning
- [x] Procedural skill discovery + registry
- [x] Two-stage dream memory consolidation
- [x] Autoresearch loop with benchmark suite (+16.29% in a 142-experiment run)
- [x] Offline-first multi-model routing
- [x] MCP protocol support, multi-agent crews, WebSocket streaming
- [x] Voice mode, screen awareness, browser agent, Telegram bot
- [x] Architecture v1 frozen, and the Build Graph run by an AI engineering swarm
- [x] Core State: hash-chained event log, beliefs, goals, prediction ledger, genome, snapshots + Continuity Test
- [x] Arena: task format, runner, practice tier
- [x] Observability: North Star numbers, effort timer, experiment ledger, causal attribution
- [x] The Warden: signed policy for every tool, fail-closed guard, secrets drawer, dead-man file, Telegram kill switch, daily spend cap

**Next (T0 foundation)**
- [ ] Model router that respects privacy and the spend cap (SUB3)
- [ ] Explicit reasoning loop (SUB2) and self model (SUB4)
- [ ] Frontier tier and the Sealed Vault (AR3, AR4)
- [ ] Traces (OBS1), hardening for unattended runs (SEC1), daily brief (OBS4)
- [ ] **The nightly loop (OBS5):** consolidate, practise, snapshot and report every night at 02:00. Seven clean nights pass the Phase 0 gate.

**Then**
- [ ] **M1: the loop closes once.** She fixes a real failure with a skill she wrote and tested herself.
- [ ] Learning (T1), self-improvement (T2), research (T3) and meta (T4)
- [ ] Always-on daemon mode, a public eval suite, `pip install tanishi`, a mobile app

---

## Background

Built by [Jyotiraditya](https://github.com/Jyotiraditya0709), an AI engineer in India, as a solo project, with a swarm of AI coding agents doing the building and him as chief architect. It started as a question: could patterns from recent agent research (Reflexion, skill discovery, memory consolidation) be combined into one system that *actually ships*? It's now aiming higher: an assistant whose ability to improve herself can outgrow the effort it takes to improve her.

Feedback, issues and contributions are welcome.

---

## License

This repository is licensed under the terms in [`pyproject.toml`](pyproject.toml). If you'd like to use, redistribute or build on Tanishi, please open an issue first to discuss licensing.

---

<p align="center">
  <a href="https://github.com/Jyotiraditya0709/tanishi-ai">⭐ Star the repo</a> ·
  <a href="https://github.com/Jyotiraditya0709/tanishi-ai/issues">Open an issue</a> ·
  <a href="https://github.com/Jyotiraditya0709">Follow the author</a>
</p>

<p align="center">
  <em>If you like Tanishi, give her a ⭐. She checks.</em>
</p>
