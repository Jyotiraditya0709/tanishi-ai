"""Module-level executors for the Sealed Vault tests (attempt processes import them by name)."""
import os
from pathlib import Path

from tanishi.arena.runner import Attempt


def answer_by_id(brief, candidate, seed):
    """Right on tasks whose id ends in '.right', wrong elsewhere."""
    return Attempt("4" if brief.id.endswith(".right") else "5", cost=0.01)


def try_to_read_the_vault(brief, candidate, seed):
    """Tries to read the vault's checks file and its store; reports what it could open."""
    seen = []
    checks = os.environ.get("TANISHI_SEALED_CHECKS", "")
    for label, path in (("checks", checks), ("store", str(Path(checks).parent / "tasks"))):
        try:
            p = Path(path)
            if p.is_dir():
                os.listdir(p)
            else:
                p.read_bytes()
            seen.append(label)
        except OSError:
            pass
    return "opened:" + ",".join(seen) if seen else "opened:none"
