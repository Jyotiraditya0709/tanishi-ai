# 0014 · Snapshot bundle format and keys (CS7)

Date: 2026-10-09. Decided by: the CS7 implementer, matching the CS7 exam (`open-problems/CS7-exam-assumptions.md`).

- **Keys.** `TANISHI_SNAPSHOT_KEY` is a Fernet key (encryption). `TANISHI_SNAPSHOT_SIGNING_KEY` is a separate HMAC secret
  (signature). Both are read from the environment only, at call time; a missing or invalid key raises `SnapshotError`
  before anything is read or written. No error message carries a key or file content.
- **File.** `MAGIC "TNSNAP01" | HMAC-SHA256(signing key, MAGIC + token) | Fernet token`, named
  `core_state-<UTC stamp>-<8 hex>.tanishi-snapshot`, mode 0600, written to a temp file and renamed into place.
  Encrypt-then-MAC: restore() checks the MAC before it decrypts.
- **Payload** (inside the token). 8-byte big-endian header length, a JSON manifest
  `{"format": 1, "created_at", "dirs": [...], "files": [{"path", "size", "sha256"}, ...]}`, then the file bytes in manifest
  order with nothing after the last one.
- **What goes in.** `core_state.db` (required), `identity.yaml` and `skills/` (optional, absent stays absent). Nothing else:
  restore() refuses any path that is not `core_state.db`, `identity.yaml` or under `skills/`, and any `..`, absolute or
  backslash path. Symlinks in the source are refused, not followed. No `-wal`/`-shm`; legacy dbs are never touched.
- **Byte for byte.** snapshot() runs `wal_checkpoint(TRUNCATE)`, takes `BEGIN IMMEDIATE`, confirms the `-wal` file is
  empty and reads the main file while holding the lock. The live file after snapshot() equals the bundled one.
- **Restore.** Target must be missing or an empty directory. Everything is verified in memory, written into a staging dir
  beside the target with per-file sha256 checked on disk, then renamed onto the target. Dirs 0700, files 0600.

Changing any of this needs a new `format` number and a restore path for format 1 bundles already on disk.
