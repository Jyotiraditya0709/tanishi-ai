# AR3 · Frontier service limits (known, not fixed)

Updated in the repair round (2026-10-10). Decision: `decisions/0022-frontier-tier.md`.

- **Same-user deployment is not detected.** The service checks that the store is owned by *its own* user and is
  0700/0600. If it runs as the agent's user, the agent can read the store directly. The human needs to create a
  separate OS user and a sudo rule for the CLI; `stat -f %Su <store>` must not be the agent's user. No test can check
  this from inside the agent's user. Run the CLI with `python -P` so the cwd cannot shadow a stdlib module.
- **The candidate is not sandboxed (RT-2, RT-3, owner SEC1).** It sees the prompt (by design) and runs as the store's
  owner, so it can copy the prompt out through files or the network and read the store. The executor can also import
  the verifier module if it guesses the name (AR1 limit), and in the CLI's evaluating child its module can patch the
  runner and fake its own scores. The parent process that prints is safe from it; the scores are not.
- **Env swap is locked, not gone.** `evaluate()` still sets `TANISHI_CORE_STATE_DB` for the length of the run (AR1
  `run()` has no db argument). Overlapping calls are serialised and restore correctly, but other threads in the same
  process see the throwaway db while a call runs.
- **Scores are an oracle.** The daily budget (20 per candidate per store) does not stop a few-call bisection, and a
  caller can mint new candidate strings. Frontier tasks must not have short enumerable answers (RT-5).
- **Cost is still the candidate's word.** Bounded (cents, cap 20.0) but under-reportable, and lost on a failed attempt
  (AR1). A cost-aware gate should use metered spend, not this field.
- **Temp leftovers.** A `SIGKILL` mid-run leaves a `tanishi-frontier-*` dir (db with raw task ids and reasons) in
  `$TMPDIR`, 0700. A `.salt-*.tmp` left in the store holds only random bytes and is ignored.
- **The `.queries` ledger grows forever** (one JSON line per call) and is read in full on each call.
