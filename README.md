<p align="center">
  <img src="https://raw.githubusercontent.com/Jyotiraditya0709/tanishi-ai/main/assets/banner.png" alt="Tanishi Banner" width="800" />
</p>

<h1 align="center">Tanishi</h1>
<p align="center"><b>Your Personal JARVIS. Built from Scratch. Open Source.</b></p>

<p align="center">
  <em>83 tools · Voice conversations · Screen awareness · Self-improving · MCP integration</em>
</p>

<p align="center">
  <a href="#features">Features</a> ·
  <a href="#demo">Demo</a> ·
  <a href="#quickstart">Quickstart</a> ·
  <a href="#tools">Tools</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#roadmap">Roadmap</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/tools-83+-cyan?style=for-the-badge" />
  <img src="https://img.shields.io/badge/voice-OpenAI%20Whisper%20%2B%20TTS-green?style=for-the-badge" />
  <img src="https://img.shields.io/badge/MCP-2300%2B%20integrations-blue?style=for-the-badge" />
  <img src="https://img.shields.io/badge/self--improving-Karpathy%20Loop-orange?style=for-the-badge" />
  <img src="https://img.shields.io/badge/license-MIT-yellow?style=for-the-badge" />
</p>

---

## What is Tanishi?

Tanishi is a **self-improving AI assistant** that lives on your computer and actually *does things* — not just chat. She speaks with a human voice, watches your screen for errors, shops on Amazon, manages your finances, creates GitHub repos, spawns teams of specialist AI agents, and improves herself overnight.

Built by a 21-year-old CS student from India. From zero to 83 tools. Entirely open source.

```
[J] › Hey Tanishi, search Amazon for wireless earbuds under ₹2000
🔊 "On it, boss. Let me pull up some options..."
🌐 Browser opens → searches Amazon → reads prices
💰 "Found 5 options. Want me to log this as shopping research?"
```

**This is not a chatbot. This is JARVIS.**

---

## Demo

> 🎬 **Video demo coming soon** — Voice conversation → Browser automation → Screen analysis → Multi-agent research → Self-improvement, all in one take.

<details>
<summary><b>📸 Screenshots</b> (click to expand)</summary>

| Feature | Screenshot |
|---------|-----------|
| CLI Interface | *coming soon* |
| Voice Mode | *coming soon* |
| Screen Watcher | *coming soon* |
| 3D Avatar | *coming soon* |
| MCP Integration | *coming soon* |

</details>

---

## Features

### 🗣️ Voice — Speaks Like a Human
Real OpenAI TTS voices, not robotic. Wake word detection ("Hey Tanishi"), sentence-by-sentence speech with interrupt support, 8 voice presets from sarcastic to warm.

### 👁️ Screen Awareness — Sees What You See  
Continuous screen monitoring via Claude Vision. Detects errors in your terminal, stuck states, app switches. Proactively offers help when she spots problems.

### 🌐 Browser Agent — Shops, Searches, Browses  
Playwright-powered visible browser automation. Watches Amazon, searches Google, fills forms. You see everything she does — no black box.

### 🧠 Self-Improving — The Karpathy Loop
Autonomous learning cycle: analyzes her own failures → scans GitHub for upgrades → proposes improvements → tests them → applies safe ones automatically. She literally gets smarter while you sleep.

### 🔌 MCP Integration — 2300+ Services
Model Context Protocol support. Connect to GitHub, Slack, Notion, Google Drive, databases, and thousands more. She created her own GitHub repo. Think about that.

### 👥 Multi-Agent Crews — Spawns Specialist Teams  
Complex tasks? She decomposes them and spawns teams: Researcher, Coder, Writer, Analyst, Planner, Critic. Parallel execution with dependency management.

### 💰 Finance Tracking — UPI/INR Native
Log expenses, parse bank SMS (SBI, HDFC, ICICI), set budgets, track spending by category. Built for India.

### 🎭 3D Avatar — Animated Face with Moods
7 mood states (idle, thinking, sarcastic, happy, error). Eyes follow your cursor. She has a face.

### 💬 Multi-Platform
CLI + Web Dashboard + Telegram Bot + System Tray. Coming: WhatsApp, Mobile App.

---

## Quickstart

