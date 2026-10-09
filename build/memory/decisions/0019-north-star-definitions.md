# Decision 0019: North Star number definitions (from the master plan)

These are the master plan's definitions for the six North Star numbers OBS2 computes. They are copied from
"Tanishi: The Recursive Staircase" (architecture v1.0), not invented here. For each number: the exact formula,
the Core State source it reads, and the precondition that turns a `None`/0 into a real value. OBS2 keeps
returning `None` with a reason until the precondition is met; do not substitute a guessed formula.

A **researcher-day is 8 hours** (from the RCR worked example: 10 hours = 1.25 researcher-days).
All rolling windows are inclusive of `window_days` back from now.

## CEI — Capability Expansion Index (central objective)
CEI = B × T × H × N × A × P, over a rolling 90 days. A zero in any factor zeroes CEI (deliberate).
Worked example in the plan: B=3, T=1.2, H=1.5, N=0.5, A=0.8, P=0.9 → 1.94.

- **B, breadth** — count of capability *clusters* (communities in the Capability Graph, not single nodes)
  in which a capability newly reached `mastered` in the window. Fifty nodes in one cluster count once.
  Source: `capabilities` grouped by cluster/family. Precondition: a cluster field on capabilities.
  Interim: group by `capabilities.family`; note it as a proxy for cluster until the Capability Graph lands.
- **T, transfer** — 1 + the average fractional gain on *sealed* tasks in clusters she did NOT train on.
  Negative transfer pulls T below 1 (forgetting is punished). Source: Frontier/sealed Arena results (AR3/AR4)
  plus a record of which clusters were trained. Precondition: AR3 + a trained-cluster record. Until then: None.
- **H, task-horizon** — longest task in human-expert-minutes she completes at 50% success now ÷ the same
  figure 90 days ago (METR time-horizon, arxiv 2503.14499). Source: Arena tasks tagged with human-expert
  minutes + success-by-horizon over time. Precondition: horizon-tagged tasks AND 90 days of history. Until then: None.
- **N, environment novelty** — share of the new mastery verified in environments she did NOT author
  (the Reality Engine). Until reality confirms something, CEI is 0. Source: Reality Engine verifications.
  Precondition: the Reality Engine. Until then N = 0, so **CEI = 0 is the correct current value.**
- **A, autonomy** — 1 − the human share of the effort that went into acquiring the mastery, from the Human
  Effort timer. Source: OBS2 effort timer, attributed per capability. Precondition: per-capability effort
  attribution (needs the traces/attribution link). Until then: None.
- **P, persistence** — share of the new mastery still held 90 days later, re-tested after any model swap in
  that window. Source: `capabilities` state history + re-tests. Precondition: 90 days of history. Until then: None.

## RCR — Research Compression Ratio (above the staircase)
RCR = (replicated discoveries × 30 researcher-days) ÷ (human researcher-days spent on research).
Only the final funnel stage (replicated discoveries) counts. Worked example: 3 replicated discoveries in a
month for 10 hours (1.25 researcher-days) → 72. Source: experiment ledger (OBS3) for replicated discoveries +
the Human Effort timer for researcher-days. Already partly computable; keep the <0.1h floor (OBS2 red-team fix).

## CAR — Capability Acquisition Rate (CEI's fast, narrow input)
Capabilities that newly reach `mastered` on sealed, held-out frontier tasks, per 30 days.
Source: `capabilities` reaching `mastered` via Frontier/sealed verification. Precondition: AR3 + AR4. Until then: None.

## AR — Autonomy Ratio (who is doing the improving)
AR = Arena gain from changes Tanishi authored ÷ Arena gain from changes a human authored, rolling 30 days.
Source: experiment ledger (OBS3) with an author field on each change, scored by OBS3's is_real_gain.
Precondition: experiments/genome record who authored each change. Computable once that field exists.

## IA — Improvement Acceleration (whether expansion compounds)
IA = the slope of CEI over the last 90 days. Source: a stored CEI history. Precondition: 90 days of CEI
snapshots (and CEI itself must be non-zero, i.e. the Reality Engine). Until then: None. (The plan itself lists
IA's Day-1 value as "not defined yet.")

## HE — Human Effort (what it costs you)
Hours spent engineering her per week, from the repo timer. Source: OBS2 effort timer. **Measured now.**

## Note for the follow-up exam and build
A second OBS2 exam can seed Core State rows and check real values ONLY for numbers whose precondition is met
(now: HE; soon: RCR, AR, B-by-family). The rest must stay None-with-reason and be tested as "returns None with
the stated reason" until their data source lands. Never let a number read a value from a source that does not
yet exist.
