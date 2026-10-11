# 0025 · The sandbox for candidate code (SB1)

Date: 2026-10-11, revised 2026-10-12 (repair round after the red team, human decisions 1-8).
Decided by: the SB1 implementer within the card and the tester's assumptions A1-A8; §3 and §4 by the human.

1. **One entry point.** `tanishi.sandbox.Sandbox` (context manager). `run(argv, input=)` for a whole run,
   `popen(argv, cwd=, stdin=, stdout=, stderr=, pass_fds=)` + `kill(proc)` for callers with their own protocol (AR1,
   AR2's code harness). Code Tanishi wrote or changed is started through one of these, never with a bare `subprocess`.
2. **Default-deny, not a blocklist.** Readable: system dirs, this interpreter's prefixes, and `read_paths` the caller
   names; the folder holding a read path may be *listed* (names only, so a granted package can be imported), never
   read. Writable: scratch only. A `read_path` may not be `/`, the user's home or a folder holding it, or carry a
   refused name. Refused names, in any letter case and at any depth inside read paths: `vault`, `builder_vault`,
   `traces`, `.ssh`, `.env`, `.env.*`, `*.db`, `*.sqlite*` (macOS: profile regex; Linux: the tree is walked and
   carved; Docker: covered by tmpfs or /dev/null mounts). A process's own scratch may hold any name.
3. **Who wrote the code picks the backend (human decision 2026-10-12).** `trusted=False` = code a model wrote. It
   runs only where the user's other processes are out of sight: Docker, or Landlock + seccomp on Linux. **sandbox-exec
   does not hide them**: any process of the same user reads the argv and environment of every other one through
   `sysctl KERN_PROCARGS2`, and no profile rule gates it (red team break 2; `deny sysctl-read` stops Python itself).
   So on macOS a `trusted=False` sandbox uses Docker whatever `prefer_docker` says, and without Docker the
   constructor raises `tanishi.sandbox.SandboxUnavailable` (a `RuntimeError`; fail closed). Under sandbox-exec, **keys in the environment of the user's other
   processes are reachable**; files outside the grants are not. `trusted=True` (code the system itself wrote) may use
   sandbox-exec. Unset (`None`) keeps the old choice, because the SB1 exam constructs `Sandbox()` and
   `Sandbox(prefer_docker=False)` on macOS without Docker and expects a run (`open-problems/SB1-repair-conflicts.md`
   R1); every caller that runs candidate code passes `trusted=False` explicitly.
4. **Who passes what.**
   - AR2 `run_python_tests` (model-written code): `trusted=False`, no read path, no network, the harness copied into
     scratch; only JSON replies cross back and the verdict is made outside. No isolating backend → `SandboxUnavailable`, the
     verifier fails (never a score from an unconfined run). The harness says `{"ready": true}` first, so starting a
     container is not charged to the candidate's `timeout_s` (`CODE_STARTUP_S` = 60 s for that).
   - AR1's executor stage: `trusted=True`. It runs the caller's executor and the task's setup (system code), needs
     the host interpreter and import path, and Docker on macOS cannot provide either (the child replaces `sys.path`
     with the host's darwin paths). Read paths are `sys.path` minus the repo root and the cwd, from which only their
     packages (folders with `__init__.py`) are granted. Network only with the task tag `network`, memory 1024 MB,
     CPU/wall = startup + timeout + 5 s. An executor that runs a candidate's own code (a git ref) is not allowed through
     this path as is: open problem R2. The verifier stage stays unconfined, but no verifier executes candidate code
     outside a sandbox any more (run_python_tests is the one that did).
5. **Network.** `network=True` grants IP sockets only (macOS: `system-socket`, `network-outbound (remote ip)`,
   `network-bind (local ip)`, plus the system DNS resolver's socket and mach service). Never a unix socket by path, so
   docker.sock and ssh-agent stay out of reach. Linux: the seccomp filter refuses `socket(AF_UNIX)` always (Landlock
   before ABI 6 does not cover connect to a socket path); `socketpair()` stays allowed.
6. **The worktree is copied, never reset in place** (A6). The copy leaves out `.git`, `.env*`, `*.db`, `*.sqlite*`
   and the guarded names, in any letter case. `rollback()` kills, clears file flags (`chflags 0`) and folder
   permissions in scratch, wipes it (raising if anything is left) and re-copies; `close()` does the same and raises if
   scratch is still on disk.
7. **A run ends when its first process exits**, and everything it started dies then, setsid included: macOS asks the
   kernel (`sandbox_check` against two tag files only this sandbox's profile tells apart); Linux keeps the run under
   a child-subreaper launcher; Docker ends with its init. The child env carries no marker (AR1's minimal-env exam).
   Under Docker the environment is cut to the names the sandbox built (no image variables, no HOSTNAME), an alarm in
   the container ends it at the wall limit if the host side died, and one passed fd is carried on the container's
   stdout (`pass_fds` with stdout DEVNULL).
8. **Limits are mandatory.** `cpu_seconds`, `memory_mb`, `wall_seconds`, `max_processes` (default 64), `max_disk_mb`
   (default 128) must be finite and > 0. CPU is RLIMIT_CPU (soft = limit, hard = limit + 1, unraisable); memory is the
   summed RSS of the run, polled every 0.1 s. Processes: RLIMIT_NPROC = the user's count at start + `max_processes`
   (a fork past it fails with EAGAIN; per-user, so approximate), and the watchdog stops a run with more live members
   than `max_processes` (`limit="processes"`). Disk: RLIMIT_FSIZE = `max_disk_mb` per file (EFBIG), and the watchdog
   stops a run whose new bytes in scratch exceed `max_disk_mb` (`limit="disk"`, polled every 0.5 s). Docker: its own
   `--memory`, `--ulimit cpu/fsize`, `--pids-limit max_processes`, `/tmp` tmpfs sized `max_disk_mb`.
9. **One script for L1 (human decision 2026-10-12).** L1's Learning Compiler runs a generated skill test with
   ```python
   tanishi.sandbox.run_python(script: str, *, stdin: str | bytes | None = None, timeout_s: float) -> PythonResult
   ```
   `script` is Python source; it is written to a file in a fresh `Sandbox(trusted=False, network=False)` (never
   `trusted=True`, no read path) and run as `python -I script.py`, so its text is on no command line. `stdin` is its
   standard input (None = empty). It is killed after `timeout_s` seconds (wall and CPU; finite and > 0, container start
   included). `PythonResult` has `returncode`, `stdout`, `stderr` (text, each clipped to 64 KB, `PYTHON_OUTPUT_LIMIT`)
   and `timed_out`. On macOS without Docker it raises `SandboxUnavailable`; it never falls back to sandbox-exec.