### Prerequisites
- Python 3.11+ 
- [Anthropic API key](https://console.anthropic.com/) (for Claude)
- Optional: [OpenAI API key](https://platform.openai.com/) (for voice)
- Optional: [Node.js](https://nodejs.org/) (for MCP servers)

### Install

```bash
# Clone the repo
git clone https://github.com/Jyotiraditya0709/tanishi-ai.git
cd tanishi-ai

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# or: .venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Set up your API keys
cp .env.example .env
# Edit .env with your keys

# Launch
python -m tanishi.cli
```

### Voice Setup (optional)
```bash
pip install SpeechRecognition edge-tts openai sounddevice pygame-ce
```

### Browser Agent Setup (optional)
```bash
pip install playwright
playwright install chromium
```

### MCP Setup (optional)
```bash
# Install Node.js from https://nodejs.org/
# Then in Tanishi:
/mcp connect filesystem
/mcp connect github  # needs GITHUB_PERSONAL_ACCESS_TOKEN in .env
```

---

## Tools

Tanishi ships with **43 built-in tools** and gains more through MCP:

| Category | Tools | Examples |
|----------|-------|---------|
| **Core** | 8 | Chat, memory, remember, recall, search memories |
| **Windows** | 6 | Open apps, clipboard, volume, lock screen, processes |
| **Browser** | 8 | Navigate, search (Google/Amazon/YouTube), click, fill forms |
| **Email** | 4 | Read, send, search, draft (Gmail/IMAP) |
| **Finance** | 5 | Log expense, parse bank SMS, budgets, spending reports |
| **Vision** | 3 | Screenshot, screen watch, screen describe |
| **Multi-Agent** | 2 | Spawn crews, delegate to specialists |
| **Learning** | 3 | Karpathy Loop, show improvements, generate reports |
| **MCP** | 3+40 | Connect servers, list, disconnect + filesystem (14) + GitHub (26) |
| **System** | 4 | Telegram, dashboard, avatar, tray |

**Total: 83+ tools** (grows every time you connect an MCP server)

---

## Architecture

```
┌─────────────────────────────────────────────┐
│                  TANISHI                      │
├──────────┬──────────┬───────────┬────────────┤
│  Voice   │  Vision  │  Browser  │  Finance   │
│ Whisper  │  Claude  │Playwright │  UPI/INR   │
│  + TTS   │  Vision  │  Agent    │  Tracker   │
├──────────┴──────────┴───────────┴────────────┤
│              Brain (Claude API)               │
│         + Ollama (local/private)              │
├──────────────────────────────────────────────┤
│           Tool Registry (83 tools)            │
├──────────┬──────────┬───────────┬────────────┤
│  Memory  │   MCP    │  Multi-   │  Learning  │
│  SQLite  │  Client  │  Agent    │  Karpathy  │
│  + Trust │  2300+   │  Crews    │   Loop     │
├──────────┴──────────┴───────────┴────────────┤
│  CLI  │  Dashboard  │  Telegram  │  Tray     │
└──────┴─────────────┴───────────┴────────────┘
```

---

## Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/voice` | Start voice mode (wake word: "Hey Tanishi") |
| `/screenshot` | Capture & analyze your screen |
| `/watch` / `/unwatch` | Toggle continuous screen monitoring |
| `/mcp connect <name>` | Connect to an MCP server |
| `/learn` | Run the Karpathy Loop (self-improvement) |
| `/crew <task>` | Spawn a multi-agent team |
| `/voices` | List available voice presets |
| `/memory` | Show what Tanishi remembers about you |
| `/dashboard` | Open web dashboard |
| `/avatar` | Launch 3D animated face |

---

## How It's Different

| | ChatGPT | OpenClaw | Tanishi |
|---|---|---|---|
| Voice | Text only | No | **Natural speech + wake word** |
| Screen awareness | No | No | **Watches for errors** |
| Self-improving | No | No | **Karpathy Loop** |
| Browser automation | No | Via skills | **Visible Playwright** |
| Finance tracking | No | No | **UPI/INR native** |
| Avatar | No | No | **3D animated face** |
| MCP support | N/A | Yes | **Yes** |
| Multi-agent | No | No | **Specialist crews** |
| Open source | No | Yes | **Yes** |
| Personality | Generic | None | **Sarcastic JARVIS** |

---

## Roadmap

- [x] Voice conversations (OpenAI Whisper + TTS)
- [x] Screen awareness (Claude Vision)
- [x] Browser agent (Playwright)
- [x] 3D Avatar with moods
- [x] Finance tracking (UPI/INR)
- [x] Multi-agent crews
- [x] Autonomous learning (Karpathy Loop)
- [x] MCP integration (2300+ services)
- [ ] WhatsApp bridge
- [ ] Always-on daemon mode
- [ ] Mobile app (React Native)
- [ ] `pip install tanishi` one-command install
- [ ] Tanishi Cloud (hosted version)

---

## Story

I'm a 21-year-old CS student from Jalandhar, India. I built Tanishi because I wanted JARVIS — not a chatbot that just responds, but an AI that *sees*, *speaks*, *acts*, and *learns*. 

Every feature was built from scratch. No templates, no starter kits. Just Claude, Python, and 48 hours of not sleeping.

Tanishi is Hindi for "a girl who is beautiful." She's also sarcastic, opinionated, and will roast you if you procrastinate.

---

## Contributing

Pull requests welcome. If you want to add a feature, open an issue first.

```bash
# Fork the repo
# Create a branch
git checkout -b feature/amazing-thing

# Make your changes
# Test them

# Push and open a PR
git push origin feature/amazing-thing
```

---

## License

MIT — do whatever you want. Just don't blame Tanishi when she roasts your code.

---

<p align="center">
  <b>Built with obsession by <a href="https://github.com/Jyotiraditya0709">J</a></b>
  <br>
  <em>If you like Tanishi, give her a ⭐ — she checks.</em>
</p>
