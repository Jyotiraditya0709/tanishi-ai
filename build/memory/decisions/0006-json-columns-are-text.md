# 0006 · JSON is stored in TEXT columns

Date: 2026-10-09. Decided by: the human, on the CS1 builder's finding.

SQLite gives a column declared JSON numeric affinity, so a JSON value such as '1.0' comes back as the number 1.
Every column that holds JSON is declared TEXT. Code writes json.dumps(...) and reads json.loads(...).
