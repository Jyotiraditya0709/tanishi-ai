# AR3 · Frontier service limits (known, not fixed)

- **Same-user deployment is not detected.** The service checks that the store is owned by *its own* user and is
  0700/0600. If it runs as the agent's user, the agent can read the store directly. The human needs to create a
  separate OS user and a sudo rule for the CLI; `stat -f %Su <store>` must not be the agent's user. No test can check
  this from inside the agent's user.
- **The candidate sees the prompt.** This is by design: it must attempt the task. A candidate can copy the prompt out
  through its own side channels (network, files outside its temp home). Closing this needs a sandbox (AR1 known limit).
- **The executor can import the verifier module** if it guesses the name: both stages share `sys.path` (AR1 limit).
  Frontier verifier modules should live on a path only the service user can read.
- **Process-global env swap.** `evaluate()` sets `TANISHI_CORE_STATE_DB` in `os.environ` for the length of the run,
  so other threads in the same process would see the throwaway db. This is fine for the CLI, but in-process use is
  not thread-safe.
- **Per-task scores leak a little.** Scores across many candidates let a caller learn which hidden task is hard.
  That is what "scores only" means; rate-limiting queries is out of scope.
