"""Suite-wide isolation: no test may read or write the real ~/.tanishi (CLAUDE.md)."""
import pytest


@pytest.fixture(autouse=True)
def _temporary_tanishi_home(tmp_path_factory, monkeypatch):
    """Point HOME, and so the default TANISHI_HOME (~/.tanishi), and the Core State db at a temp dir.

    TANISHI_HOME itself is unset rather than set, so tests that move HOME still see the default
    path follow it (decision 0007).
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("TANISHI_HOME", raising=False)
    monkeypatch.setenv("TANISHI_CORE_STATE_DB", str(home / ".tanishi" / "core_state.db"))
    yield home
