# AR3 · the candidate needs a sandbox (owner: SEC1, human decision 2026-10-10)

**Status 2026-10-11: RT-2 and RT-3 fixed by SB1** (AR1's executor stage runs in `tanishi.sandbox`, decision 0025;
xfail marks removed). The verifier/runner-patching point below is still open.

Decided in the AR3 repair round: not built in AR3. Both proofs stay `xfail(strict=True)` in
`tests/arena/test_redteam_ar3.py` until SEC1 lands; SEC1's fixer removes the marks.

| ID | Break | Proof |
|----|-------|-------|
| RT-2 (high) | The executor can write `brief.prompt` to any path the service user can write. | `test_executor_cannot_write_the_prompt_outside_its_home` |
| RT-3 (high) | The executor runs as the store's owner, so it can `listdir` and read every task file and the salt. | `test_executor_cannot_read_the_store` |

Same root: candidate code runs unsandboxed with the service's privileges. A second OS user for the *service* does not
help while the *executor* is that same user.

## What SEC1 needs to provide

- The executor stage of an AR1 attempt runs confined: writes only under its temp home, no read of the Frontier store,
  no network unless the task asks for it (`sandbox-exec` profile on macOS, `bwrap`/landlock on Linux), or as a third OS
  user with no access to the store or anything outside its home.
- The verifier must run in a process the executor's code never entered. Today the CLI's evaluating child imports the
  executor module (to pass it to `run()`), so a hostile module can patch the runner there and fake its own scores.
  A fix: the child passes the dotted name to the attempt process and never imports it itself.
- Then remove the two xfail marks; nothing else in the tests should change.

## Note

SEC1's card in `build/graph.yaml` (BA-01..BA-06) does not mention this yet. The graph is protected: the human has to add
it to SEC1's acceptance. AR4 (Sealed Vault) has the same need.
