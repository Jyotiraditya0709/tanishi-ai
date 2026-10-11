# 0024 · The sandbox for candidate code (SB1)

Date: 2026-10-11. Decided by: the SB1 implementer, within the card and the tester's assumptions A1-A8.

1. **One entry point.** `tanishi.sandbox.Sandbox` (context manager). `run(argv, input=)` for a whole run,
   `popen(argv, cwd=, stdin=, stdout=, stderr=, pass_fds=)` + `kill(proc)` for callers with their own protocol (AR1).
   Code Tanishi wrote or changed is started through one of these, never with a bare `subprocess`.
2. **Default-deny, not a blocklist.** Readable: system dirs, this interpreter's prefixes, and `read_paths` the caller
   names. Writable: scratch only. A `read_path` may not be `/`, the user's home or a folder holding it, or a guarded
   name. Inside read paths, `vault`, `builder_vault`, `traces`, `.ssh`, `.env`, `.env.*` are refused (macOS: at any
   depth; Linux: at the top level of each read path, see known-bugs).
3. **Backends.** `prefer_docker=True` (the default) uses Docker when its daemon answers; else `sandbox-exec` on macOS,
   Landlock (ABI 4+, or 2+ when network is granted) + a seccomp filter on Linux; else `RuntimeError`. Nothing runs
   unconfined. AR1 passes `prefer_docker=False`: its executor needs the host interpreter and checkout.
4. **AR1's executor stage is sandboxed, its verifier stage is not.** `run()` confines setup + executor with
   `read_paths = sys.path` (minus refused entries), network only if the task has the tag `network`
   (`runner.NETWORK_TAG`), memory 1024 MB (`EXECUTOR_MEMORY_MB`), CPU/wall = startup + timeout + 5 s as a backstop.
   No sandbox available → the attempt scores 0 as `infra`. The verifier runs afterwards, unconfined, in the same home.
5. **The worktree is copied, never reset in place** (A6). `rollback()` kills, wipes scratch and re-copies.
6. **A run ends when its first process exits**, and everything it started dies then, setsid included: macOS asks the
   kernel (`sandbox_check` against two tag files only this sandbox's profile tells apart); Linux keeps the run under
   a child-subreaper launcher. The child env carries no marker (AR1's minimal-env exam).
7. **Limits are mandatory.** `cpu_seconds`, `memory_mb`, `wall_seconds` must be finite and > 0. CPU is RLIMIT_CPU
   (soft = limit, hard = limit + 1, unraisable); memory is the summed RSS of the run, polled every 0.1 s; Docker uses
   its own `--memory`/`--ulimit`.
