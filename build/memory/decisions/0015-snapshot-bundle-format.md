# 0015 · Snapshot bundle format and keys (CS7)

Date: 2026-10-09. Decided by: the CS7 implementer, matching the CS7 exam (`open-problems/CS7-exam-assumptions.md`).
Numbered 0014 until the CS7 repair round, which renamed it to 0015 and added the repair rules below. The bundle
`format` is still 1: the repair only widened what restore() accepts (backslash names on POSIX) and added checks.

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
  restore() refuses any path that is not `core_state.db`, `identity.yaml` or under `skills/`, any `..`, absolute or NUL
  path, and any path holding a separator of the restoring OS other than `/` (so `\` is refused on Windows only).
  Symlinks in the source are refused, not followed. No `-wal`/`-shm`; legacy dbs are never touched.
- **Capture uses the restore rule.** snapshot() checks every skills path with the same rule restore() uses, and refuses a
  name that is not valid UTF-8, raising `SnapshotError` ("rename it"). A bundle it writes always restores on the same OS.
- **Destination inside the home.** A destination under `skills/` is left out of the bundle; `skills/` itself as the
  destination is refused.
- **Byte for byte.** snapshot() runs `wal_checkpoint(TRUNCATE)`, takes `BEGIN IMMEDIATE`, confirms the `-wal` file is
  empty, runs `PRAGMA quick_check` and reads the main file while holding the lock. The live file after snapshot() equals
  the bundled one. Anything but `ok` raises `SnapshotError` and writes nothing.
- **Restore.** Target must be missing or an empty directory. Everything is verified in memory, written into a staging dir
  beside the target with per-file sha256 checked on disk, the staged db is `quick_check`ed (opened `mode=ro&immutable=1`
  so no `-wal`/`-shm` appears), then the dir is renamed onto the target. Dirs 0700, files 0600.
- **Staging names.** `.tanishi-snapshot-<pid>-*.part` in the destination, `.tanishi-restore-<pid>-*` beside the target.
  snapshot() sweeps its destination and restore() sweeps the target's parent for these names whose pid is no longer
  running (POSIX), or older than 24 h (elsewhere). Own-pid entries are never swept.

Changing any of this needs a new `format` number and a restore path for format 1 bundles already on disk.
