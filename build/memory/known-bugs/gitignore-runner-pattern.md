# `.gitignore` line `runner.*` hides any file named runner.py

Found by AR1, 2026-10-09. Not fixed (outside the AR1 spec).

`.gitignore` has a bare `runner.*` (probably meant for legacy autoresearch output). It matches `tanishi/arena/runner.py`,
so `git add tanishi/arena` silently skipped the spec's own file and a commit went out without it. AR1 force-added it
(`git add -f`), and a tracked file is no longer ignored, so it is safe now.

Any new `runner.py` (or `runner.json`, ...) anywhere in the repo will be dropped the same way. Smallest fix: anchor the
pattern to the path it was meant for, or add `!tanishi/**/runner.py`. Until then, check `git show --stat` after committing.
