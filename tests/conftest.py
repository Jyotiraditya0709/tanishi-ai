"""Suite-wide isolation: no test may read or write the real ~/.tanishi (CLAUDE.md)."""
import pytest


@pytest.fixture(autouse=True)
def _temporary_tanishi_home(tmp_path_factory):
    """Point HOME, and so the default TANISHI_HOME (~/.tanishi), and the Core State db at a temp dir.

    TANISHI_HOME itself is unset rather than set, so tests that move HOME still see the default
    path follow it (decision 0007). This uses its own MonkeyPatch, not the test's ``monkeypatch``
    fixture, so a test that calls ``monkeypatch.undo()`` cannot point HOME back at the real one.
    """
    home = tmp_path_factory.mktemp("home")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HOME", str(home))
        mp.setenv("USERPROFILE", str(home))
        mp.delenv("TANISHI_HOME", raising=False)
        mp.setenv("TANISHI_CORE_STATE_DB", str(home / ".tanishi" / "core_state.db"))
        yield home
