# CS1 · design problems left for later nodes

Not fixed in the CS1 repair round on purpose. Each one needs a node that does not exist yet.

## 1. `open_db()` hands out a read-write connection to anyone (W1, substrate API)

Any code that holds the connection can rewrite `experiments.score` or `capabilities.ability`, including a candidate under test
grading itself. Candidates must never hold this connection. Fix this with W1 (the Warden) and the substrate API. Give readers a
read-only open (`file:...?mode=ro` URI). Make the only write path for `experiments` and `capabilities` a single writer that the
candidate cannot reach.

## 2. An append-only log cannot redact a leaked secret (CS2)

`events` now refuses UPDATE and DELETE (decision 0007), and the CS2 spec hashes the whole row, payload included. If a secret or
personal text ever lands in a payload, the only cure is rebuilding the chain from that row on.
**Proposal for CS2:** chain the hash over `payload_sha256` (plus the other fields), not over the raw payload. Keep the payload in a
separate `event_payloads(event_id, payload)` table that can be redacted, with its own audited redaction path. The chain stays
verifiable, and the secret can be removed. The event writer must also scrub before writing (CLAUDE.md: no secrets in events).

## 3. Tamper tests must drop the triggers first (CS2)

The schema blocks UPDATE on `events`, and DELETE of hashed rows, with `events_no_update` and `events_no_delete`. A CS2 test that
proves the verifier catches a rewritten or deleted row must first run `DROP TRIGGER events_no_update` (or `events_no_delete`) on
its temp database. Otherwise the tamper itself fails and the test passes for the wrong reason.
Also, `prev_hash` is UNIQUE, so the CS2 writer must read the tail and insert inside one `BEGIN IMMEDIATE`, and retry on
`UNIQUE constraint failed: events.prev_hash`.
