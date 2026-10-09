"""AR1 exam: the task format. YAML fields: id, family, tier, prompt, setup (optional), verifier, timeout_s, tags."""
import pytest
from tanishi.arena import task as task_mod
from tanishi.arena.task import Task

REJECTED = (ValueError, TypeError)  # assumption A3: an invalid task file raises ValueError or TypeError

FULL_YAML = """\
id: greet-001
family: greeting
tier: practice
prompt: Say hello to the user.
setup: |
  echo prepare
verifier: some.module.check_greeting
timeout_s: 30
tags: [style, quick]
"""

MINIMAL_YAML = """\
id: min-001
family: math
tier: practice
prompt: What is 2+2?
verifier: some.module.check_math
timeout_s: 10
tags: []
"""


def _load(tmp_path, text, name="t.yaml"):
    p = tmp_path / name
    p.write_text(text)
    return task_mod.load_task(p)


def test_all_spec_fields_are_read(tmp_path):
    t = _load(tmp_path, FULL_YAML)
    assert isinstance(t, Task)
    assert t.id == "greet-001"
    assert t.family == "greeting"
    assert t.tier == "practice"
    assert t.prompt == "Say hello to the user."
    assert "echo prepare" in t.setup
    assert t.verifier == "some.module.check_greeting"
    assert t.timeout_s == 30
    assert list(t.tags) == ["style", "quick"]


def test_setup_is_optional(tmp_path):
    t = _load(tmp_path, MINIMAL_YAML)
    assert not t.setup
    assert list(t.tags) == []


@pytest.mark.parametrize("field", ["id", "family", "tier", "prompt", "verifier", "timeout_s"])
def test_missing_required_field_is_rejected(tmp_path, field):
    kept = [ln for ln in MINIMAL_YAML.splitlines() if not ln.startswith(field + ":")]
    with pytest.raises(REJECTED):
        _load(tmp_path, "\n".join(kept) + "\n")


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "null", ".inf", ".nan"])
def test_timeout_must_be_a_positive_finite_number(tmp_path, bad):
    text = MINIMAL_YAML.replace("timeout_s: 10", f"timeout_s: {bad}")
    with pytest.raises(REJECTED):
        _load(tmp_path, text)


@pytest.mark.parametrize("field", ["id", "prompt", "verifier"])
def test_blank_required_strings_are_rejected(tmp_path, field):
    lines = [f'{field}: ""' if ln.startswith(field + ":") else ln for ln in MINIMAL_YAML.splitlines()]
    with pytest.raises(REJECTED):
        _load(tmp_path, "\n".join(lines) + "\n")


def test_malformed_yaml_is_rejected(tmp_path):
    with pytest.raises(REJECTED):
        _load(tmp_path, "id: [unclosed\nfamily: x\n")


def test_non_mapping_yaml_is_rejected(tmp_path):
    with pytest.raises(REJECTED):
        _load(tmp_path, "- just\n- a list\n")


def test_yaml_loading_never_executes_python_tags(tmp_path):
    marker = tmp_path / "pwned"
    text = MINIMAL_YAML.replace(
        "prompt: What is 2+2?",
        f"prompt: !!python/object/apply:pathlib.Path.touch [!!python/object/apply:pathlib.Path [{marker}]]",
    )
    with pytest.raises(REJECTED):
        _load(tmp_path, text)
    assert not marker.exists()


def test_tags_must_be_a_list(tmp_path):
    with pytest.raises(REJECTED):
        _load(tmp_path, MINIMAL_YAML.replace("tags: []", "tags: {a: 1}"))


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        task_mod.load_task(tmp_path / "nope.yaml")


def test_task_is_constructible_and_keeps_fields():
    t = Task(id="x", family="f", tier="practice", prompt="p", verifier="a.b", timeout_s=3, tags=["t"])
    assert (t.id, t.family, t.tier, t.prompt, t.verifier, t.timeout_s) == ("x", "f", "practice", "p", "a.b", 3)
    assert list(t.tags) == ["t"]
