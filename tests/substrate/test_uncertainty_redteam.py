"""SUB4 red team: attacks on capability calibration. See build/memory/runs/redteam-SUB4-20261010.md."""
import time
from contextlib import closing

import pytest

from tanishi.core_state import migrate, open_db
from tanishi.core_state import predictions as P
from tanishi.substrate import uncertainty as U


def _db():
    conn = open_db()
    migrate(conn)
    return closing(conn)


def _tagged(cap, confidence=0.9, outcome=True, via=None):
    pid = P.predict("self:probe", {"success": True, "capability": cap}, confidence)
    (via or U.resolve_prediction)(pid, {"success": outcome})
    return pid


def test_b1_prediction_resolved_through_cs5_leaves_the_table_empty():
    """B1: ToolRegistry resolves through predictions.resolve(), so the acceptance line ("resolving predictions
    updates capabilities.calibration_error") only holds for callers that know to use uncertainty.resolve_prediction."""
    _tagged("code", 0.9, False, via=P.resolve)
    with _db() as conn:
        row = conn.execute("SELECT calibration_error FROM capabilities WHERE id = 'code'").fetchone()
    assert row is not None and row[0] == pytest.approx(0.9)


def test_b1b_router_read_is_stale_after_a_cs5_resolve():
    """B1b: even get_capability(), which the router calls, does not see evidence that is already in the ledger."""
    _tagged("code", 0.9, True)
    _tagged("code", 0.9, False, via=P.resolve)
    assert U.get_capability("code").ability == pytest.approx(0.5)


def test_b2_tag_with_nul_never_matches_its_own_evidence():
    """B2: json_extract truncates at NUL, so a tag containing U+0000 is silently never counted."""
    _tagged("co\x00de", 0.9, False)
    assert U.update_calibration("co\x00de") == pytest.approx(0.9)


@pytest.mark.parametrize("call", ["get_capability", "update_calibration"])
def test_b3_lone_surrogate_name_crashes_with_unicode_error(call):
    """B3: the router asking about a name with a lone surrogate gets UnicodeEncodeError, not the documented
    TypeError / ValueError / unknown reading."""
    try:
        getattr(U, call)("co\udc80de")
    except (TypeError, ValueError) as e:
        assert not isinstance(e, UnicodeError)
    # reaching here means it returned a reading: also fine


def test_b4_distinct_secret_shaped_tags_collapse_into_one_capability():
    """B4: CS5 redacts the tag before storing, so two different secret-looking tags share one stored text and
    one capability row, and neither can be read back by its own name."""
    _tagged("sk-" + "a" * 30, 0.9, True)
    _tagged("sk-" + "b" * 30, 0.9, False)
    assert U.get_capability("[REDACTED:openai]").ability is None


@pytest.mark.parametrize("name", ["token counting", "api_key rotation", "password-manager use", "secret santa planning"])
def test_b5_ordinary_capability_names_survive_redaction(name):
    """B5: capability names that merely mention a secret word must be readable by their own name."""
    _tagged(name, 0.9, False)
    assert U.get_capability(name).calibration_error == pytest.approx(0.9)


def test_b6_one_lucky_prediction_reads_as_perfect():
    """B6: a single resolved prediction at confidence 1.0 gives ability 1.0 and calibration_error 0.0: the read
    carries no evidence count, so the router cannot tell it from a thousand."""
    _tagged("newskill", 1.0, True)
    r = U.get_capability("newskill")
    assert not (r.ability == 1.0 and r.calibration_error == 0.0)


@pytest.mark.xfail(strict=True, reason="design: same root as CS5, no call_id provenance")
def test_b7_self_graded_predictions_set_the_router_read():
    """B7: nothing ties a tagged prediction to reality. A caller that writes and resolves its own predictions
    owns the capability's ability. (Same root as CS5's open design point: no call_id.)"""
    for _ in range(20):
        _tagged("surgery", 0.5 + 0.0, True)
    r = U.get_capability("surgery")
    assert r.ability < 1.0, "20 self-written, self-resolved predictions produced ability 1.0 with no external check"


def test_b8_resolve_cost_does_not_grow_with_the_ledger():
    """B8: every resolution scans the whole ledger with json_extract and re-parses every row of the capability,
    so each resolve costs the whole ledger: 40k tool rows plus 20k rows of one capability."""
    plain = '{"success": true}'
    tagged = '{"success": true, "capability": "code"}'
    rows = [(f"pred_u{i}", "t", "tool:x", plain, 0.8, plain, "t", 0.04) for i in range(40000)]
    rows += [(f"pred_t{i}", "t", "self:x", tagged, 0.8, plain, "t", 0.04) for i in range(20000)]
    with _db() as conn, conn:
        conn.executemany("INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?)", rows)
    pid = P.predict("self:probe", {"success": True, "capability": "code"}, 0.9)
    t = time.perf_counter()
    U.resolve_prediction(pid, {"success": True})
    elapsed = time.perf_counter() - t
    assert elapsed < 0.25, f"one resolve took {elapsed:.2f}s"
