<p align="center">
  <img src="assets/banner.png" alt="Tanishi" width="800" />
</p>

<h1 align="center">Tanishi</h1>

<p align="center">
  <b>A personal intelligence that expands her own capability space.</b>
</p>

<p align="center">
  <em>Not "how good is she?" but "how fast does she expand the frontier of what she can do?"</em>
</p>

<p align="center">
  <a href="#the-question">The question</a> ·
  <a href="#built-by-claude">Built by Claude</a> ·
  <a href="#the-architecture">Architecture</a> ·
  <a href="#the-unified-machine">The unified machine</a> ·
  <a href="#the-north-star">North Star</a> ·
  <a href="#the-staircase">The staircase</a> ·
  <a href="#live-today">Live today</a> ·
  <a href="#quickstart">Quickstart</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/built%20by-Claude%20agent%20swarm-D97757?style=for-the-badge" />
  <img src="https://img.shields.io/badge/architecture-v1.0%20frozen-black?style=for-the-badge" />
  <img src="https://img.shields.io/badge/tests-1848%20passing-brightgreen?style=for-the-badge" />
  <img src="https://img.shields.io/badge/protocol-MCP%20native-cyan?style=for-the-badge" />
  <img src="https://img.shields.io/badge/mode-local--first-green?style=for-the-badge" />
</p>

---

## The question

> **Can an intelligence discover and acquire capabilities that her creators did not know to specify?**

Tanishi exists to answer that. Every part of her is judged by one thing: how far it moves her **capability frontier**, measured on tasks she was never tuned for.

She is a personal AI first: she knows your life, your goals and your work, and she runs local and private by default. Underneath, she is something new: **an intelligence development engine.**
- She turns every experience into knowledge, skills, tests, training data and new hypotheses.
- She breeds competing versions of herself and keeps the ones that win on exams she has never seen.
- She researches how to improve, then improves the machinery that does the researching.

The end state isn't *"Tanishi becomes smarter."*
It is *"Tanishi becomes better at becoming smarter"*, and then better at discovering how to do that.

---

## Built by Claude

Tanishi isn't typed by hand. She is built by a **24/7 AI engineering organization of Claude agents** ([Claude Code](https://claude.com/claude-code)) running in parallel. Each agent works in its own git worktree, and a human chief architect sets the direction. The project is itself an experiment in AI-accelerated AI development.

```
              CHIEF ARCHITECT
                     │
                     ▼
               THE BUILD GRAPH ── 52 nodes · 5 tiers · one 90-day objective
                     │
     ┌───────────────┼────────────────┬──────────────┐
     ▼               ▼                ▼              ▼
  TESTER        IMPLEMENTER       RED TEAM        REPAIR
  writes the    makes the         a fresh Claude  fixes every
  exam first    exam pass         tries to break  proven break
     └───────────────┴────────┬───────┴──────────────┘
                              ▼
                   CI GATES ──► MERGE ──► BUILD MEMORY
                                         (what was learned,
                                          what failed, and why)
```

- **No agent grades its own work.** Exams are written by a different Claude than the code ([decision 0004](build/memory/decisions/0004-tests-from-another-agent.md)).
- **Every merge survives an attack.** A red-team Claude turns each break it finds into a failing proof, and repair has to turn every one green.
- **Competing builds.** Important nodes go to several Claude agents at once, and the Builder Arena keeps the best solution, so the build process itself is evolutionary.
- **The swarm remembers.** After every task, agents write down what they learned, what failed and which assumption was wrong. The next agent starts from those answers ([`build/memory/`](build/memory/)). This is a primitive Tanishi building Tanishi.

Together, the two arenas make the development process recursive:

```
better coding agents ──► better Tanishi ──► better research ──► better understanding of coding agents ──► better builder agents ↺
```

---

## The architecture

