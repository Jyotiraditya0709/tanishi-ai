# SB1 · sandbox limits (known, not fixed)

Decision: `decisions/0024-sandbox.md`.

- **Metadata is open.** `stat`/`exists` work on any path (macOS profile allows `file-read-metadata`; Landlock does
  not cover it). A candidate can probe whether a file exists and its size, not read it.
- **Linux guards names one level deep.** Landlock has no deny rule, so inside a read path only top-level `vault/`,
  `.env`, etc. are left out; a nested `pkg/.env` under a read path is readable on Linux (macOS denies at any depth).
  Guarded top-level entries can still be *listed* (names, not contents) on Linux.
- **Linux refuses chmod/chown by path everywhere,** also inside scratch (seccomp; Landlock cannot scope them), so
  `shutil.copy`'s `copymode` fails in a candidate on Linux. `fchmod` on an open fd still works. UDP and abstract
  unix sockets are not covered by Landlock's network rules (TCP connect/bind are).
- **`/etc`, `/usr` and `/System` are readable** (macOS: `/usr/local` is not). Root-only files stay root-only.
- **The import path is readable by AR1 executors,** including the repo checkout and `tests/`: an executor can still
  import a verifier module whose name it guesses (AR1 limit), and on Linux list names in the repo root.
- **The verifier stage is not confined.** It runs in the home the executor wrote to, after every executor process is
  dead. It only reads the output string; a verifier that opens files in its cwd reads candidate-made files.
- **Docker** maps the host interpreter to the image's `python3` (default `python:3.12-slim`) and needs that image
  (pulled on first use). A host-loopback network grant is not testable; process kill relies on `--init` + `docker kill`.
- **Escaping the kill on Linux:** a process that outlives a SIGKILLed launcher (killed by someone else first) is
  reparented away from the subreaper. Still confined by Landlock and RLIMIT_CPU; scratch is deleted under it.
- **Memory is polled every 0.1 s** (RSS of the tree), so a burst can overshoot the limit briefly before the kill.
  No limit on process count (except Docker's 256), disk use in scratch, or output size beyond 8 MB kept per stream.
- **Kernel/profile escapes** (a sandbox-exec or Landlock bug) are out of scope; `sandbox-exec` is deprecated by
  Apple but still shipped and used by Apple's own tools.
