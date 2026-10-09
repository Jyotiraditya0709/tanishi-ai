# 0004 · No agent writes its own exam

Date: 2026-10-09.

For every node, a testing agent writes tests from the spec, apart from the implementation. Builder Arena tasks also have
hidden acceptance tests in the separate builder vault repo, which implementation agents can never read.
A test that passes against an empty implementation is rejected.
