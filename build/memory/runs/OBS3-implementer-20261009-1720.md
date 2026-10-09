# OBS3 · implementer · 2026-10-09

Built `tanishi/observability/{attribution,experiments}.py`. The exam passes 60/60, the full suite passes 245, and ruff is clean.
Own tests and evaluation: `tests/observability/test_experiments.py`. At sigma 0.1 with 3 seeds per arm, the legacy +0.001 rule keeps
about 50% of null changes. OBS3 keeps fewer than 5% of them and still finds a +0.4 gain more than 70% of the time.

1. **What did we learn?** "2x noise" is only well defined once you say whose noise. Using the max of the two arms keeps a noisy
   candidate from slipping through. Pairing each (task, seed) back to back, in random arm order, cancels drift much better
   than a plain shuffle.
2. **What failed, and why?** Nothing failed at runtime. The only snag was ruff: once `tanishi.observability` existed, ruff wanted the exam's
   imports sorted differently. I sorted them and changed no test, as CS1 did.
3. **Which assumption was wrong?** I assumed the `experiments` table had arm and task columns. It does not. Arm and task go in `meta` and
   `task_set` (decision 0008), and no new migration was needed.
4. **What should the next agent know?** `is_real_gain` raises on fewer than 3 seeds, so catch `ValueError` and keep running seeds.
   One list element is one seed: use `seed_scores()`. `ablate` double-credits interacting changes (known-bugs/obs3-limits.md).
   The CS6 merge hook is unowned.
