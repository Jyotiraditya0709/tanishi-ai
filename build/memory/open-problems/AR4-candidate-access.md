# AR4 · How a real candidate runs inside the Sealed Vault (needs the human)

Status: open. Blocks the first real sealed read of a model-written candidate on the Mac (gate G1 onwards), not G0.

## The problem

The vault copies a candidate's `tanishi/` package into `~/staging/<id>/` and passes that path to the executor as the
`candidate` string. Two things stop a real candidate from being scored there today:

1. **The executor stage cannot read the copy.** AR1's runner grants the executor's sandbox read access only to
   `sys.path` entries. The staging folder is not one, and must not become one: anything on `sys.path` is also imported
   by the verifier stage, which runs unconfined, so a candidate module could shadow a missing import there.
2. **Model-written code must run on an isolating backend (decision 0025 §3).** On macOS that is Docker, and the vault
   user cannot use the agent user's Docker Desktop (its socket and its file sharing belong to that user).

## Options

A. **Add `executor_read_paths` to AR1 `run()`**: extra folders granted to the executor stage's sandbox only, each
   checked by `checked_read_path`, never added to `sys.path`. A ready patch exists (about 30 lines). With it, a
   `checkout_executor` runs the candidate's `tanishi.arena.answer` in a child of the executor stage.
B. **Run candidates as the vault user under sandbox-exec.** 0025 forbids sandbox-exec for model-written code because
   any same-user process can read every other same-user process's argv and environment (KERN_PROCARGS2). Inside the
   vault the only same-user processes are the vault's own, which hold no secret beyond the model key the candidate is
   given anyway; the agent user's processes are a different uid and out of reach. This would be a narrow, written
   exception to 0025 for the vault user only (for example a root-owned list of users allowed it).
C. **Give the vault user its own isolating backend**: a Linux VM for that user (Colima, Lima, or Apple's `container`),
   started only for a read. About 1.5 GB of RAM while it runs. Keeps 0025 as it is.

Option A is needed under every choice. Then either B (fast, an exception) or C (slower to set up, no exception).

## Also needed, whatever is chosen

- A headless entry point in every candidate: `tanishi/arena/answer.py` with `answer(request: dict) -> str`, where
  `request` holds the brief (id, family, tier, prompt, timeout_s, tags) and the seed. IL1 needs the same thing to run
  a candidate on Practice tasks, so IL1 should build it.
