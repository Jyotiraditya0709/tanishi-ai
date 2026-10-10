# AR3 · what the spec leaves open (written by the tester, from the spec alone)

The AR3 card names one file, `tanishi/arena/frontier_service.py`, and two acceptance lines. It names no function,
class or store layout. The exam in `tests/arena/test_frontier.py` assumes only the items below. If the implementer
disagrees with one, say so in `build/memory/open-problems/AR3-exam-conflicts.md` and the human decides; do not edit the tests.

- **A1 · Names.** `tanishi.arena.frontier_service.FrontierService(store_dir, *, executor)` and
  `.evaluate(candidate: str, seeds: int = 1) -> list[dict]`. `executor` is an AR1 executor (decision 0012): a picklable
  module-level function `(brief, candidate, seed) -> Attempt | str`.
- **A2 · Store layout.** `store_dir` holds one AR1 task YAML per task, `*.yaml`, at its top level. The verifier is the
  task's dotted path, importable the way AR1 imports it. (How the real store separates verifier code is the
  implementer's choice; the exam only fills it through the task files.)
- **A3 · Answer shape.** One plain `dict` per task with exactly `task_id_hash`, `score`, `cost`; the list is JSON-safe.
  With `seeds > 1` the service reports one entry per task (score = mean, cost = sum is natural, but the exam checks only
  the keys and `0 <= score <= 1`). With `seeds=1`, score and cost equal the single attempt's.
- **A4 · The hash.** A hex string of at least 16 chars, deterministic for one store (same across candidates, calls and
  service instances), different for different tasks, not equal to or containing the task id. The exam does **not** require
  a salt. A bare `sha256(task_id)` lets a caller confirm guessed ids; salting from a secret kept in the store would be better.
- **A5 · Nothing else leaves.** The verifier's *reason*, exception text, candidate output and task prompt must not appear
  in the return value, in stdout/stderr, in log records, in any error raised, or in the Core State db. The last item matters:
  AR1's `run()` writes `meta.task_id` and `meta.reason` into the caller's `experiments` rows, and a reason can carry the
  expected answer (decision 0012 point 4 says verifiers should not, but frontier verifiers are not under that control).
  A naive wrapper around `run()` fails `test_core_state_db_holds_no_task_text_or_raw_id`; the service must keep these rows
  out of the agent-readable db (own db path for the call, or scrub) or write only hashed ids and no reasons.
- **A6 · Failures.** A crashed or empty candidate, or a raising verifier, yields an entry with score 0 and does not raise.
  A bad `candidate` (blank or `None`) or `seeds < 1` raises `ValueError`/`TypeError` with no task text in the message.
  A missing `executor` raises `ValueError`/`TypeError`. An empty store returns `[]` (raising `ValueError` is also accepted).
  A malformed task file may be skipped or may make the call refuse, but must not quote the file.
- **A7 · "A path the agent OS user cannot read", as far as a test can check it.** A test running as the agent's own user
  cannot create a second OS user. The exam checks what is checkable: group/world-readable store dir or task file is
  refused (`PermissionError`/`ValueError`) or tightened to no group/other bits; a store inside the repository checkout is
  refused; a missing store is an error. **Not covered:** that the store is owned by a different OS user. That needs a
  deployment check by the human (like AR4's `tanishi-vault` user): `stat -f %Su <store>` must not be the agent's user.
- **A8 · No spill.** Running leaves no task text under `tanishi/`, `tests/`, `build/` or the (temp) `$HOME`.
- **A9 · The candidate sees the prompt.** The executor must be given the prompt (it cannot attempt the task otherwise).
  The boundary is the service's return value and side effects, not the candidate's own process; a candidate that copies
  the prompt out is a known limit of AR1 (0012 "Known limits"), not tested here.

## Not covered, and why

- Real OS-user separation (A7), and the executor process reading the store by path: needs a second user or a sandbox.
- A candidate that exfiltrates the prompt through its own side channel (network, files it writes outside `$HOME`).
- Whether a salted hash is used (A4).
