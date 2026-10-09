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
