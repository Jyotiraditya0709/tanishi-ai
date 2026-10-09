"""Shared helper: CS1 tests follow the latest migration instead of hard-coding schema version 1."""
import re

import pytest

from tanishi.core_state import db


@pytest.fixture
def latest_version():
    """Highest NNNN prefix among the .sql files in tanishi/core_state/migrations/."""
    return max(int(m.group(1)) for p in db.MIGRATIONS_DIR.glob("*.sql")
               if (m := re.match(r"(\d{4})_", p.name)))