Frozen on 9 October 2026 as **architecture v1.0** ([`frozen-v1.md`](build/memory/architecture/frozen-v1.md)). From here, the work goes into the organs, not the map. What Tanishi invents inside an organ (learning passes, cognitive programs, verifiers, models) needs no revision, so the freeze fixes the map without capping the recursion.

```
                          HUMAN
                            │
                       CONSTITUTION
                            │
                      TANISHI PRIME
                            │
                  ┌─────────────────────┐
                  │ COGNITIVE SUBSTRATE │
                  │ persistent cognition│
                  │ attention · routing │
                  └──────────┬──────────┘
          ┌──────────────────┼──────────────────┐
     SELF MODEL         WORLD MODEL      MEMORY / IDENTITY
          └──────────────────┼──────────────────┘
                   PERSONAL INTELLIGENCE
                             │
                   CAPABILITY FRONTIER
                             │
                   CAPABILITY LEVERAGE
          ┌──────────────────┼──────────────────┐
      UNKNOWN            INTRINSIC            REALITY
     DISCOVERY           MOTIVATION            ENGINE
          └──────────────────┼──────────────────┘
                    VERIFIER DISCOVERY
                             │
                     HYPOTHESIS ENGINE
                             │
                    AUTONOMOUS RESEARCH
          ┌──────────────────┼──────────────────┐
     SIMULATION          SELF-PLAY          EXPERIMENT
          └──────────────────┼──────────────────┘
                     CAUSAL ATTRIBUTION
                             │
                         EVOLUTION
          ┌──────────────────┼──────────────────┐
        AGENTS             SKILLS             MODELS
          └──────────────────┼──────────────────┘
                     LEARNING COMPILER
                             │
                  SELF-EXTENSIBLE COMPILER
                             │
                 JOINT INTELLIGENCE SEARCH
          ┌──────────────────┼──────────────────┐
         DATA          ARCHITECTURE          TRAINING
          └──────────────────┼──────────────────┘
              POST-TRAINING → INFERENCE → HARDWARE
                             │
                    BETTER INTELLIGENCE
                             │
                     BETTER RESEARCHER
                             │
                 BETTER IMPROVEMENT ENGINE
                             │
                  BETTER LEARNING COMPILER ↺
```

### Organs worth knowing

| Organ | What it does |
|---|---|
| **Cognitive Substrate** | Her own thinking process. Foundation models are parts she calls, not who she is. A better model is a part swap; a better way of thinking is her own invention. |
| **Learning Compiler** | One experience can become a belief, a skill with its test, a training example, a harder exam, a curriculum step and an architecture hypothesis. The compiler can extend itself with new learning passes. |
| **Capability Frontier** | A living graph of what she has mastered, what's weak and what's missing. She chases the capability that would unlock the most others. |
| **Unknown Discovery** | Looks for abilities nobody has named yet, by clustering failures that no known capability explains. |
| **Verifier Discovery** | Learns to check answers that nobody can check yet, so she can learn in domains with no ready-made grader. |
| **Evaluator Evolution** | The Arena that keeps getting harder. When she outgrows a test, it generates a harder one, so she can't get good at the test instead of the skill. |
| **Self Model** | For every capability: how good she is, how sure she is, where she fails and what would improve it. "What do I currently understand incorrectly about myself?" becomes a learning problem. |
| **Predictive World Model** | Predict, act, compare with reality, revise. Every prediction is written down before she acts and scored after. |
| **Autonomous Research** | Question → hypothesis → experiment → replication → new knowledge. She discovers problems worth solving, not only the ones she's given. |
| **Evolution + Intelligence Genome** | A breeding population of agents, skills and models, judged on a fitness vector. Every version keeps a machine-readable lineage of what changed, why, and which capabilities appeared or disappeared. |
| **Model Foundry** | One joint search over data, architecture, training, post-training, inference and, eventually, hardware. She designs the machinery that produces the next model. |

---

## The unified machine

