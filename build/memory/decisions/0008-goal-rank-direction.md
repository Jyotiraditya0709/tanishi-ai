# 0008 · Goal rank: higher comes first

Date: 2026-10-09. Decided by: the CS4 implementer, because the spec says "then by rank" and gives no direction. The
human may overrule it.

- `active_goals()` orders by owner (all `user` goals, then all `tanishi` goals), then `rank DESC`, then `created_at`,
  then rowid. A rank is a priority score, so 0.9 matters more than 0.1. The default is 0.5.
- Rank is any finite number. `add_goal` rejects NaN, ±inf, bools and non-numbers. There is no [0, 1] bound, because the
  exam uses ranks like -1e9 and 1e6.
- Only `status = 'active'` is listed. `add_goal` writes `active`. Other modules may set any other status (the exam uses
  `done` and `dropped`).
- Goal ids are `uuid4().hex` and never change. A trigger refuses `UPDATE goals SET id`.
