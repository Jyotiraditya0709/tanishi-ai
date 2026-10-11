# E3 · nightly clustering evaluation (2026-10-11)

Source: `tests/frontier/test_clustering_eval.py` (in CI). Synthetic, shaped like real decisive steps (span name +
error class): 8 planted failure modes of 3..40 traces over 3 families, one spread over two span names, plus 300
one-off failures that reuse the same span names with unique error classes. 405 steps, 5 shuffles/seeds.

| metric | result |
|---|---|
| planted modes recovered exactly | 8 / 8 on every seed |
| false clusters | 0 |
| pairwise precision / recall | 1.0 / 1.0 |
| time per run (405 steps) | under 10 ms |

With Jaccard >= 0.5 (the first version), `tool.read_file FileNotFoundError` and `tool.read_file TimeoutError`
merged, and one-off failures on the same tool chained into them. Strict > 0.5 fixed it.

**What this does not show.** Real step texts are this regular, so near-perfect scores are expected. It says nothing
about quality on the real trace corpus, which does not exist yet: nothing in production opens spans (0023 rule 11).
Re-run on real traces once the Brain and ToolRegistry are wired.
