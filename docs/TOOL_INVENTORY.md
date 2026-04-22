# Tanishi Tool Inventory Audit

## Table Of Contents
- [How Tools Are Registered](#how-tools-are-registered)
- [Complete Registered Tool List](#complete-registered-tool-list)
- [Dynamic MCP Tools](#dynamic-mcp-tools)
- [Benchmark Coverage](#benchmark-coverage)
- [Tool Quality Summary](#tool-quality-summary)
- [Top Tooling Gaps To Fix First](#top-tooling-gaps-to-fix-first)

## How Tools Are Registered

Primary registration paths:
- `tanishi/cli.py`: broadest registration surface; includes most optional tool packs plus MCP.
- `tanishi/api/server.py`: base packs + limited optional packs.
- `tanishi/bridges/telegram_bot.py`: base packs only.
- `tanishi/core/brain.py`: standalone fallback registration for realtime basics (`get_datetime`, `get_system_info`) when no external registry is supplied.

Implication: tool availability depends on entrypoint, so behavior can differ between CLI/API/Telegram unless registration is unified.

## Complete Registered Tool List

Grading scale:
- `A`: production-ready baseline
- `B`: works but needs polish/hardening
- `C`: half-built/brittle
- `D`: broken/stub

| Tool | What it does | Runtime requirements | Bench tested? | Grade |
|---|---|---|---|---|
| `web_search` | Internet search (DuckDuckGo) | network, `httpx` | No | B |
| `fetch_webpage` | Fetch and extract webpage content | network, `httpx` | No | B |
| `read_file` | Read local files | filesystem permissions | No | A |
| `write_file` | Write/append local files | filesystem permissions | No | B |
| `list_directory` | List directory contents with metadata | filesystem permissions | No | A |
| `search_files` | Recursive file pattern/content search | filesystem permissions | No | B |
| `run_command` | Execute shell command | OS shell, user permissions | No | B |
| `get_system_info` | Return OS/system/disk/runtime info | stdlib + local system access | Yes (`system_check`) | A |
| `get_datetime` | Return current date/time metadata | stdlib | Yes (`get_time`) | A |
| `scan_github_trending` | Search trending repos for ideas | network, GitHub API limits | No | B |
| `scan_for_improvements` | Aggregate improvement opportunities from GitHub scans | network, GitHub API limits | No | B |
| `take_screenshot` | Capture screen and optionally analyze via Claude vision | desktop display, `Pillow`, optional `ANTHROPIC_API_KEY` | No | B |
| `read_emails` | Read recent Gmail messages | `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` | No | B |
| `send_email` | Send Gmail messages | `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` | No | B |
| `search_emails` | Query Gmail messages | Gmail IMAP access; encoding/query brittleness | No | C |
| `open_app` | Launch local application | OS-specific app paths/commands | No | B |
| `open_url` | Open URL in default browser | browser + OS integration | No | A |
| `get_clipboard` | Read clipboard text | OS-specific clipboard backend | No | C |
| `set_clipboard` | Set clipboard text | OS-specific clipboard backend | No | C |
| `list_processes` | List running processes | OS process access | No | B |
| `kill_process` | Kill processes by name | OS process control permissions | No | B |
| `control_system` | Lock/sleep/mute/volume control | mostly Windows-specific hooks | No | B |
| `browse_url` | Open and inspect URL in Playwright session | `playwright` + browser install | No | B |
| `browser_search` | Perform web search via browser automation | `playwright`; selector/site fragility | No | C |
| `click_element` | Click a page element by selector/text | `playwright`; robust selector required | No | B |
| `fill_form` | Fill form fields in browser | `playwright` | No | B |
| `get_page_info` | Extract metadata/links/forms from page | `playwright` | No | B |
| `scroll_page` | Scroll browser page | `playwright` | No | A |
| `browser_back` | Navigate back in browser session | `playwright` | No | B |
| `close_browser` | Close browser session | `playwright` | No | A |
| `log_expense` | Record expense entry to local DB | local sqlite DB | No | A |
| `parse_transaction` | Parse bank/UPI text and log expense | parser patterns + sqlite DB | No | B |
| `spending_report` | Summarize spend over period/categories | sqlite DB | No | A |
| `set_budget` | Set category budget | sqlite DB | No | A |
| `spending_by_category` | Category-wise expense listing/summary | sqlite DB | No | A |
| `multi_agent_task` | Split complex task into specialist sub-agents | `ANTHROPIC_API_KEY` | No | B |
| `spawn_agent` | Spawn one specialist agent | `ANTHROPIC_API_KEY` | No | B |
| `run_learning_cycle` | Execute autonomous learn/analyze/propose cycle | Anthropic + filesystem + optional integrations | No | C |
| `show_improvements` | Show latest autonomous improvement proposals | local files | No | A |
| `show_latest_report` | Show latest autonomous learning report | local files | No | A |
| `mcp_connect` | Connect/configure MCP server | network/stdio, external server credentials | No | C |
| `mcp_list` | List connected/available MCP servers | MCP manager | No | A |
| `mcp_disconnect` | Disconnect server and unregister its tools | MCP manager | No | B |

## Dynamic MCP Tools

When MCP servers connect successfully, additional tools are registered dynamically as:
- `mcp_<server>_<tool>`

These tool grades are variable by server quality and schema consistency, but default to `C` until proven in integration tests.

Typical dependencies:
- GitHub MCP: `GITHUB_PERSONAL_ACCESS_TOKEN`
- Slack MCP: `SLACK_BOT_TOKEN`, `SLACK_TEAM_ID`
- Notion MCP: `NOTION_API_KEY`
- Brave search MCP: `BRAVE_API_KEY`

## Benchmark Coverage

Autoresearch benchmark (`tanishi/autoresearch/benchmark.py`) only targets:
- `get_datetime` in `get_time`
- `get_system_info` in `system_check`

Important caveat:
- Benchmark defines `expected_tool`, but current harness does not strictly assert that tool was actually called in every success case. It primarily scores output quality text.

## Tool Quality Summary

- **A-grade cluster**: local deterministic utilities (`get_datetime`, `get_system_info`, finance summaries, file reads/lists, browser session controls).
- **B-grade cluster**: solid functionality but needs hardening (web tools, command execution safety, screenshot path, email send/read, multi-agent orchestration).
- **C-grade cluster**: dependency-fragile or schema-brittle paths (email search, clipboard portability, browser search selectors, MCP dynamic tools, autonomous learning cycle).
- **No clear D-grade named tool** currently registered, but there are integration-level failures due to missing dependencies or inconsistent registration by interface.

## Top Tooling Gaps To Fix First

1. Unify registration parity across CLI/API/bridges so available tools are consistent.
2. Add tool health/self-test command that reports missing dependencies/API keys before first use.
3. Enforce benchmark-time validation that expected tools were called.
4. Tighten tool input schemas to match handler signatures.
5. Standardize structured error output for all tool handlers.
6. Add smoke tests for each tool pack under optional dependency guards.
