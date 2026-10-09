# OBS3 · the repair card contradicts two red-team tests

Found in the OBS3 repair round on 2026-10-09. The card says "the test wins: leave it and write the conflict". So both tests are
unchanged and still `xfail(strict=True)`, and the code follows the human's ruling. The suite has **2 xfailed** until someone rules.

| Red-team test | What it asserts | Card ruling | Why both cannot hold | Smallest amendment |
|---|---|---|---|---|
| `test_rerun_of_same_seed_is_one_sample_not_three` | Records (a, t, 1) and (b, t, 1) three times each, expects one sample per arm, *then* expects a fourth `record_run` of (a, t, 1) to raise `ValueError` | Fix 2: `record_run` refuses the **second** row for the same arm, task and seed | The test needs the 2nd and 3rd identical rows accepted and the 4th refused. No rule refuses a repeat after the third but not after the first, and accepting rows then averaging them is the break itself | Record the first (a, t, 1) / (b, t, 1) once, then assert the repeat raises and `seed_scores` still gives one sample per arm |
| `test_string_tasks_not_split_into_characters` | `interleave("a", "b", "abc", [1, 2, 3])` returns runs whose task is `"abc"` | Fix 5: `interleave` refuses a `str` for tasks or seeds | Refusing means raising, so there are no runs to check. The test asks for the other fix (treat a lone string as one task) | `with pytest.raises(ValueError): interleave("a", "b", "abc", [1, 2, 3])` |

If the human would rather the test win on behaviour (not just on the assertion), the string case is easy to flip: wrap a lone
`str` as `[tasks]` instead of raising. The rerun case cannot be satisfied by any sensible rule.

**Status: resolved (2026-10-09).** Both conflicts are resolved by rewriting the two tests to the human decisions (a repeat row is refused with `ValueError`; a lone `str` for tasks or seeds is refused with `ValueError`). The code was not changed.
