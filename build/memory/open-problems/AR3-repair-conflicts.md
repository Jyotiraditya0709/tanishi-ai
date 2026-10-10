# AR3 repair round: where the card and a red-team test conflict (2026-10-10)

The card says the test wins. Both tests are unchanged; the human decides what to do next.

## RT-5 · a query budget cannot make `test_a_hidden_needle_cannot_be_bisected_out_of_the_scores` pass

- **Card:** each candidate gets `DAILY_QUERY_BUDGET` (default 20) `evaluate()` calls per day; past it, refuse and record.
  Built: `QueryBudgetExceeded`, ledger `.queries` in the store, own test `test_query_budget_refuses_and_records_the_refusal`.
- **Test:** bisects 8 words with **3** calls, each with a **different** candidate string. A per-candidate budget never
  sees the same candidate twice, and even a store-wide budget of 20 allows 3 calls. The test only passes if the scores
  stop telling a matching guess from a non-matching one, i.e. if the service stops returning real scores.
- **So:** the strict xfail stays. Code alone cannot close an oracle; the red-team said so too ("also a task-design rule").
- **What I would do instead:** (1) a Frontier task-design rule, enforced when tasks are written for the store: no verifier
  whose answer is an enumerable short token (`contains`/`exact` on one word); prefer tasks whose answers are long or
  structured. (2) A store-wide daily budget across all candidates (one agent can mint unlimited candidate strings),
  and a budget measured in tasks×seeds, not calls. (3) Re-word the red-team test to the budget it can check (e.g. 21
  calls with one candidate refused), or mark it as a design limit. All three are human calls.

## RT-11 · the card says load `*.yml`; `test_a_task_file_with_another_extension_is_refused_not_skipped` says refuse it

- **Card:** "load *.yml as well as *.yaml".
- **Test:** a store holding a valid `t1.yaml` and a valid `t2.yml` (different ids) must raise `ValueError`. Loading the
  `.yml` file returns two scores and no error, so the test fails. There is no reading that satisfies both, short of
  inventing a "no mixed extensions" rule nobody asked for.
- **Built (test wins):** any entry other than a `*.yaml` task file and the service's own files (`.salt`, `.queries`,
  `.salt-*.tmp`) refuses the whole store with `ValueError("... rename .yml files to .yaml")`. Nothing is skipped, which
  is the red-team's actual concern. A store of only `.yml` files is refused too.
- **If the human prefers the card:** change the test to expect two scores, then accept `*.yml` in `_checked_store` and
  `_load_all` (and refuse a `.yaml`/`.yml` pair with the same stem).
