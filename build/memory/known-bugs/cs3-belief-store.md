# CS3 · belief store limits known at merge

- **Every predicate is treated as single-valued.** Two live beliefs `("user", "likes", "tea")` and `("user", "likes", "coffee")`
  are contested, though both can be true. The spec defines contradiction this way. The legacy importer avoids it by giving every
  free-text memory its own subject (`memory:<id>`). Fix later: a list of multi-valued predicates, or a `cardinality` on predicates.
- **Matching is exact.** `"Blue"` and `"blue"` contradict each other; `"blue "` too. There is no normalisation.
- **Conflict and retire events are unhashed** rows in `events` until CS2 lands, so they are still deletable (decision 0007, point 2).
- **The importer opens and migrates the db once per legacy row.** Fine for hundreds of rows; slow for tens of thousands.
- **A changed `core_memory` value leaves the old belief contested**, not retired. The importer cannot tell an update from a contradiction.
