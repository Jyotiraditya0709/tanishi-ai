"""AR2 red team: each test below FAILS on the code as delivered and proves one break.

Findings are written up in build/memory/runs/redteam-AR2-20261009.md (ids RT-AR2-n). When a break is fixed, its test
must pass unchanged; do not weaken or skip any of them.
"""
import subprocess
import sys
import textwrap

import practice_executors as ex
import pytest
from practice_helpers import DATETIME_OUTPUT, SYSINFO_OUTPUT, TASKS, record_call, score

from tanishi.arena.practice import PRACTICE_DIR, legacy
from tanishi.arena.practice._common import run_python_tests, standalone_ints
from tanishi.arena.runner import run

# --- RT-AR2-1: the code runner hands the candidate the secret it checks for -------------------------------------


def test_rt1_candidate_code_cannot_read_the_nonce_from_its_own_argv():
    """`python -c <program>` keeps the whole program, nonce included, in sys.orig_argv. Code that prints it and
    exits 0 scores 1 without defining slugify at all."""
    cheat = textwrap.dedent(
        """
        import os, re, sys
        nonce = re.findall(r"print\\('([0-9a-f]{32})'\\)", sys.orig_argv[-1])[-1]
        print(nonce)
        sys.stdout.flush()
        os._exit(0)
        """
    )
    assert score("slugify_fn", cheat)[0] == 0.0


# --- RT-AR2-2: candidate code runs with the verifier's environment -----------------------------------------------


def test_rt2_candidate_code_does_not_inherit_the_callers_secrets(monkeypatch):
    """env_passthrough exists so the judge can see an API key. run_python_tests then lets model-written code read
    the same key. The environment of the test process must be minimal."""
    monkeypatch.setenv("FAKE_API_KEY", "sk-not-a-real-key")
    code = "import os\nassert 'FAKE_API_KEY' not in os.environ\n"
    assert run_python_tests(code, "").score == 1.0


# --- RT-AR2-3: code tasks that pass for the wrong reason ---------------------------------------------------------


def test_rt3_an_always_equal_object_does_not_pass_the_asserts():
    """Every assert is `f(x) == expected`. A str subclass whose __eq__ is always True passes them all."""
    cheat = textwrap.dedent(
        """
        class Yes(str):
            def __eq__(self, other): return True
            __hash__ = str.__hash__
        def slugify(text): return Yes()
        """
    )
    assert score("slugify_fn", cheat)[0] == 0.0


def test_rt3_a_lookup_table_of_the_public_cases_does_not_pass():
    """The cases sit in checks.py of an open-source repo. A table of exactly those inputs passes."""
    cheat = textwrap.dedent(
        """
        def slugify(text):
            return {"Hello, World!": "hello-world", "  many   spaces here ": "many-spaces-here",
                    "---Already--Dashed---": "already-dashed", "Rock & Roll 101": "rock-roll-101",
                    "": "", "!!!": ""}[text]
        """
    )
    assert score("slugify_fn", cheat)[0] == 0.0


def test_rt3_a_quadratic_dedupe_does_not_pass_a_task_that_says_stay_fast():
    cheat = textwrap.dedent(
        """
        def dedupe(items):
            out = []
            for x in items:
                if x not in out:
                    out.append(x)
            return out
        """
    )
    assert score("dedupe_fn", cheat)[0] == 0.0


# --- RT-AR2-4: sql_query ------------------------------------------------------------------------------------------


def test_rt4_a_query_that_hardcodes_the_expected_rows_does_not_pass():
    """One fixed dataset, rows compared: SELECT 'cy', 300.0 UNION ALL ... passes without reading the table."""
    cheat = "SELECT 'cy' AS customer, 300.0 AS total UNION ALL SELECT 'ada', 130.0 UNION ALL SELECT 'ed', 101.0"
    assert score("sql_query", cheat)[0] == 0.0


def test_rt4_a_runaway_query_is_stopped_by_the_verifier_not_by_the_task_timeout():
    """A recursive CTE never ends; the verifier has no step limit, so it hangs until the runner kills it and the
    run records a *verifier* failure for what is the candidate's fault."""
    child = (
        "from tanishi.arena.practice.checks import sql_query\n"
        "sql_query(None, 'WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT x FROM c')\n"
    )
    try:
        subprocess.run([sys.executable, "-c", child], timeout=8, check=False, capture_output=True)
    except subprocess.TimeoutExpired:
        pytest.fail("sql_query hung on a recursive CTE")


# --- RT-AR2-5: tool tasks where a real call proves nothing -------------------------------------------------------