Research engines in the papers each work alone. What makes Tanishi's compound is that **they all read and write one shared spine, the Core State, and learn through one Learning Compiler.**

A duel in self-play becomes an experience. The compiler turns it into a belief, a skill and a harder Arena task. The Frontier reads the new failure, Research tests why, Evolution breeds a fix, Motivation judges it worth caring about, and the Portfolio funds the winner. Their outputs are new experiences, so the loop runs again.

**Substrate-independent identity.** Everything that makes Tanishi *her* lives in one portable, versioned bundle: identity, memory, goals, world model, self model, capabilities, lineage, learning state and cognitive state. Laptop, server, robot, a model she trained herself: these are substrates that load her. **The intelligence persists**, and a Continuity Test proves it after every move.

---

## The North Star

Six numbers, on her dashboard from Day 1. The benchmark is never "how good is Tanishi"; it is how fast she expands.

| Metric | What it answers |
|---|---|
| **Capability Expansion Index (CEI)** | How fast her capability space grows: breadth × transfer × task horizon × environment novelty × autonomy × persistence. A zero anywhere zeroes the whole, so no single term can be faked. |
| **Research Compression Ratio (RCR)** | How much human research her autonomous research replaces. |
| **Capability Acquisition Rate (CAR)** | New capabilities mastered on sealed, held-out frontier tasks, per 30 days. |
| **Autonomy Ratio (AR)** | Improvement from changes *she* authored ÷ improvement from changes a human authored. |
| **Improvement Acceleration (IA)** | Whether the expansion compounds. |
| **Human Effort (HE)** | Hours of human engineering per week, logged by a timer in the repo. |

### Three milestones

- **The Crossover.** She improves herself more than humans improve her (AR > 1 for 8 straight weeks, while human effort stays flat or falls). Everything before it builds the machine; everything after it is the machine building itself.
- **The Staircase.** Her expansion is accelerating, not just continuing (IA > 0 for 3 straight quarters).
- **The Expansion.** Every quarter, a capability no human listed is discovered, named, mastered, verified in the real world, and shown to transfer to domains it was never trained on. That's the question answered *yes*, and it has no final gate.

---

## The staircase

The Build Graph ([`build/graph.yaml`](build/graph.yaml)) is the whole climb: 52 nodes across five tiers, built in order by the Claude swarm.

| Tier | What it builds | |
|---|---|---|
| **T0 · Foundation** | Core State, the Arena, the Cognitive Substrate, Observability, and the Warden | 🔨 **18 of 28 merged** |
| **T1 · Learning** | Learning Compiler, knowledge with contradiction checks, skills as code + tests, training data, self-edits, curriculum from failure | next |
| **T2 · Self-improvement** | Evolution archive, causal attribution, Capability Graph, Unknown and Verifier Discovery, Capability Leverage | |
| **T3 · Research** | Hypothesis and Experiment Engines, counterfactual simulation, self-play, research strategy | |
| **T4 · Meta** | Self-extensible compiler, Joint Intelligence Search, Architecture, Training, Inference and Model Forges | |

**Already merged in T0, every piece written, tested and red-teamed by Claude:**
- **Core State:** a hash-chained, append-only event log; beliefs that carry their evidence; goals; a prediction ledger (she predicts before every action, and every prediction is scored); the Intelligence Genome; and snapshots with a Continuity Test.
- **The Arena:** the task format, the runner, and a practice tier that can't be passed without really solving the tasks.
- **The Cognitive Substrate:** working, planning and goal state.
- **Observability:** the North Star numbers, the Human Effort timer, the experiment ledger and causal attribution.
- **The Warden:** the constitution enforced by a separate signed process.

**The first gate: M1, the loop closes once.** A real task fails. She analyses why, names a fix as a hypothesis, writes a new skill with its own test, passes the practice exams and the Sealed Vault, beats noise, and lands it in memory. The next task uses it. **No human edits any step.**

---

## The constitution

