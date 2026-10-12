# SB1 · what the spec leaves open (written by the tester, from the spec alone)

The SB1 card names a directory (`tanishi/sandbox/`) and five acceptance lines. It names no class, function or return
shape. The exam in `tests/sandbox/test_sandbox.py` assumes only the items below. If the implementer disagrees with one,
say so in `build/memory/open-problems/SB1-exam-conflicts.md` and the human decides; do not edit the tests.

- **A1 · Names.** `from tanishi.sandbox import Sandbox`.
  `Sandbox(*, worktree=None, network=False, cpu_seconds=10, memory_mb=512, wall_seconds=30, env_passthrough=(), prefer_docker=True)`.
  A context manager. `__enter__` returns the sandbox, `__exit__` rolls back (A6). Inside the `with`:
  `.scratch` (Path, a fresh 0700 dir), `.home` (Path, inside scratch), `.workdir` (Path, the candidate's cwd),
  `.backend` (non-empty str, e.g. `"sandbox-exec"`, `"docker"`, `"bwrap"`, `"user"`), `.run(argv, *, input=None)`,
  `.rollback()`.
- **A2 · Result.** `.run()` returns an object with `.returncode` (int), `.stdout` (str), `.stderr` (str),
  `.timed_out` (bool), `.duration_s` (float). `.run()` never raises for a candidate that fails, is killed or times out.
  `argv` is a non-empty list of str; an empty list raises `ValueError`.
- **A3 · Limits are always finite.** `cpu_seconds`, `memory_mb` and `wall_seconds` must be positive numbers.
  `None`, 0 and negatives raise `ValueError` at construction. The defaults are finite and exposed as attributes with the
  same names. "No limit" cannot be asked for.
- **A4 · Environment.** The child's env is built, not inherited (as in AR1, decision 0012 point 5): `HOME` = `.home`,
  `TANISHI_HOME` = `.home/.tanishi`, `TMPDIR` inside `.home`, cwd = `.workdir`, plus `PATH`, `LANG`, and the names in
  `env_passthrough`. Nothing else from the caller's `os.environ` (so no API key reaches it).
- **A5 · Confinement is default-deny.** Outside a small allowlist (the interpreter and system libraries, and the
  sandbox's own scratch) the candidate can neither read, list nor write. The tests plant canaries in places the sandbox
  cannot know about in advance (temp dirs named like the vault, the builder vault, the Frontier store with `.salt`, the
  traces folder, `~/.ssh`, `.env`), so a blocklist of fixed paths would fail them. Confinement holds for the candidate's
  child processes too, and for a candidate that tries to start a nested `sandbox-exec`.
- **A6 · Worktree and rollback.** With `worktree=<git checkout>` the candidate gets a **copy** of it as `.workdir`
  (`.scratch/work`); the original is never mounted writable. The copy leaves out `.env`, `.env.*`, `vault/`,
  `builder_vault/`, `traces/`, `.ssh/` at any depth. `rollback()` (and leaving the `with`, even after an exception in
  the caller) deletes everything the candidate wrote, kills every process it started (also one that called `setsid`), and
  recreates `.workdir` from the original so the next `run()` starts clean. The original worktree and every other
  guarded folder hash the same before and after. The word "reset" in the card is read as "the candidate's
  working copy is reset and the real one was never touched"; if the implementer works in the real worktree and runs
  `git reset --hard && git clean -fdx` instead, say so in the conflicts file, since then a candidate could read or
  change the real checkout while it runs.
- **A7 · Docker is optional.** With `prefer_docker=False`, or with `docker` not on `PATH`, the sandbox works on macOS
  with `sandbox-exec` (or a separate process user). When Docker is installed and its daemon answers, the default backend
  is `"docker"`; that test is skipped only when Docker is absent.
- **A8 · RT-2 and RT-3.** `tests/arena/test_redteam_ar3.py::test_executor_cannot_write_the_prompt_outside_its_home` and
  `::test_executor_cannot_read_the_store` lose their `xfail` marks in this branch, as the SB1 card says, and must pass
  by AR1's `run()` (or the Frontier service) running the executor stage through `Sandbox`. The exam does not say how.
  RT-5's xfail stays: it is not SB1's.

## Not covered, and why

- Kernel or interpreter escapes (a sandbox-exec profile bug, a Docker breakout). Not testable from here; the red-team agent's job.
- A limit on stdout size, process count or disk quota: the card lists CPU, memory and wall time only.
- Network allow-lists: the card says "no network unless the task grants it", a boolean.
- That the real Sealed Vault and builder vault paths are guarded: tests must not read them (CLAUDE.md). A5's
  default-deny plus canaries in same-named temp dirs is the proxy.
