# Red team · SUB1 state, round 2 (attack on the repair) · 2026-10-09

Proofs: `tests/substrate/test_state_redteam2.py` (13 tests; 7 fail on purpose and show a break, 6 pass and show what held).
Round 1 is `redteam-SUB1-20261009.md`. B2 and B3 (duplicate and empty step ids) are still open and still fail; not repeated here.

## Breaks

| # | Sev | Break | Test | Smallest fix |
|---|-----|-------|------|--------------|
| R1 | **Medium** | `save()` hangs on hostile text. `events.redact()` is quadratic on repeated secret words: `"password" * 5000` (40 kB) takes 2 s, 80 kB 8.7 s, 160 kB 34 s, 320 kB over two minutes. Anything the agent reads (a web page, a tool result) and puts in `working` can freeze the step that saves it. `emit()` has the same hole, since it redacts before it clips. Cause: `events._ASSIGNED_SECRETS[0]`, `password[\w-]*` runs to the end of the run at every "password", then fails to find `:` or `=`. The comment above the patterns claims they cannot do this. | `test_r2_redaction_does_not_hang_on_adversarial_text` | In `events.py` make the tail possessive: `[\w-]*+` (Python 3.11+). Defence in depth: `save()` refuses a string over a size cap (say 1 MB) with `ValueError`. The real fix is in CS2's file, so it needs its owner. |
| R2 | **Medium** | Round 1's B1 fix leaves secrets in containers. A secret-named key hides only a *text* value, so `{"password": {"value": "hunter2"}}`, `{"auth": ["..."]}` and `{"token": 987654321}` are written in plaintext. `events.redact()` hides any value under such a key. Round 1 noted this as a known gap; here it is proven, and it is a plain leak (a nested credentials dict is the usual shape). | `test_r2_container_under_secret_key_keeps_its_secret` | In `_without_secrets` use the same rule as `events._scrub`: `secret_name and v is not None and v != ""` replaces the value with `"[REDACTED]"`. Counters named `token` are rare; leaking a nested credential is worse. |
| R3 | Low | `task_id` is the one field never redacted. `job-sk-ant-...` is stored as the primary key, and the task id also goes into events and logs. | `test_r2_secret_in_task_id_is_stored_in_plaintext` | `ValueError` in `_validate_shape` if `redact(task_id) != task_id`. Refuse rather than redact, since a changed id would point at a different task. |
| R4 | Low | `TypeError` text quotes the dict key path (`working['API_KEY=... sk-ant-...']`), so a secret that is a key reaches the traceback and any log of it. | `test_r2_secret_key_name_leaks_into_error_message` | Run the `where` path through `redact()` before putting it in a message, or leave the key out. |
| R5 | Low | Look-alike statuses still re-run a finished step: `"done​"` (zero-width space) and `"dоne"` (Cyrillic о) pass the B4 check, so `next_step()` returns that step. | `test_r2_lookalike_done_silently_reruns_step` | Require `status.isascii() and status.isprintable()` and no inner blanks, or restrict to a vocabulary. |
| R6 | Low | `load("a\ud800")` raises `UnicodeEncodeError`, not the documented `LookupError`. (`save` of such an id raises it too, which is a `ValueError`, so that side is within contract.) | `test_r2_load_of_unencodable_task_id_is_lookup_error` | Catch `UnicodeEncodeError` in `load()` and raise `LookupError`. |

## Held (tried, no break)

- **Performance of normal use.** A save is under 20 ms for a small state (test asserts it); a 20 000-key, 400 kB
  `working` saves in under 2 s. The round-1 worry about `migrate()` hashing files on every call is not a problem at
  this size. It still scales with step count, so the loop should save once per step, not per token.
- **Key collisions.** Three keys that redact to the same text, one already ending in `#`, stay distinct.
- **Re-save of a loaded state** is stable (redacted text does not get redacted again into something else).
- **Redaction false positives** do change ordinary prose (`"rotate the password: monthly"` loses its value). This is
  what decision 0014 item 6 says; not a break, but a hypothesis written as prose about passwords will be mangled.
- **Positional resume.** A done step after a failed one is re-run (`next_step()` is the first not-done step). By design.
- **Schema.** No foreign key or trigger touches `substrate_state`, so `INSERT OR REPLACE` cannot cascade (the CS4 trap).
  WAL plus the busy timeout hold under the 16-process first-save race from round 1.
- Also read and fine: depth 600 nesting round-trips; lone surrogates inside values round-trip; no path to Frontier tasks
  or the Sealed Vault. I did not open `vault/` or `builder_vault/`.

## Not tried

Power-loss durability (needs a real crash rig), and very long runs with many tasks (table scan cost). Neither has a cheap proof.
