# E2 repair round (2026-10-12): where the card and the tests conflict

Rule for this round: the test wins. Four red-team tests in `tests/observability/test_redteam_e2.py` keep their
`xfail(strict=True)` mark because no code can satisfy both them and the card (or the exam). One card sentence was read
against its literal wording to keep the exam passing. Nothing in any test was changed.

## 1. RT-1: the 5-seed minimum makes both RT-1 tests fail by design

- `test_null_change_false_accept_rate_at_minimum_seeds` calls `verify_gain` with 3 seeds and wants a rate <= 1%.
- `test_a_null_change_can_be_pushed_through_by_choosing_seeds` loops over 3-seed subsets.

The card says fewer than 5 seeds raises `ValueError`. Both tests now raise `ValueError` at their first call, so they
still fail (xfail). The break they prove is closed: with 3 seeds `causal` refuses to answer. At the new minimum
(5 seeds) the null false-accept rate is 0.37% (red team, 6000 trials) and 0.55% (my eval, 2000 trials), both <= 1%.
**What I would do:** the red team (or the human) rewrites both tests to use `causal.MIN_SEEDS` seeds, and the second
to loop over 5-seed subsets. I expect both to pass then, but I did not edit them.

## 2. RT-7: "real=True with a negative gain is refused" vs the exam and the card

`test_organ_scores_reject_a_negative_real_gain_and_a_repeated_proposal` wants
(a) `organ_scores([{"organ": "a", "gain": -0.5, "real": True}])` to raise, and
(b) two identical proposals with no gene id to raise.

- (a) conflicts with the card ("a real negative gain lowers the organ's score; scores can be negative") and with the
  exam: `test_causal.py::test_organ_scores_ignore_proposal_order` draws gains from (-0.05, 0.3) with random `real`
  and expects the plain sum over `real` items, negatives included. Negative real gains are counted.
- (b) conflicts with the exam: `test_causal.py::test_a_generator_is_read_in_full` passes the same proposal (no gene
  id) three times and expects 0.3. Without an id, two identical proposals cannot be told apart from two real ones.

**Done instead:** the card's "every proposal carries a gene id" is implemented as an *optional* `gene` key, because
the exam's proposals have none. When present, the same id twice in one call raises `ValueError`.
`Attribution.proposals(organ_of)` always sets it. **What I would do:** make `gene` required once the exam is revised.

## 3. RT-7: "an organ that only proposes harmful changes scores below a quiet one"

`test_organ_that_only_proposes_harmful_changes_scores_below_a_quiet_organ` passes `real=a.genes["bad"].real` for a
harmful gene. That is `False`, and the exam (`test_a_harmful_gene_shows_a_negative_effect`) requires it to stay
`False`. So `organ_scores` would have to penalise an *unreal* negative gain. The exam's
`test_organ_scores_ignore_proposal_order` has unreal gains down to -0.05 and expects them to add nothing. Only a
penalty cut-off below -0.05 would pass both, and that is a number tuned to the tests, not a rule.

**Done instead:** `Attribution.proposals(organ_of)` builds proposals whose `real` is True when the gene's harm is
real (removing it is a real gain under the same OBS3 rules). Through it, the harmful organ scores -0.2, below the
quiet organ's 0.0 (`test_causal_eval.py::test_real_harm_lowers_the_organ_score_through_proposals`).
**What I would do:** the red-team test builds its proposals with `a.proposals(...)`.

## 4. RT-2: "a true +0.01 is real at every score level"

The card's wording says *real*. Decision 0011 rule 7 says a gain equal to the floor is *not* real, and the exam
(`test_gain_equal_to_floor_is_not_real`) asserts that. The tolerance was added on the side that keeps rule 7:
`effect <= 0.01 + 1e-12` is not real, so a true +0.01 is *not real* at every score level. The red-team test only asks
for the same answer at every level, and passes.