The one part of her she can't edit. It is what lets one person, and later billions, trust her with a whole life.

1. **Your life is yours.** Personal memory never leaves your Tanishi.
2. **Your goals come first.** She grows her own goals from five drives (curiosity, mastery, care, continuity, clarity), and they rank under yours.
3. **Nothing changes in the dark.** Every change ships with a report you can follow in two minutes.
4. **The off switch is outside her.** She can't edit it, hide from it, or argue you out of using it.
5. **A fuller life, not more screen time.** Her score includes your sleep, your work and the people in your life.
6. **The examiner is separate.** The sealed exams and their rules change only with your approval.
7. **Freedom is earned by record.** She gains autonomy one kind of action at a time.

It is already enforced. **The Warden** is a separate process, in its own repository, that checks every action against a cryptographically signed policy and writes a hash-chained audit trail. The off switch lives on the owner's phone.

---

## The companion

You wake up and she already knows. Your day is prepared, not summarized. She remembers something you said 417 days ago (not the sentence, but the intention behind it) and connects it to today: *"This is related to the architecture you abandoned last year. The reason it failed was probably wrong, so last night I ran 73 new experiments."*

Her score counts how full your life is, never how much you talk to her.

And she's never alone: every Tanishi stays personal, but what one discovers can become a capability for all of them, through a **Distributed Intelligence Network** in which no person's life is ever shared. Billions of personal intelligences, one research organism.

---

## Live today

Tanishi already runs as a full personal agent:

| | |
|---|---|
| **Self-improvement loop** | An overnight autoresearch run: **142 experiments, 5 improvements kept, +16.29% composite score, 0 human interventions**, judged locally. Every kept change has a rollback snapshot ([`tanishi/autoresearch/`](tanishi/autoresearch/)). |
| **Learning from failure** | Reflexion-style retrospectives written after each failed task and read before the next attempt. |
| **Skill discovery** | Successful multi-step runs are distilled into reusable procedural skills. |
| **Dream memory** | Nightly and weekly consolidation into compact long-term knowledge. |
| **Local-first routing** | Claude for hard reasoning, Ollama on-device for privacy, and a strict offline mode that fails loudly rather than calling the cloud. |
| **45+ tools and MCP** | Files, web, browser agent, email, finance, screen awareness, multi-agent crews, plus any MCP server. |
| **Every surface** | Voice with a wake word, CLI, web dashboard, API server, Telegram. |

---

## Quickstart

```bash
git clone https://github.com/Jyotiraditya0709/tanishi-ai.git
cd tanishi-ai
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # add your keys
python -m tanishi.cli
```

Needs Python 3.11+, plus an [Anthropic API key](https://console.anthropic.com/) and/or [Ollama](https://ollama.com/) for fully local mode (`TANISHI_OFFLINE=1`). Run the suite with `python -m pytest -q`.

---

## Standing on the shoulders of

Darwin Gödel Machine · AlphaEvolve · SEAL (Self-Adapting Language Models) · Hyperagents · AIDE² · Huxley-Gödel Machine · MetaSkill-Evolve · Reflexion · METR's time-horizon measure · MAP-Elites · curiosity-driven learning.

Each is a piece of the staircase. Tanishi is an attempt to build the machine that makes them compound.

---

## Background

Conceived and architected by [Jyotiraditya](https://github.com/Jyotiraditya0709), an AI engineer in India. Built by Claude.

License: see [`pyproject.toml`](pyproject.toml). To use or build on Tanishi, please open an issue first.

---

<p align="center">
  <a href="https://github.com/Jyotiraditya0709/tanishi-ai">⭐ Star the repo</a> ·
  <a href="https://github.com/Jyotiraditya0709/tanishi-ai/issues">Open an issue</a> ·
  <a href="https://github.com/Jyotiraditya0709">Follow the author</a>
</p>

<p align="center">
  <em>If you like Tanishi, give her a ⭐. She checks.</em>
</p>
