# CS7 · snapshot and continuity limits known at merge

- [ ] **"Signed" is an HMAC, not a public-key signature.** Anyone holding `TANISHI_SNAPSHOT_SIGNING_KEY` can forge a bundle.
  Fine while both copies are hers; if a bundle must be verifiable by someone who cannot write one, move to Ed25519
  (`cryptography` already has it) under a new `format` number.
- [ ] **Whole bundle in memory.** Fernet has no streaming mode, so snapshot() and restore() hold the db plus skills in RAM
  (about 3x the home size at peak). Fine for megabytes, not for gigabytes. Fix: chunked encryption (e.g. AES-GCM per chunk).
- [ ] **Nightly and "two places" are not wired.** snapshot(dest) writes one bundle; the nightly node must call it once per
  destination and own retention and pruning of old bundles. Nothing here deletes old bundles.
- [ ] **No key rotation.** A bundle opens only with the keys it was written with. Losing the keys loses every snapshot.
- [ ] **Skill file modes are not kept.** Restored files are 0600, dirs 0700; an executable skill loses its x bit.
- [ ] **Continuity "from memory" is only as honest as the answerer.** `continuity.run()` takes the answerer from the caller
  because the brain does not read the Core State yet. Nothing stops an answerer that knows the answers without reading
  `home`. Fix when the brain lands: also run the answerer against an empty home and count any question it gets right there
  as not answered from memory.
- [ ] **snapshot() waits up to 5 s** for long readers to let the WAL checkpoint finish, then raises. A reader that holds a
  read transaction open for longer blocks the nightly snapshot.
- [ ] **The Continuity Test is an oracle.** `run()` gives each hidden question to the answerer in plain text and says how many
  were right. A candidate that may call it repeatedly can learn the answers by trial. Run the vault file only from a
  process the candidate cannot call, and never hand it the result or `missed` (which holds personal question text).
- [ ] **Answers must be the bare fact.** Matching is whole-answer after normalising (repair round, R7). When the brain answers
  in prose ("It was X"), it fails. The brain-side answerer must extract the bare answer, or the matching must be loosened
  under a new rule that keeps the R4/R6/R7 red-team tests passing. The exam docstring still says "contains"; no exam
  test needs that, so nothing conflicts today.
- [ ] **Old bundles may be restored** (rollback). By design for now; the nightly node should choose by manifest
  `created_at`, not file mtime.
- [ ] **quick_check holds the write lock.** snapshot() runs `PRAGMA quick_check` inside `BEGIN IMMEDIATE`, so writers wait for
  a full scan of the db. Milliseconds today; for a large db, check a copy instead.
- [ ] **Restorable on the same OS.** A bundle from POSIX with `\` in a skill name is refused on Windows, and two skill names
  that differ only by case are refused (as `SnapshotError`) on a case-insensitive filesystem.
- [ ] **Sweeping is best effort.** Debris is swept only on the next snapshot() into the same destination or restore() beside
  the same parent. A reused pid delays the sweep. Staging made by code before the repair round (`.snapshot-*.part`,
  `.<home>.restore-*`) has no pid in its name and is never swept; delete it by hand. On Windows the sweep goes by age (24 h).
