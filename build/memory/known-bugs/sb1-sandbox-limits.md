# SB1 · sandbox limits (known, not fixed)

Decision: `decisions/0025-sandbox.md`.

- **sandbox-exec does not hide other processes.** Under it (unset `trusted` or `trusted=True`) a process reads the
  argv and environment of every process of the same user (`sysctl KERN_PROCARGS2`): exported keys included. Only
  `trusted=False` code is kept off it. AR1's executor stage runs there (conflicts R2).
- **Metadata is open.** `stat`/`exists` work on any path (macOS profile allows `file-read-metadata`; Landlock does
  not cover it). A candidate can probe whether a file exists and its size, not read it. The folder holding a read path
  can be listed (names only).
- **Linux was not run in the repair round.** Refused names are carved at any depth by walking each read path when a
  process starts (files added later inside a wholly granted folder are not re-checked); refused names can still be
  *listed*. Seccomp refuses chmod/chown by path everywhere (also in scratch, so `shutil.copy`'s `copymode` fails) and
  `socket(AF_UNIX)`. UDP and abstract unix sockets are not covered by Landlock's network rules (TCP connect/bind are).
- **`/etc`, `/usr` and `/System` are readable** (macOS: `/usr/local` is not). Root-only files stay root-only.
- **The import path is readable by AR1 executors,** minus the repo root and cwd (only their packages): `tests/`
  folders on `sys.path` are still readable, so an executor can import a verifier module whose name it guesses.
- **The verifier stage is not confined.** It runs in the home the executor wrote to, after every executor process is
  dead. A verifier that opens files in its cwd reads candidate-made files. Verifiers that *execute* candidate output
  must sandbox it (`run_python_tests` does).
- **Docker** maps the host interpreter to the image's `python3` (default `python:3.12-slim`) and needs that image
  (pulled on first use). Host paths are mounted at the same place, so host darwin packages do not import. A
  host-loopback network grant is not testable. On Docker Desktop for Mac a read path is a case-insensitive mount, so
  `VAULT/` may reach a `vault/` that the tmpfs covers only by its exact name (not tested; no read path is given to
  Docker by any caller today). `pass_fds`: at most one, carried on the container's stdout.
- **Escaping the kill on Linux:** a process that outlives a SIGKILLed launcher (killed by someone else first) is
  reparented away from the subreaper. Still confined by Landlock and RLIMIT_CPU; scratch is deleted under it.
- **Limits are polled or per user.** Memory (RSS of the tree) and processes/disk past their caps are checked every
  0.1 s / 0.5 s, so a burst can overshoot briefly. RLIMIT_NPROC is per user: the cap is the user's count at start +
  `max_processes`, so it drifts when the user's other processes come and go (root is not limited by it). RLIMIT_CPU is
  per process: a tree of N processes burns N times the budget until the wall limit. Output kept: 8 MB per stream.
- **`run_python`'s `timeout_s` includes starting the sandbox** (a container on macOS: about 1-3 s on Docker Desktop,
  more when the image is pulled the first time). A very small `timeout_s` can kill a script before it prints anything.
  Each call starts a fresh container, so L1 should batch a skill's checks into one script, not call it per assert.
- **Kernel/profile escapes** (a sandbox-exec or Landlock bug) are out of scope; `sandbox-exec` is deprecated by
  Apple but still shipped and used by Apple's own tools.
