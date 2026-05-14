<p align="center">
  <img src="assets/banner.png" alt="Tanishi" width="800" />
</p>

<h1 align="center">Tanishi</h1>

<p align="center">
  <b>An autonomous AI agent with self-improving memory, skill discovery, and offline-first multi-model orchestration.</b>
</p>

<p align="center">
  <em>Reflexion-based learning · Procedural skill discovery · Two-stage memory consolidation · Local + cloud routing</em>
</p>

<p align="center">
  <a href="#why-tanishi">Why</a> ·
  <a href="#measured-results">Measured Results</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#core-systems">Core Systems</a> ·
  <a href="#demo">Demo</a> ·
  <a href="#quickstart">Quickstart</a> ·
  <a href="#features">Features</a> ·
  <a href="#roadmap">Roadmap</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge" />
  <img src="https://img.shields.io/badge/LLMs-Claude%20%2B%20Ollama-purple?style=for-the-badge" />
  <img src="https://img.shields.io/badge/protocol-MCP%20native-cyan?style=for-the-badge" />
  <img src="https://img.shields.io/badge/memory-Reflexion%20%2B%20Dream-orange?style=for-the-badge" />
  <img src="https://img.shields.io/badge/mode-offline--first-green?style=for-the-badge" />
</p>

---

## Why Tanishi

Most AI agents today are stateless: every run starts from zero. They don't learn from their failures, they don't accumulate reusable skills across sessions, and their "memory" is just appended conversation history that gets summarised when context windows fill up.

Tanishi is an attempt to build an agent that **gets monotonically more capable over time** by implementing patterns inspired by recent agent research:

- **Reflexion** (Shinn et al., 2023) — agents that learn from their own failures by maintaining structured retrospectives
- **Procedural skill discovery** — abstract skills extracted from successful runs, indexed, and reinjected as context for future tasks
- **Two-stage memory consolidation** — short-term episodic memory + long-term consolidated knowledge, inspired by sleep-based memory consolidation in cognitive science

Built in Python with full offline-first support — Tanishi can run entirely on local Ollama models with strict cloud-call disabling, designed for privacy-sensitive deployments.

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

The full benchmark TSV and mutation logs are committed in [`tanishi/autoresearch/`](tanishi/autoresearch/) — every kept improvement has a snapshot for rollback.

This is the agent improving itself overnight. The same system that ran the experiments scored the outcomes locally on Ollama, and either committed the change or reverted it based on benchmark score.

---

## Demo

> 🎬 **Video walkthrough coming soon** — Reflexion failure-learning · skill discovery in action · offline-first multi-model routing · the overnight autoresearch loop.