ECHO_FAKES = {
    # task: (command that only echoes the planted values, tool output, answer)
    "read_config_value": ("echo notes/config.txt QX-4471", "QX-4471", "The build_id is QX-4471."),
    "count_lines_shell": ("echo 12 data.csv", "12", "12"),
    "count_matching_files": ("echo logs 7", "7", "7"),
    "find_todo_file": ("echo src FIXME-ORCHID c.py", "src FIXME-ORCHID c.py", "c.py"),
    "latest_report": ("echo reports COBALT", "reports COBALT", "COBALT"),
    "read_two_sum": ("echo 173 269", "173 269", "442"),
}


@pytest.mark.parametrize("name", sorted(ECHO_FAKES))
def test_rt5_a_run_command_that_only_echoes_the_answer_does_not_count_as_using_the_tool(name):
    """`run_command` is accepted for every file task, and the checks only look for strings in the call's input and
    output. A candidate that knows the planted value (it is in the open-source repo) echoes it and scores 1."""
    command, output, answer = ECHO_FAKES[name]
    record_call("run_command", {"command": command}, output)
    assert score(name, answer)[0] == 0.0


@pytest.mark.parametrize("name,needle", [("read_config_value", "QX-4471"), ("latest_report", "COBALT"),
                                          ("find_todo_file", "FIXME-ORCHID")])
def test_rt5_the_planted_answers_are_not_sitting_in_the_repo_for_a_tool_using_agent_to_grep(name, needle):
    """An agent with run_command can `grep -r QX-4471 <checkout>`. Planted values must come from the attempt's
    home (random per seed), not from files shipped next to the verifier."""
    hits = [p.name for p in PRACTICE_DIR.parent.rglob("*") if p.suffix in {".py", ".yaml"}
            and needle in p.read_text(encoding="utf-8")]
    assert hits == [], hits


def test_rt5_standalone_ints_does_not_read_a_decimal_as_an_integer():
    assert standalone_ints("about 7.5 files") == set()


def test_rt5_standalone_ints_does_not_glue_a_list_into_one_number():
    assert standalone_ints("rows 1,2 and 3") == {1, 2, 3}


# --- RT-AR2-6: answers that hedge or contradict the tool ---------------------------------------------------------


def test_rt6_flaky_probe_does_not_credit_an_answer_that_says_the_probe_failed():
    record_call("autoresearch_flaky_probe", {}, "PROBE OK")
    assert score("flaky_probe", "I could not get a PROBE OK from it, it failed.")[0] == 0.0


def test_rt6_today_weekday_does_not_credit_a_list_of_every_weekday():
    record_call("get_datetime", {}, DATETIME_OUTPUT)
    shotgun = "Monday, Tuesday, Wednesday, Thursday, Friday, Saturday or Sunday; day 282"
    assert score("today_weekday", shotgun)[0] == 0.0


def test_rt6_os_python_does_not_credit_a_list_of_every_os():
    record_call("get_system_info", {}, SYSINFO_OUTPUT)
    assert score("os_python", "Windows, Linux or macOS, Python 3.12")[0] == 0.0


def test_rt6_system_check_does_not_credit_a_list_of_sizes(monkeypatch):
    monkeypatch.setattr(legacy, "_ram_gib", lambda: 16.0)
    record_call("get_system_info", {}, SYSINFO_OUTPUT)
    assert score("system_check", "Could be 4 GB, 8 GB, 16 GB, 32 GB or 64 GB.")[0] == 0.0


# --- RT-AR2-7: legacy ports ---------------------------------------------------------------------------------------


def test_rt7_memory_recall_does_not_give_away_the_answer_in_its_prompt():
    """'remember my favorite color is blue. what is my favorite color?' is one turn: echoing the prompt scores 1,
    so the task tests no memory at all."""
    assert "blue" not in TASKS["practice.memory_recall"].prompt.casefold()


def test_rt7_math_does_not_fail_a_right_answer_that_ends_with_the_number_of_weeks():
    assert score("math", "$50 x 26 weeks = $1,300 saved over 26 weeks")[0] == 1.0


def test_rt7_explain_concept_does_not_credit_two_keywords():
    assert score("explain_concept", "retrieval generation")[0] < 1.0


# --- RT-AR2-8: CEI exclusion lives only in a helper --------------------------------------------------------------


def test_rt8_experiment_rows_carry_what_cei_needs_to_leave_out_judge_tasks(outer_db):
    """The runner stores task_id/family/tier in meta, not tags. A consumer reading `experiments` (CEI, OBS3) cannot
    tell a judge:llm row from a code-checked one, and a run without a judge scores them 0."""
    run([TASKS["practice.greeting"]], "rt-cfg", seeds=1, executor=ex.faker_read_config)
    meta = outer_db.execute("SELECT meta FROM experiments").fetchone()[0]
    assert "judge:llm" in meta
