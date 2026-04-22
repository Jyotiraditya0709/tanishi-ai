# Tanishi Comprehensive Improvement Plan

## Table Of Contents
- [Executive Summary](#executive-summary)
- [Phase 1 - Full Codebase Scan](#phase-1---full-codebase-scan)
  - [tanishi/core](#tanishicore)
  - [tanishi/tools](#tanishitools)
  - [tanishi/voice](#tanishivoice)
  - [tanishi/autoresearch](#tanishiautoresearch)
  - [tanishi/proactive](#tanishiproactive)
  - [tanishi/config](#tanishiconfig)
  - [tanishi/memory](#tanishimemory)
  - [tanishi/api](#tanishiapi)
  - [tanishi/bridges](#tanishibridges)
  - [tanishi/desktop](#tanishidesktop)
  - [tanishi/dashboard](#tanishidashboard)
  - [tanishi root package](#tanishi-root-package)
  - [Missing expected directories](#missing-expected-directories)
- [Phase 2 - Tool Inventory Summary](#phase-2---tool-inventory-summary)
- [Phase 3 - Architecture Gaps vs Top Assistants](#phase-3---architecture-gaps-vs-top-assistants)
- [Phase 4 - Prioritized Improvement Roadmap](#phase-4---prioritized-improvement-roadmap)
  - [Tier 1 - Ship This Week](#tier-1---ship-this-week-high-impact--4-hours-each)
  - [Tier 2 - Ship This Month](#tier-2---ship-this-month)
  - [Tier 3 - Ship In 60 Days](#tier-3---ship-in-60-days)
  - [Tier 4 - Long-Term Vision](#tier-4---long-term-vision-3-6-months)
- [Phase 5 - Top 10 Quick Wins (Under 30 Minutes)](#phase-5---top-10-quick-wins-under-30-minutes)

## Executive Summary
Tanishi already has strong foundations: multi-provider LLM routing, tool execution, memory/trust data models, proactive daemon flows, voice interfaces, and an autoresearch loop. The main blockers are wiring and operational quality: several config modules are not connected to runtime behavior, benchmark tooling does not verify tool usage, some features are partially implemented, and cross-interface behavior (CLI vs API vs bridge) is inconsistent.

The highest-leverage work is to make behavior deterministic and observable: unify tool registration, enforce real tool usage for realtime questions, fix benchmark validity, and harden API/runtime dependencies. This plan is designed so each task can be started directly from file-level targets.

## Phase 1 - Full Codebase Scan

### `tanishi/core`
**Files and purpose**
- `tanishi/core/__init__.py`: global runtime settings (`TanishiConfig`) and path/bootstrap setup.
- `tanishi/core/brain.py`: central routing + Claude tool loop + Ollama chat fallback.
- `tanishi/core/personality.py`: system prompt and prompt builders.
- `tanishi/core/autonomy.py`: task scheduler + notification loop.

**What works well**
- Brain routing pipeline is clear and mostly robust.
- Claude tool loop supports iterative tool calls with safety cap.
- Config bootstrap creates required runtime directories.

**Broken or half-built**
- `think(style=...)` in `brain.py` is unused.
- Exception swallowing in autonomy hides runtime failures.
- Some behavior relies on prompt-only policy rather than hard enforcement.

**What's missing**
- Structured logs/metrics across core loops.
- Strong policy enforcement layer (privacy/trust/tool constraints).
- Full integration of mutable `tanishi/config/*` runtime knobs.

### `tanishi/tools`
**Files and purpose**
- `tanishi/tools/__init__.py`: package marker.
- `tanishi/tools/registry.py`: tool model + registry + execution.
- `tanishi/tools/web_search.py`: DDG search + webpage fetch.
- `tanishi/tools/filesystem.py`: read/write/list/search files.
- `tanishi/tools/system_tools.py`: shell, datetime, system info.
- `tanishi/tools/self_improve.py`: GitHub trend scan/improvement scan.
- `tanishi/tools/screenshot.py`: screenshot + vision summary.
- `tanishi/tools/email_tools.py`: Gmail read/send/search.
- `tanishi/tools/windows_auto.py`: local app/system automation.
- `tanishi/tools/browser_agent.py`: Playwright browser actions.
- `tanishi/tools/finance.py`: local expense and budget tools.
- `tanishi/tools/multi_agent.py`: specialist-agent decomposition tools.
- `tanishi/tools/autonomous_learn.py`: autonomous learning loop tools.
- `tanishi/tools/mcp_client.py`: MCP connectivity + dynamic tool exposure.

**What works well**
- Large breadth of tooling already exists.
- Registry abstraction is simple and extensible.
- Optional-tool loading pattern allows partial environments.

**Broken or half-built**
- Tool schema/handler mismatches in a few tools.
- Interface asymmetry: CLI gets broad tools, API/Telegram get reduced sets.
- Some tool modules are dependency-heavy and fail silently on missing packages.

**What's missing**
- Tool health diagnostics endpoint/check.
- Consistent schema validation and error shape.
- Test coverage for each tool pack.

### `tanishi/voice`
**Files and purpose**
- `tanishi/voice/__init__.py`: voice module overview.
- `tanishi/voice/listener.py`: speech input with fallback STT.
- `tanishi/voice/speaker.py`: TTS with backend fallbacks.
- `tanishi/voice/pipeline.py`: voice loop orchestration.
- `tanishi/voice/realtime.py`: OpenAI realtime speech mode.
- `tanishi/voice/voice_config.py`: mutable voice params (autoresearch target).

**What works well**
- Multi-backend STT/TTS fallback architecture.
- Realtime voice mode exists.
- Voice pipeline is operationally separated from core brain.

**Broken or half-built**
- `voice_config.py` tuning is weakly wired into runtime behavior.
- Streaming UX is still mostly request/response, not true incremental output.

**What's missing**
- Device permission/availability preflight.
- Voice integration tests and startup readiness checks.

### `tanishi/autoresearch`
**Files and purpose**
- `tanishi/autoresearch/__init__.py`: package marker.
- `tanishi/autoresearch/README.md`: usage docs.
- `tanishi/autoresearch/autoresearch.py`: main mutate-benchmark loop.
- `tanishi/autoresearch/benchmark.py`: benchmark tasks and scoring harness.
- `tanishi/autoresearch/benchmark.py.bak`: backup old benchmark code.
- `tanishi/autoresearch/mutator.py`: mutation proposals and application.
- `tanishi/autoresearch/scorer.py`: composite score weights and normalization.
- `tanishi/autoresearch/setup_configs.py`: helper/bootstrap for mutable config files.

**What works well**
- End-to-end loop exists (snapshot, mutate, benchmark, keep/revert, log).
- Composite scoring and results logging are easy to inspect.

**Broken or half-built**
- Experiment areas include `tool_descriptions` but mutator lacks that ruleset.
- LLM proposer path exists but does not apply LLM proposal.
- Benchmark tracks expected tools but does not assert tool usage.

**What's missing**
- Deterministic benchmark protocol (repeat runs, variance handling).
- Auto-skip of invalid mutation areas.
- CI guardrails around benchmark integrity.

### `tanishi/proactive`
**Files and purpose**
- `tanishi/proactive/__init__.py`: package marker.
- `tanishi/proactive/README.md`: proactive setup docs.
- `tanishi/proactive/run_proactive.py`: daemon orchestrator.
- `tanishi/proactive/sentinel.py`: periodic alert checks.
- `tanishi/proactive/daily_briefing.py`: briefing data collection and synthesis.
- `tanishi/proactive/wake_word.py`: Picovoice wake-word listener.
- `tanishi/proactive/proactive_speak.py`: proactive speaking path.
- `tanishi/proactive/calendar_helper.py`: local calendar utilities.

**What works well**
- Sentinel + briefing + wake-word architecture is modular.
- Cooldown handling and persisted proactive state are implemented.

**Broken or half-built**
- Calendar integration is local JSON stub, not production calendar sync.
- Wake-word path depends on Picovoice access key and local audio setup.
- Proactive speak has import-time side effects that complicate ops.

**What's missing**
- Health monitoring for long-running daemon threads.
- Centralized backoff/retry for external feeds.
- Robust supervisor integration (launchd/systemd style).

### `tanishi/config`
**Files and purpose**
- `tanishi/config/__init__.py`: package marker.
- `tanishi/config/routing.py`: routing constants used by brain.
- `tanishi/config/prompts.py`: mutable prompts file (currently broken syntax).
- `tanishi/config/personality.py`: personality constants for experiments.
- `tanishi/config/memory_params.py`: memory tuning constants.
- `tanishi/config/tool_params.py`: tool runtime tuning constants.

**What works well**
- Routing constants now influence runtime selection.
- Autoresearch mutation targets are centralized.

**Broken or half-built**
- `prompts.py` has malformed content.
- Several mutable config files are not consumed by runtime paths.

**What's missing**
- Validation and import tests for all config files.
- End-to-end wiring from config constants into execution.

### `tanishi/memory`
**Files and purpose**
- `tanishi/memory/__init__.py`: package marker.
- `tanishi/memory/manager.py`: SQLite memory CRUD and retrieval.
- `tanishi/memory/trust.py`: trust tiers/secrets/contact models.
- `tanishi/memory/auto_learn.py`: auto extraction of memory items.

**What works well**
- Persistent memory core is in place.
- Trust schema exists with explicit tiers.

**Broken or half-built**
- Retrieval is mostly lexical, not semantic.
- Trust enforcement is not uniformly applied across all channels.

**What's missing**
- Embeddings/vector retrieval path.
- Data encryption and retention controls.

### `tanishi/api`
**Files and purpose**
- `tanishi/api/__init__.py`: package marker.
- `tanishi/api/server.py`: FastAPI server and websocket endpoints.

**What works well**
- API exposes chat/status/memory/tasks/notifications.
- Startup initializes brain/memory/autonomy.

**Broken or half-built**
- Open CORS and no auth.
- Some endpoints are loose dict-based contracts.

**What's missing**
- Authentication, rate limiting, and request auditing.
- Session/user isolation for multi-client scenarios.

### `tanishi/bridges`
**Files and purpose**
- `tanishi/bridges/__init__.py`: package marker.
- `tanishi/bridges/telegram_bot.py`: Telegram bridge.

**What works well**
- Telegram integration route exists and uses same brain/memory concepts.

**Broken or half-built**
- Bridge ecosystem is single-channel.

**What's missing**
- WhatsApp/SMS channel adapters.
- Shared bridge framework for retries and observability.

### `tanishi/desktop`
**Files and purpose**
- `tanishi/desktop/__init__.py`: package marker.
- `tanishi/desktop/avatar.py`: local avatar service launcher.
- `tanishi/desktop/avatar.html`: avatar UI asset.
- `tanishi/desktop/screen_watcher.py`: screen monitoring loop.
- `tanishi/desktop/tray.py`: system tray process.

**What works well**
- Desktop presence stack exists (tray + avatar + watcher).

**Broken or half-built**
- Cross-platform launch assumptions are brittle.
- Limited controls around continuous monitoring.

**What's missing**
- User consent/permissions UX and persistent preferences.
- Unified desktop supervisor process.

### `tanishi/dashboard`
**Files and purpose**
- `tanishi/dashboard/index.html`: web dashboard frontend.

**What works well**
- Dashboard surfaces chat/status/memory/tasks quickly.

**Broken or half-built**
- Websocket capability is not fully used.

**What's missing**
- Auth/session management.
- Better state sync and error handling in frontend.

### `tanishi` root package
**Files and purpose**
- `tanishi/__init__.py`: package identity.
- `tanishi/cli.py`: main CLI runtime.
- `tanishi/cli.py~`: backup file (should not be shipped).

**What works well**
- CLI orchestrates most existing subsystems.

**Broken or half-built**
- CLI commands reference non-existent `tanishi.core.multi_agent`.

**What's missing**
- Startup diagnostics for missing optional dependencies.
- Cleanup of backup/dead files.

### Missing expected directories
- Missing: `tanishi/agents/`
- Missing: `tanishi/multi_agent/` (tool module exists under `tanishi/tools/multi_agent.py`)
- Missing: `tanishi/server/` (server functionality lives in `tanishi/api/server.py`)

## Phase 2 - Tool Inventory Summary
Full inventory is in `docs/TOOL_INVENTORY.md`. Key points:
- 40+ named tools plus dynamic MCP tools are present.
- Only `get_datetime` and `get_system_info` are represented in benchmark tasks.
- Tool quality is uneven because of dependency variability and interface-specific registration.
- Highest-value stabilization area: tool health checks + benchmark enforcement of actual tool invocation.

## Phase 3 - Architecture Gaps vs Top Assistants

| Capability | Tanishi status | Completeness | What is needed to ship |
|---|---|---:|---|
| Proactive alerts and monitoring | Present (`proactive/sentinel.py`) | 70% | better daemon reliability + alert quality tuning + health telemetry |
| Daily briefing with real data | Present (`proactive/daily_briefing.py`) | 75% | stronger retries/cache + configurable locations/sources |
| Self-improvement loop | Present (`autoresearch/*`) | 55% | fix invalid mutation areas + benchmark validity + deterministic protocol |
| Wake-word activation | Present but key/deps gated | 45% | onboarding wizard for Picovoice/audio + robust fallback modes |
| Multi-agent task delegation | Present in tools, partial in CLI | 40% | unify module path, add robust planner/executor contracts |
| Deep research mode | Partial via tools/multi-agent + web tools | 35% | dedicated research pipeline (parallel fetch, synthesis, citations) |
| Browser autonomous agent | Present (`tools/browser_agent.py`) | 55% | selector hardening + stateful plans + anti-flake retries |
| Email/calendar/messaging integration | Email + Telegram + local calendar stub | 45% | Google Calendar real integration + WhatsApp/SMS bridge |
| Local screen/audio recording + searchable recall | Partial screen watcher only | 20% | capture service + indexing pipeline + query UX |
| Offline mode (Gemma/local) | Partial routing to Ollama | 40% | local-tool parity, offline-safe tools, startup mode policy |
| Phone/WhatsApp/SMS interface | Not shipped | 10% | Twilio/WhatsApp adapter + auth + event routing |
| Weekly self-improvement report | Not shipped | 15% | scheduled analytics from autoresearch logs + digest delivery |
| Plugin/skill ecosystem | Early MCP support | 30% | stable extension API, versioning, docs, validation pipeline |

## Phase 4 - Prioritized Improvement Roadmap

### Tier 1 - Ship This Week (high impact, <4 hours each)

1) **Fix benchmark validity and crash loops**
- What to build: make autoresearch skip invalid areas, enforce `expected_tool` checks, and fail clearly when areas have no rules.
- Files: `tanishi/autoresearch/autoresearch.py`, `tanishi/autoresearch/mutator.py`, `tanishi/autoresearch/benchmark.py`
- Estimated hours: 3.5
- Dependencies: none
- UX impact: 9/10

2) **Add tool registration parity baseline**
- What to build: central function to register core tool packs consistently across CLI/API/bridges/standalone brain.
- Files: `tanishi/cli.py`, `tanishi/api/server.py`, `tanishi/bridges/telegram_bot.py`, `tanishi/core/brain.py`
- Estimated hours: 3
- Dependencies: none
- UX impact: 8/10

3) **Repair broken config prompt module**
- What to build: fix `tanishi/config/prompts.py` syntax and add import check in tests/startup self-check.
- Files: `tanishi/config/prompts.py`, `tanishi/cli.py` (startup check)
- Estimated hours: 1.5
- Dependencies: none
- UX impact: 7/10

4) **Harden realtime data behavior**
- What to build: ensure all realtime intents force tool calls and add graceful tool-failure messaging templates.
- Files: `tanishi/core/brain.py`, `tanishi/core/personality.py`
- Estimated hours: 2
- Dependencies: Anthropic key for Claude tool loop
- UX impact: 8/10

5) **API security quick hardening**
- What to build: add optional API key auth, restrict CORS by config, and basic request logging.
- Files: `tanishi/api/server.py`, `tanishi/core/__init__.py`
- Estimated hours: 4
- Dependencies: API key provisioning
- UX impact: 8/10

### Tier 2 - Ship This Month

1) **Semantic memory retrieval**
- What to build: embeddings-based retrieval with lexical fallback and score-based memory ranking.
- Files: `tanishi/memory/manager.py`, `tanishi/memory/auto_learn.py`, `tanishi/config/memory_params.py`
- Estimated hours: 24
- Dependencies: local embedding model or API key
- UX impact: 9/10

2) **Google Calendar real integration**
- What to build: replace local calendar stub with OAuth sync and upcoming-event APIs used by proactive.
- Files: `tanishi/proactive/calendar_helper.py`, `tanishi/proactive/sentinel.py`, `tanishi/proactive/daily_briefing.py`
- Estimated hours: 14
- Dependencies: Google OAuth credentials
- UX impact: 8/10

3) **Deep research mode**
- What to build: explicit workflow that launches multiple web/browser passes, consolidates findings, and outputs structured reports.
- Files: `tanishi/tools/multi_agent.py`, `tanishi/tools/web_search.py`, `tanishi/tools/browser_agent.py`, `tanishi/cli.py`
- Estimated hours: 20
- Dependencies: Anthropic key, Playwright setup
- UX impact: 9/10

4) **Voice reliability pass**
- What to build: startup capability checker, clearer fallback policy, and unified voice config wiring.
- Files: `tanishi/voice/listener.py`, `tanishi/voice/speaker.py`, `tanishi/voice/pipeline.py`, `tanishi/voice/voice_config.py`
- Estimated hours: 16
- Dependencies: audio drivers, OpenAI key optional
- UX impact: 7/10

5) **Operations/observability baseline**
- What to build: structured logs, component health endpoints, and failure counters for daemon loops.
- Files: `tanishi/api/server.py`, `tanishi/proactive/run_proactive.py`, `tanishi/core/autonomy.py`
- Estimated hours: 12
- Dependencies: logging stack (local file is enough initially)
- UX impact: 7/10

### Tier 3 - Ship In 60 Days

1) **Unified runtime orchestrator**
- What to build: one service orchestration layer that starts/stops CLI/API/proactive/desktop modes with shared dependency checks.
- Files: `tanishi/cli.py`, `tanishi/api/server.py`, `tanishi/proactive/run_proactive.py`, new `tanishi/runtime/*`
- Estimated hours: 40
- Dependencies: process supervision approach
- UX impact: 8/10

2) **Cross-channel trust and policy enforcement**
- What to build: formal policy middleware so trust/privacy decisions are code-enforced before response generation.
- Files: `tanishi/memory/trust.py`, `tanishi/core/brain.py`, `tanishi/bridges/telegram_bot.py`, `tanishi/api/server.py`
- Estimated hours: 32
- Dependencies: policy spec agreement
- UX impact: 9/10

3) **Phone/WhatsApp/SMS interface**
- What to build: messaging bridge with auth, rate limits, command routing, and memory context.
- Files: new `tanishi/bridges/whatsapp.py`, `tanishi/bridges/sms.py`, `tanishi/api/server.py`, `tanishi/core/brain.py`
- Estimated hours: 28
- Dependencies: Twilio/WhatsApp API keys
- UX impact: 8/10

4) **Weekly self-improvement reports**
- What to build: periodic report generation from autoresearch metrics and delivery to dashboard/email/telegram.
- Files: `tanishi/autoresearch/*`, `tanishi/core/autonomy.py`, `tanishi/api/server.py`, `tanishi/dashboard/index.html`
- Estimated hours: 18
- Dependencies: scheduler already present
- UX impact: 7/10

### Tier 4 - Long-Term Vision (3-6 months)

1) **Offline-first assistant mode**
- What to build: full local mode with local LLM, local tools, local memory retrieval, and explicit offline capability matrix.
- Files: `tanishi/core/brain.py`, `tanishi/tools/*`, `tanishi/memory/*`, `tanishi/config/routing.py`
- Estimated hours: 80
- Dependencies: local model stack and hardware profile
- UX impact: 10/10

2) **Searchable life-recall layer (screen/audio timeline)**
- What to build: opt-in capture/index/query pipeline for screen snapshots + audio transcripts with privacy boundaries.
- Files: `tanishi/desktop/screen_watcher.py`, `tanishi/voice/listener.py`, new `tanishi/memory/recall_store.py`, dashboard/UI files
- Estimated hours: 100
- Dependencies: storage, indexing, privacy controls
- UX impact: 10/10

3) **Plugin ecosystem and extension SDK**
- What to build: stable plugin contract (manifest, auth, sandbox, versioning) and docs for community extensions.
- Files: `tanishi/tools/registry.py`, `tanishi/tools/mcp_client.py`, new `tanishi/plugins/*`, docs
- Estimated hours: 72
- Dependencies: extension governance model
- UX impact: 9/10

## Phase 5 - Top 10 Quick Wins (Under 30 Minutes)
1. Remove `cli.py~` from package to reduce confusion and accidental edits (`tanishi/cli.py~`).
2. Fix `tanishi/config/prompts.py` malformed top content to valid Python.
3. Wire `style` argument into `get_system_prompt` call or remove dead arg (`tanishi/core/brain.py`).
4. Add startup warning when no tools are registered (`tanishi/core/brain.py`).
5. Enforce non-empty selector/text for `click_element` schema (`tanishi/tools/browser_agent.py`).
6. Include optional args in tool schemas (`timezone`, email `folder`, list/search limits) (`tanishi/tools/system_tools.py`, `tanishi/tools/email_tools.py`, `tanishi/tools/filesystem.py`).
7. Add one-line health endpoint that reports tool count and core dependency readiness (`tanishi/api/server.py`).
8. Replace silent `except Exception: pass` in autonomy with logged warnings (`tanishi/core/autonomy.py`).
9. Add explicit benchmark warning when `expected_tool` is defined but not validated (`tanishi/autoresearch/benchmark.py`).
10. Add a `--check-deps` CLI command to validate optional feature dependencies before runtime (`tanishi/cli.py`).

---

This plan is paired with the complete tool-by-tool audit in `docs/TOOL_INVENTORY.md`.