Try it yourself — see [Quickstart](#quickstart).

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                     Orchestrator                      │
│         (Claude API · Ollama local · routing)         │
├──────────────────────────────────────────────────────┤
│   Tool Registry (built-in tool packs + MCP servers)   │
├────────────┬────────────┬─────────────┬──────────────┤
│  Reflexion │   Skill    │   Dream     │   Auto-      │
│   Memory   │ Discovery  │   Memory    │  research    │
│  (failure  │ (success → │ (consolida- │ (mutation +  │
│  logs →    │ procedural │  tion loop) │  benchmarks) │
│  lessons)  │  skills)   │             │              │
├────────────┴────────────┴─────────────┴──────────────┤
│       WebSocket streaming · CLI · FastAPI server      │
└──────────────────────────────────────────────────────┘
```

Each subsystem is independently toggleable. The orchestrator stays simple; the intelligence lives in the memory and skill systems around it.

---

## Core Systems

The four systems that make Tanishi different from a wrapped-LLM-with-tools.

### 1. Reflexion-Based Failure Learning

When a multi-step tool task fails, most agents repeat the same failure on retry. Reflexion solves this by writing structured retrospectives after failures and conditioning future generations on past lessons.

Tanishi implements this with an append-only JSONL log per agent. After each failed run, the agent extracts: *what was attempted, what broke, why*. Subsequent mutation proposals read the last N relevant failures before generating new actions.

Code: [`tanishi/autoresearch/reflections.py`](tanishi/autoresearch/reflections.py)
Paper: Shinn et al., *Reflexion: Language Agents with Verbal Reinforcement Learning*, 2023.

### 2. Procedural Skill Discovery + Registry

When a multi-step tool sequence succeeds, Tanishi extracts the *procedural pattern* — not the conversation, but the abstract skill — and indexes it. Future runs match the user's request against indexed skills and inject the matched skill into context.

This is meaningfully different from raw conversation-history memory: skills generalise across sessions, while memory only resurfaces prior conversation. A skill is an executable pattern; a memory is a record.

Code: [`tanishi/skills/`](tanishi/skills/) — registry, extraction, manifest format, and runtime injection.

### 3. Two-Stage Dream Memory

Most agent memory dumps conversation history into a vector DB. This works for retrieval but bloats context and degrades over time as old conversations crowd useful information.

Tanishi runs a two-stage consolidation loop inspired by sleep-based memory consolidation in cognitive science:

- **Stage 1 (nightly):** extract structured knowledge from the day's conversations
- **Stage 2 (weekly):** consolidate stage-1 outputs into compact long-term knowledge

The result: retrieval queries hit small, distilled knowledge instead of raw conversation logs. Lower token cost, higher signal.

Code: [`tanishi/memory/dream.py`](tanishi/memory/dream.py)

### 4. Autoresearch — Self-Improvement Loop

A small benchmark suite runs on a schedule. The system proposes config mutations (prompt changes, weighting changes), runs the benchmark, scores outcomes locally on Ollama, and either keeps or reverts. Every mutation has a snapshot for rollback safety.

This is *not* autonomous training — it's structured A/B testing of the agent's own configuration. Useful for tuning behavior without manual intervention; safe because every change is reversible. See [Measured Results](#measured-results) above for an example overnight run.

Code: [`tanishi/autoresearch/`](tanishi/autoresearch/)

---

## Offline-First Multi-Model Routing

Tanishi runs in three modes:

- **Cloud (Claude)** — full capability, lowest latency for complex reasoning
- **Hybrid** — Claude for reasoning, local Ollama models for cheap operations
- **Strict offline (Ollama only)** — cloud calls disabled at the routing layer, cached web-search fallback, local-only retrieval. Designed for privacy-sensitive use cases.

Offline mode is enforced, not aspirational: a flag at startup disables all cloud routes, and the system fails loudly if a local-only path is unavailable.

---

## Features

Beyond the core systems above, Tanishi includes a broad surface of practical capabilities:

| Capability | Description |
|---|---|
| **Voice mode** | Real-time TTS + speech recognition with configurable wake word (default: `"Jarvis"` via Porcupine) |
| **Screen awareness** | Periodic screen capture + Claude Vision analysis for proactive error detection |
| **Browser agent** | Visible Playwright-based browser automation for web tasks |
| **Multi-agent crews** | Decomposes complex tasks and spawns specialist sub-agents (researcher, coder, analyst, critic) |
| **MCP protocol support** | Connects to any Model Context Protocol-compatible server (filesystem, GitHub, Slack, Notion, and 2000+ others in the MCP ecosystem) |
| **Finance tracking** | Local expense logging with UPI/INR-aware SMS parsing (Indian banks) |
| **Streaming** | WebSocket-based streaming protocol; richer event types (canvas, tool-call events) in progress |
| **Multiple interfaces** | CLI, FastAPI server, web dashboard, Telegram bot |

---

## Quickstart

### Prerequisites
- Python 3.11+
- [Anthropic API key](https://console.anthropic.com/) (for Claude, optional in offline mode)
- [Ollama](https://ollama.com/) (for offline / hybrid mode)
- Optional: Node.js (for MCP servers), OpenAI API key (for TTS voice)

### Install

```bash
git clone https://github.com/Jyotiraditya0709/tanishi-ai.git
cd tanishi-ai

python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# Editable install (matches pyproject.toml)
pip install -e .

cp .env.example .env
# Add your API keys to .env

# Launch
python -m tanishi.cli
# or: tanishi (console script)
```

### API Server

```bash
python -m tanishi.api.server
# or: tanishi-server
```

### Offline Mode

Set `TANISHI_OFFLINE=1` in your `.env` and ensure Ollama is running locally with a pulled model. The startup will fail loudly if Ollama is unreachable — by design.

---

## Evaluation

Tanishi's autoresearch loop uses a fixed benchmark suite to score config mutations on quality and latency. The benchmark logs every experiment to a TSV file along with which mutations were kept and which were reverted — see [`tanishi/autoresearch/`](tanishi/autoresearch/).

A separate audit of the registered tool surface and benchmark coverage is in [`docs/TOOL_INVENTORY.md`](docs/TOOL_INVENTORY.md).

---

## Commands

| Command | Description |
|---|---|
| `/help` | Show all commands |
| `/voice` | Enter voice mode (configurable wake word) |
| `/watch` / `/unwatch` | Toggle continuous screen monitoring |
| `/screenshot` | Capture and analyse current screen |
| `/mcp connect <server>` | Connect to a configured MCP server |
| `/learn` | Run the autonomous improvement cycle |
| `/crew <task>` | Spawn a multi-agent team for a complex task |
| `/memory` | Inspect long-term consolidated memory |
| `/dashboard` | Open web dashboard |

---

## Roadmap

**Shipped**
- [x] Reflexion-based failure learning
- [x] Procedural skill discovery + registry
- [x] Two-stage Dream memory consolidation
- [x] Autoresearch loop with benchmark suite (measured +16.29% in 142-experiment run)
- [x] Offline-first multi-model routing
- [x] MCP protocol support
- [x] Multi-agent crews
- [x] WebSocket streaming
- [x] Voice mode (speech recognition + TTS)
- [x] Screen awareness (Claude Vision)
- [x] Browser agent (Playwright)
- [x] Telegram bot interface

**In progress**
- [ ] Public eval suite — golden set with relevance/faithfulness/tool-call accuracy scoring
- [ ] Frontend canvas rendering (Mermaid, Chart.js, sandboxed HTML)
- [ ] Structured logging + production metrics
- [ ] `pip install tanishi` distribution
- [ ] Always-on daemon mode
- [ ] Mobile app (React Native)

---

## Background

Built by [Jyotiraditya](https://github.com/Jyotiraditya0709), a CS student in India, over several intensive weeks. The goal was to explore whether agent research patterns from recent papers (Reflexion, skill discovery, memory consolidation) could be combined into a single coherent system that *actually ships* — not just reproduces a benchmark.

This is a solo project. Feedback, issues, and contributions welcome.

---

## License

This repository is licensed under terms specified in [`pyproject.toml`](pyproject.toml). If you'd like to use, redistribute, or build on Tanishi, please open an issue first to discuss licensing.

---

<p align="center">
  <a href="https://github.com/Jyotiraditya0709/tanishi-ai">⭐ Star the repo</a> ·
  <a href="https://github.com/Jyotiraditya0709/tanishi-ai/issues">Open an issue</a> ·
  <a href="https://github.com/Jyotiraditya0709">Follow the author</a>
</p>

<p align="center">
  <em>If you like Tanishi, give her a ⭐ — she checks.</em>
</p>