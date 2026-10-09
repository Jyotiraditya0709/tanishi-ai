# OBS3 · red-team breaks (2026-10-09)

Report: `build/memory/runs/redteam-OBS3-20261009.md`. Proofs: `tests/observability/test_redteam_obs3.py`.
Repair run: `build/memory/runs/OBS3-repair-20261009-1737.md`. Decision 0011 (rules 7-12) says how each one was fixed.

Fixed in the repair round (the test passes and its xfail mark is removed):

- [x] 1 · zero-noise arms accepted any gain: `test_zero_noise_arms_accept_a_tiny_gain`
- [x] 3 · seed missing in one arm: `test_seed_missing_in_one_arm_is_not_misaligned`
- [x] 3 · partial tasks for a seed: `test_partial_tasks_for_a_seed_bias_the_mean`
- [x] 4 · fractional seed: `test_fractional_seeds_do_not_collide`
- [x] 4 · bool seed: `test_bool_seed_rejected`
- [x] 5 · arms `1` and `"1"`: `test_arm_names_that_stringify_equal_are_rejected`
- [x] 6 · NaN cost: `test_nan_cost_not_silently_nulled`
- [x] 6 · bool scores: `test_is_real_gain_rejects_bool_scores`

Fixed in code, but the red-team test still fails (strict xfail kept, because the test conflicts with the human's ruling):

- [x] 2 · repeated rows: `record_run` refuses a second row for the same arm, task and seed. `test_rerun_of_same_seed_is_one_sample_not_three`
  records that row three times before it expects the refusal, so it fails on the second call.
- [x] 5 · string tasks: `interleave` refuses a `str` for tasks. `test_string_tasks_not_split_into_characters` expects `"abc"` to be
  accepted as one task.

Both are written up in `open-problems/OBS3-repair-conflicts.md` and need a human ruling or a red-team test amendment.
