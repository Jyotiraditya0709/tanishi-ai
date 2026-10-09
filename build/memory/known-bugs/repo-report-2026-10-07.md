# Known bugs from the repo report (2026-10-07, HEAD 3d0a9b3)

Fix the security ones before anything runs unattended (node SEC1, Builder Arena BA-01 to BA-06).

## Security

- `tanishi/api/server.py` binds `0.0.0.0` with CORS `*` and no auth. Anyone on the LAN can chat with ungated tools, read `/memory`, trigger `/screenshot`. (BA-04)
- `tanishi/bridges/telegram_bot.py`: with `TELEGRAM_ALLOWED_IDS` unset, anyone can use the bot; a stranger can approve their own `run_command`, `write_file`, `send_email`; `/status` and `/memory` skip the allowlist. (BA-03)
- `tanishi/cli.py` `_approve_tool`: an empty Enter counts as approve. (BA-01)
- Only 5 tools need approval. `read_file` can read `~/.ssh` and `.env`. (BA-05)
- `browse_url` accepts `file://`; `fetch_webpage` allows SSRF including localhost. (BA-06)
- `tanishi/tools/windows_auto.py`: shell injection in `set_clipboard`, `open_app`, the `list_processes` filter; `kill_process` uses `pkill -f` and can kill Tanishi.
- `tanishi/memory/trust.py`: `secrets` stored in plaintext; passwords are unsalted SHA-256. (BA-09)
- `~/.tanishi/mcp_servers.json` stores MCP tokens in plaintext.
- Offline mode still calls Claude (AutoMemory, screenshot, summarize_clipboard, autoresearch judge); the screen watcher sends a screenshot every 10 s or less when the screen changes.
- The wake-word daemon accepts voice commands from anyone in earshot, with ungated tools.

## Correctness

- `tanishi/core/brain.py` `_select_model`: with `LOCAL_FIRST=True` and Ollama up, short prompts go to Ollama with no tools, so tool use silently drops. (BA-07, SUB3)
- One global `TanishiBrain` is shared across sessions; concurrent sessions interleave and leak context.
- `ToolRegistry.execute()` retries side-effecting tools up to 3 times (finance writes, GitHub scans, email). (BA-02)
- Sync `anthropic`, `imaplib`, `smtplib`, `sqlite3` calls block the event loop; the 10 s tool timeouts cannot interrupt them.
- CLI `/crew`, `/crews`, `/agents` import the non-existent `tanishi.core.multi_agent`.
- `/ws` streaming is fake.
- Finance: same-second expense IDs overwrite each other (BA-08); the SMS parser can read account digits as the amount.
- `web_search`: a cache write failure turns a successful search into "Search failed". (BA-10)
- Email: reading marks mail as read; quotes in the query break IMAP search.
- `requests` is used but not declared, so the proactive daemon and the dream cycle silently do nothing.
- `tanishi.cli` does not import in the current venv (pyexpat/libexpat); recreate the venv.

## Hygiene

- `data/db.sqlite` (real conversations, memories, a password hash) is committed. (B0)
- `tanishi/config/prompts.py` is clobbered to a one-line stub. (B0)
- Runtime files are tracked: `tanishi/tools/search_cache.json`, `proactive_state.json`, parts of `autoresearch_results/snapshots`.
- `.env.example` lists names nothing reads and misses many the code does read.
