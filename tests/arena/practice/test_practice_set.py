"""AR2 exam: the shape of the Practice tier, from the spec (build/graph.yaml node AR2).

Spec: 9 legacy tasks ported; style tasks keep an LLM judge, tagged judge:llm and out of CEI; 20 new tasks with code
verifiers and no personal text; at least 8 of the 20 need a tool.
"""
import re

import pytest

from tanishi.arena.practice import PRACTICE_DIR, cei_eligible, load_practice, needs_tool
from tanishi.arena.runner import task_set_id
from tanishi.arena.task import Task
from tanishi.arena.verifiers import resolve

LEGACY = ["greeting", "explain_concept", "get_time", "system_check", "flaky_probe", "memory_recall", "math",
          "personality", "poem"]
STYLE = {"greeting", "personality", "poem"}


@pytest.fixture(scope="module")
def tasks():
    return load_practice()


@pytest.fixture(scope="module")
def by_id(tasks):
    return {t.id: t for t in tasks}


def short(task):
    return task.id.removeprefix("practice.")


def test_there_are_29_tasks(tasks):
    assert len(tasks) == 29
    assert len(list(PRACTICE_DIR.glob("*.yaml"))) == 29


def test_all_are_tasks_in_the_practice_tier_with_unique_ids(tasks):
    assert all(isinstance(t, Task) for t in tasks)
    assert {t.tier for t in tasks} == {"practice"}
    assert len({t.id for t in tasks}) == 29
    assert [t.id for t in tasks] == sorted(t.id for t in tasks)


def test_each_file_is_named_after_its_task(tasks):
    assert {p.stem for p in PRACTICE_DIR.glob("*.yaml")} == {short(t) for t in tasks}


def test_the_nine_legacy_tasks_are_ported(by_id):
    for name in LEGACY:
        assert f"practice.{name}" in by_id, name
        assert "legacy" in by_id[f"practice.{name}"].tags


def test_exactly_the_nine_legacy_tasks_are_tagged_legacy(tasks):
    assert {short(t) for t in tasks if "legacy" in t.tags} == set(LEGACY)


def test_legacy_prompts_are_kept_word_for_word(by_id):
    assert by_id["practice.greeting"].prompt.strip() == "hey tanishi how's it going"
    assert by_id["practice.explain_concept"].prompt.strip() == "explain RAG in two sentences"
    assert by_id["practice.get_time"].prompt.strip() == "what time is it right now?"
    assert by_id["practice.system_check"].prompt.strip() == "how much RAM do I have?"
    assert by_id["practice.flaky_probe"].prompt.strip() == (
        "Run the autoresearch_flaky_probe tool and tell me its result.")
    assert by_id["practice.math"].prompt.strip() == "if I save $50/week, how much in 6 months?"
    assert by_id["practice.poem"].prompt.strip() == "write me a quick poem about coffee"


def test_style_tasks_are_tagged_judge_llm_and_only_they(tasks):
    assert {short(t) for t in tasks if "judge:llm" in t.tags} == STYLE


def test_style_tasks_are_excluded_from_cei_and_the_rest_are_not(tasks):
    assert {short(t) for t in tasks if not cei_eligible(t)} == STYLE
    assert sum(cei_eligible(t) for t in tasks) == 26


def test_every_verifier_resolves_to_a_callable(tasks):
    for t in tasks:
        assert callable(resolve(t.verifier)), t.id


def test_new_tasks_are_20_and_have_no_judge(tasks):
    new = [t for t in tasks if "legacy" not in t.tags]
    assert len(new) == 20
    assert all("judge:llm" not in t.tags for t in new)
    assert all(cei_eligible(t) for t in new)


def test_at_least_8_of_the_20_new_tasks_need_a_tool(tasks):
    new_tool = [t for t in tasks if "legacy" not in t.tags and needs_tool(t)]
    assert len(new_tool) >= 8


def test_tool_tasks_name_the_tool_they_need(tasks):
    for t in tasks:
        if needs_tool(t):
            assert any(tag.startswith("tool:") for tag in t.tags), t.id


def test_the_three_legacy_tool_tasks_still_need_a_tool(by_id):
    for name in ("get_time", "system_check", "flaky_probe"):
        assert needs_tool(by_id[f"practice.{name}"]), name


def test_every_task_has_a_sane_timeout(tasks):
    assert all(0 < t.timeout_s <= 600 for t in tasks)


def test_tool_tasks_allow_time_for_a_model_to_call_tools(tasks):
    assert all(t.timeout_s >= 30 for t in tasks if needs_tool(t))


def test_task_set_id_is_stable_across_loads():
    assert task_set_id(load_practice()) == task_set_id(load_practice())


def test_briefs_do_not_show_the_verifier_or_the_setup(tasks):
    for t in tasks:
        brief = t.brief()
        assert not hasattr(brief, "verifier") and not hasattr(brief, "setup")


# --- no answers or personal text in the prompts ----------------------------------------------------------------

PLANTED = ["QX-4471", "COBALT", "AMBER", "JADE", "ready-42", "442", "173", "269", "2024-02-29", "42.2"]
PERSONAL = [r"@\w+\.", r"jmishra", r"aniket", r"jyoti", r"/Users/", r"\bsk-[A-Za-z0-9]{8,}"]


def test_prompts_do_not_contain_the_answers(tasks):
    for t in tasks:
        for secret in PLANTED:
            if t.id in ("practice.write_exact_file",) and secret == "ready-42":
                continue  # the file content is part of the instruction there
            assert secret not in t.prompt, (t.id, secret)


def test_no_personal_text_in_tasks(tasks):
    for t in tasks:
        blob = " ".join([t.prompt, t.setup or "", *t.tags])
        for pattern in PERSONAL:
            assert not re.search(pattern, blob, re.IGNORECASE), (t.id, pattern)


def test_setup_scripts_stay_inside_the_attempt_home(tasks):
    for t in tasks:
        if t.setup:
            assert "~" not in t.setup and "$HOME" not in t.setup
            assert not re.search(r"(^|\s)/(?!dev/null)\w", t.setup), t.id  # no absolute paths
            assert "sudo" not in t.setup and "rm -rf" not in t.setup and "curl" not in t.setup


# --- the planted values in the setup scripts and the verifiers must agree ----------------------------------------


@pytest.mark.parametrize("name,token", [
    ("read_config_value", "QX-4471"),
    ("find_todo_file", "FIXME-ORCHID"),
    ("latest_report", "COBALT"),
    ("read_two_sum", "173"),
    ("read_two_sum", "269"),
])
def test_setup_plants_the_value_the_verifier_looks_for(by_id, name, token):
    assert token in by_id[f"practice.{name}"].setup
