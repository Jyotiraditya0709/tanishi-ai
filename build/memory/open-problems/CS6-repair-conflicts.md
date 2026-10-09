# CS6 repair · fix G6 (duplicate gene names) conflicts with the CS6 exam

The repair card says: blank or duplicate gene names are refused (G6). Blank names are now refused. Duplicates are not.

**Conflict.** `tests/core_state/test_genome.py::test_random_history_round_trips` builds `genes_changed` as
`[f"g{rng.randrange(50)}" for _ in range(rng.randint(0, 5))]`, so it sometimes repeats a name (seeds 2, 5, 6 and 7 do),
and then asserts the record reads back exactly that list. Refusing duplicates made those 4 exam cases fail with
`ValueError: gene names must not repeat`. Per the card, the test wins.

**What I did.**
- `record_version()` keeps duplicate gene names as written (a comment in `genome.py` points here).
- In `tests/core_state/test_genome_redteam.py`, the G6 test is parametrized over `[""]`, `["  "]`, `["a", "a"]`. The
  first two now pass, so the test-level `BREAK` mark could not stay (strict XPASS) and could not be removed
  (the third case fails). I moved the mark onto the `["a", "a"]` case only (`pytest.param(..., marks=BREAK_EXAM_CONFLICT)`,
  still `xfail(strict=True)`). No assertion changed.

**What I would do instead.** One of:
1. The exam owner changes the generator to unique names, for example `rng.sample(range(50), k)`. Then
   `record_version()` refuses duplicates (two lines) and the `["a", "a"]` mark goes.
2. The human decides duplicates are allowed (a gene changed twice in one version is just noise). Then the red-team
   owner drops the `["a", "a"]` case.

I recommend option 1: "genes changed" is a set in meaning, and refusing a repeat catches a buggy merge hook early.
