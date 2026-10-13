# 0029 · The Sealed Vault (AR4)

Date: 2026-10-13. Owner: the human (AR4 is a human node), built with Claude. Builds on 0022 (Frontier), 0025 (sandbox).
Code: `tanishi/arena/sealed.py`, `tanishi/arena/canary_scan.py`, `scripts/vault/`. Tests: `tests/arena/test_sealed.py`,
`tests/arena/test_canary_scan.py`. The tasks and their checks are not in this repo and never will be.

1. **Who can read it.** The vault is the home of the hidden OS user `tanishi-vault` (uid 401-499, no password, no login
   shell), mode 0700. No process of the agent's user can list or read it; `scripts/vault/check_vault.sh` proves that
   on the Mac for gate G0. The tasks reach the Mac only as an AES-256 bundle decrypted straight into that home by
   `setup_vault.sh`; plaintext never sits in the agent's folders. Its private GitHub backup holds only that ciphertext.
2. **What it holds.** 36 tasks, three per E3 seed capability (`cap:<id>` tag), in the Frontier store format (0022): the
   same salt, HMAC task hashes and store checks. Every task's `verifier` is `tanishi.arena.sealed.check`, which runs
   the vault's own `~/sealed/checks.py` (a private file, imported by path, never via `sys.path`). Families match E3:
   coding (write, fix, SQL), reasoning (arithmetic, dates, extraction), instruction, tool_use (read, write, system),
   memory, conversation. No LLM judge anywhere: every check is code. Tool tasks need a real, successful registry call
   that produced the answer (AR2's `_tool_check`). Code tasks run only through `run_python_tests` (isolating sandbox).
3. **One door.** The agent's user may run exactly one command as the vault, through `/etc/sudoers.d/tanishi-vault`:
   the root-owned wrapper `/usr/local/libexec/tanishi-vault/vault-run` (`evaluate`, `manifest`, `status`, `verify`).
   sudo drops the caller's environment and the wrapper sets the vault's own, so no caller can widen the executor
   list, point at another store or raise the budget. Executors resolve only under `tanishi.` from the vault's pinned
   runtime (a non-editable install of a main commit the human chose), never from the candidate.
4. **What crosses back.** Only `{task_id_hash, score, cost}` per task, plus the read id, the candidate's content hash and
   an HMAC receipt (`~/sealed/receipt.key`), printed and copied to `/Users/Shared/tanishi-vault/reads/`. No id, prompt,
   reason, output or error text. `SealedResult.mean` raises on an empty list: an empty vault is never a pass.
5. **Reads are scarce.** One read per candidate content hash, ever, and 6 reads per UTC day across all candidates
   (`TANISHI_SEALED_DAILY_READS`, capped at 20, set only in the wrapper). The charge is written before the run: a crash
   still counts. Every read and every refusal is a line in `~/sealed/reads.jsonl`.
6. **The candidate.** The client sends only `tanishi/` and `pyproject.toml` of a checkout, as a gzip tarball on stdin;
   no `.git`, databases, `.env*`, caches or guarded names. The vault refuses any other entry (links, `..`, absolute
   names, a name given twice, oversize) and copies it into `~/staging/<id>/`, deleted after the read.
7. **Canaries and the leak lock.** Every task carries its own `TNSH-CANARY-<32 hex>` in its prompt, its fixtures and the
   checks file. `canary_scan.py` (stdlib only, Python 3.9+) runs nightly at 03:17 as root from a root-owned copy:
   the repo and every worktree, `~/.tanishi` (Core State, skills, traces, memory), `~/.claude/projects` (agent
   transcripts) and the repo's whole git object store (run as the repo's owner, never as root). A hit writes the
   report, creates `/Users/Shared/tanishi-vault/LEAK` and sends one Telegram message. While LEAK exists the vault
   refuses every read. Only the human removes it, after rotating the leaked tasks. No file in this repo may hold a
   whole canary (`test_the_repo_holds_no_canary`); tests build them at run time.
8. **Code tasks need an isolating sandbox.** A store with `code`-tagged tasks is refused (exit 4,
   `no_isolating_sandbox`) where `Sandbox(trusted=False)` is unavailable. On macOS that is the vault user without
   Docker. Until the human decides `open-problems/AR4-candidate-access.md`, real sealed reads on the Mac wait; the
   selftest skips the 6 code tasks there and says so.

## Not decided here

- How a real candidate's own code runs inside the vault: `open-problems/AR4-candidate-access.md` (needs the human).
- Rotation: replacing leaked or saturated tasks is a manual step (new bundle, new canaries, LEAK removed).
