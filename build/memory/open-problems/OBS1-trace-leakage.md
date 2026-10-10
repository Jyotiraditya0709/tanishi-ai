# OBS1 · traces must not be readable by Arena candidates (for SEC1)

Raised: 2026-10-10, OBS1 repair round (human decision). Owner: SEC1. Not built in OBS1.

**Problem.** Traces go to `$TANISHI_HOME/traces/`. The Arena runs a candidate under a temporary `TANISHI_HOME`, and
graders and the harness run in the same process tree with the same home. Any span a grader or the harness opens
there (expected output in an attr, a task id that names the answer, a tool trace of the reference solution) sits in a
directory the candidate can read with an ordinary file tool. The files are 0600 and the dir 0700, but that protects
against other users, not against code running as the same user.

**What OBS1 already does.** Records carry ids, lengths and tool names only by rule (decision 0023 point 10), secrets
are redacted, strings clipped, records capped at 64 KB. None of that stops a candidate reading what a grader wrote.

**What SEC1 should decide.**
- Give the harness and graders a traces directory outside the candidate's `TANISHI_HOME` (a separate `TANISHI_HOME`
  for the grader side, or a `TANISHI_TRACES_DIR` override that the candidate's environment does not get).
- Or deny the candidate's tools read access to `$TANISHI_HOME/traces/` in the Warden (W1), the same way as the vault.
- Either way, add a test: a candidate tool call that reads `traces/*.jsonl` during an Arena run is refused or sees
  nothing from the grader.
- Until then, graders must not put task answers or expected outputs in span attrs.
