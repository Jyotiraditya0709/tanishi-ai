# `.gitignore` line `runner.*` hid any file named runner.py (fixed)

Found by AR1, 2026-10-09. Fixed in the AR1 repair round the same day (human decision): the line is now `/runner.*`,
so it only matches files at the repo root (legacy autoresearch output), not `tanishi/arena/runner.py` or any other
`runner.py` in a subfolder.

What happened: `git add tanishi/arena` silently skipped the spec's own file, and a commit went out without it. AR1
force-added it (`git add -f`). If a pattern ever hides a file again, `git show --stat` after committing catches it.
