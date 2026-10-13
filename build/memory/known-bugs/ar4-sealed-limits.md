# AR4 · Sealed Vault limits (known, not fixed)

Decision: `decisions/0029-sealed-vault.md`. Open problem: `open-problems/AR4-candidate-access.md`.

- **No real candidate reads on the Mac yet.** The executor stage cannot read the candidate copy, and model-written code
  has no isolating backend for the vault user. See the open problem.
- **The network is open during a read.** Sealed tasks carry the `network` tag so a candidate can call its model. A
  candidate can therefore send a prompt out. Canaries catch a leak back into her own memory, not this.
- **Canaries are a tripwire, not a lock.** A candidate that knows the pattern could strip it before saving text. The
  pattern is public (this repo); the canaries themselves are only in the vault.
- **The vault runs its own runtime.** A fix merged to main reaches the vault only when the human reruns
  `setup_vault.sh --commit <sha>`. A vault-user process escaping its sandbox could change that runtime (not the
  root-owned wrapper or scanner).
- **Per-task scores are an oracle.** One read per candidate hash and 6 a day limit it; a candidate that changes one byte
  is a new candidate. E3 needs per-task outcomes, so they stay.
- **Editable installs.** AR1's executor stage grants `sys.path` entries only, so a dev checkout installed with
  `pip install -e` and run from outside the checkout cannot import `tanishi` inside the sandbox. The vault installs
  tanishi non-editable for this reason.
- **The scan reads what root can read, as files.** Encrypted, compressed (other than git objects) or remote copies of
  sealed text are not seen.
