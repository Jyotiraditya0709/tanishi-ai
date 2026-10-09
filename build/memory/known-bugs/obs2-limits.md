# OBS2 v0: known limits

- **Anyone who can emit events can log Human Effort.** `effort_start` and `effort_stop` with actor `human` are ordinary
  events, so any code that can call `emit()` can make up hours. When the Warden (W1) lands, it should restrict those kinds
  to the CLI path.
- **A forgotten timer runs forever.** A session that was never stopped is shown as running but does not count. Once it is
  stopped days later, all of that time counts. There is no idle cap.
- **`compute()` reads every `experiments` row** and parses each ts in Python (OBS3 writes `isoformat()` without fixed
  microseconds, so a string compare could be wrong). That is fine at v0 scale and slow at millions of rows.
- **B trusts `capabilities.state`.** Nothing checks that "mastered" was really verified by Reality, because the Reality
  Engine does not exist yet.
- **Five CEI factors, CAR, AR and IA are `None`.** See open-problems/OBS2.md. Waiting on the human's
  decisions/0019-north-star-definitions.md.
- **The funnel has no multiple-comparisons rule** (red team). One candidate judged against many baselines, or many
  near-identical candidates, each count as a discovery. The hours floor stops tiny timers inflating RCR, but not this.
- **Window mismatch** (red team). The funnel picks pairs by ledger ts in the window, but `seed_scores` reads every row of
  the pair, old or new. CEI's B ignores the window.
- **`stop()` can report 0.0 hours** for the session it closed if another terminal starts one in between (cosmetic).

## Fixed in the 2026-10-09 repair round (decision 0018, items 8 to 10)

- RCR from a few seconds of timer: now `None` under 0.1 timed hours.
- Huge `window_days` (1e9, 10**400) raised OverflowError and `tanishi time report 1e9` printed a traceback: now capped
  at 36500 days with a ValueError, so the CLI exits 1 with a message.
- `cei`/`rcr` raised OverflowError on huge ints and returned inf on overflow: now ValueError on input; `rcr` raises
  ValueError on a non-finite result and `cei` returns `None` (see open-problems/OBS2-repair-conflicts.md).
