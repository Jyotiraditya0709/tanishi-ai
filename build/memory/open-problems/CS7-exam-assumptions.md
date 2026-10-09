# CS7 · what the spec leaves open (written by the tester, from the spec alone)

The CS7 card names `snapshot()`, `restore()`, a Fernet key "from env", "signed", and a Continuity Test of 10 questions.
It gives no signatures, env var names, signing scheme or question format. The exam in `tests/core_state/test_snapshot.py`
assumes only the items below. If the implementer disagrees with one, say so in `build/memory/open-problems/CS7-exam-conflicts.md`
and the human decides; do not edit the tests.

- **S1 · `snapshot(dest_dir) -> Path`.** Writes exactly one bundle file into `dest_dir` (an existing dir) and returns its path.
  Source is `$TANISHI_HOME`: `core_state.db`, `identity.yaml`, `skills/`. "Two places you control" = call it twice with two dirs.
- **S2 · `restore(bundle, home) -> None`.** `home` must be missing or empty. Any failure raises (type free) and leaves `home` empty
  or absent. A non-empty `home` is refused and left untouched.
- **S3 · Keys.** `TANISHI_SNAPSHOT_KEY` is a Fernet key; `TANISHI_SNAPSHOT_SIGNING_KEY` is the signing secret (HMAC or similar). The
  card says only "key from env" and "signed"; a separate signing secret is my reading. If the implementer signs with a key
  derived from the Fernet key, the two `wrong_signing_key` / `missing_signing_key` tests need changing by the human.
- **S4 · Byte for byte.** Compared by SHA-256 of `core_state.db` on disk **after** `snapshot()` returns, so the snapshot may
  checkpoint the WAL. Committed rows still in the `-wal` file must be in the bundle.
- **S5 · Absent optional parts.** Missing `identity.yaml` or `skills/` is not an error and is reproduced as absent.
- **S6 · Tamper.** Every single-byte flip, truncation, extension or empty file is refused. This is how "signed" is checked
  without knowing the scheme.
- **S7 · Legacy dbs.** `tanishi.db` and friends in the home are never bundled.
- **C1 · `continuity.run(questions_file, home, answerer) -> result`** with `.passed`, `.correct`, `.total`. `answerer(question, home) -> str`
  stands for "the restored instance"; the real one will be the brain reading memory, which does not exist yet, so the exam
  injects it. Questions file: JSON list of `{"question", "expected"}`; right = answer contains `expected`, case-insensitive.
- **C2 · Pass rule.** Exactly 10 questions (else `ValueError`); passes only if all 10 are right. The card says "passes only if"
  without a threshold; 10/10 is my reading, and `test_nine_of_ten_is_not_a_pass` pins it. A blank `expected` must not pass for free.
- **C3 · The real questions** are in `vault/`, which I did not read. The exam uses synthetic questions; nothing from the vault is copied.

## Not covered, and why

- **Nightly scheduling and "two places".** Belongs to the nightly node (N-series); only two-destination snapshots are tested.
- **Key rotation and key storage.** Out of scope on the card.
- **A real "answers from memory" check.** Needs the brain. The exam only proves the restored data is what an answerer would read.
- **Path traversal inside a crafted bundle.** Needs the bundle format; the red-team agent should cover it once the format exists.
