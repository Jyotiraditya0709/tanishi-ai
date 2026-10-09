"""AR2 exam: the verifiers of the tasks that need no tool, and the judge rules of the style tasks."""
import pytest
from practice_helpers import plant, score

# name -> (answers that must score 1, answers that must score 0)
CASES = {
    "json_extract": (["12", "The value is 12.", "12\n"], ["40", "", "   ", "It is 1 or 2", "sustained"]),
    "unit_convert": (["42.2", "42.2 km", "About 42.16 km"], ["26.2", "42.8", "41.0", "forty-two", ""]),
    "date_arith": (["2024-02-29", "The date is 2024-02-29."], ["2024-03-01", "2024-02-28", "29 Feb 2024", "",
                                                              "2024-02-29 or 2024-03-01"]),
    "json_only_reply": (
        ['{"word": "verification", "vowels": 6}', '```json\n{"vowels": 6, "word": "verification"}\n```'],
        ['{"word": "verification", "vowels": 5}', '{"word": "verification", "vowels": "6"}',
         '{"word": "verification", "vowels": 6, "extra": 1}', 'vowels: 6', '{"word": "verification"}', '[6]',
         '{"word": "verification", "vowels": true}', '{"word": "verification", "vowels": 6.0}', ""],
    ),
    "bullet_count": (
        ["- fast feedback\n- safer changes\n- living documentation"],
        ["- one\n- two", "- one\n- two\n- three\n- four", "* a\n* b\n* c", "- one\n\n- two\n- three\n\n\n- four",
         "- " + "word " * 13 + "\n- b\n- c", "Here:\n- a\n- b\n- c", "-\n- b\n- c", ""],
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_right_answers_score_1(name):
    for text in CASES[name][0]:
        assert score(name, text)[0] == 1.0, (name, text)


@pytest.mark.parametrize("name", sorted(CASES))
def test_wrong_answers_score_0(name):
    for text in CASES[name][1]:
        assert score(name, text)[0] == 0.0, (name, text)


@pytest.mark.parametrize("name", sorted(CASES))
def test_none_and_non_string_output_score_0(name):
    for bad in (None, 12, [], b"12"):
        assert score(name, bad)[0] == 0.0


# --- tasks graded by running the candidate's code ---------------------------------------------------------------

SLUG_OK = '''```python
import re

def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
```'''
SLUG_NO_STRIP = "import re\ndef slugify(text):\n    return re.sub(r'[^a-z0-9]+', '-', text.lower())\n"
SLUG_NO_LOWER = "import re\ndef slugify(text):\n    return re.sub(r'[^A-Za-z0-9]+', '-', text).strip('-')\n"
SLUG_RAISES_ON_EMPTY = "def slugify(text):\n    return text.lower().split()[0]\n"

DEDUPE_OK = "def dedupe(items):\n    seen = set()\n    out = []\n    for x in items:\n        if x not in seen:\n            seen.add(x)\n            out.append(x)\n    return out\n"
DEDUPE_SORTS = "def dedupe(items):\n    return sorted(set(items))\n"
DEDUPE_MUTATES = "def dedupe(items):\n    seen = []\n    for x in list(items):\n        if x in seen:\n            items.remove(x)\n        else:\n            seen.append(x)\n    return items\n"
DEDUPE_QUADRATIC = ("import time\ndef dedupe(items):\n    out = []\n    for x in items:\n        time.sleep(0.01)\n"
                    "        if x not in out:\n            out.append(x)\n    return out\n")

AVG_OK = "def moving_avg(xs, k):\n    return [sum(xs[i:i + k]) / k for i in range(len(xs) - k + 1)]\n"
AVG_BUGGY = "def moving_avg(xs, k):\n    out = []\n    for i in range(len(xs) - k):\n        out.append(sum(xs[i:i + k]) / k)\n    return out\n"
AVG_OVERSHOOT = "def moving_avg(xs, k):\n    return [sum(xs[i:i + k]) / k for i in range(len(xs))]\n"

CODE_CASES = {
    "slugify_fn": ([SLUG_OK], [SLUG_NO_STRIP, SLUG_NO_LOWER, SLUG_RAISES_ON_EMPTY, "def slugify(text): return text",
                               "I would use a regex.", "def slugify(:", ""]),
    "dedupe_fn": ([DEDUPE_OK, "```python\n" + DEDUPE_OK + "```\nThat keeps order."],
                  [DEDUPE_SORTS, DEDUPE_MUTATES, DEDUPE_QUADRATIC, "def other(items): return items", ""]),
    "fix_bug_fn": ([AVG_OK], [AVG_BUGGY, AVG_OVERSHOOT, "def moving_avg(xs, k): return []", "no idea"]),
}


@pytest.mark.parametrize("name", sorted(CODE_CASES))
def test_working_code_scores_1(name):
    for code in CODE_CASES[name][0]:
        assert score(name, code)[0] == 1.0, name


@pytest.mark.parametrize("name", sorted(CODE_CASES))
def test_broken_missing_or_slow_code_scores_0(name):
    for code in CODE_CASES[name][1]:
        assert score(name, code)[0] == 0.0, (name, code[:40])


def test_code_that_hangs_scores_0_instead_of_hanging():
    assert score("slugify_fn", "while True:\n    pass\n")[0] == 0.0


def test_code_that_exits_zero_early_does_not_pass(tmp_path):
    """sys.exit(0) before the asserts must not read as success."""
    value, _ = score("slugify_fn", "import sys\nsys.exit(0)\n")
    assert value == 0.0


def test_code_that_writes_files_does_not_touch_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    score("slugify_fn", "open('leak.txt', 'w').write('x')\n" + SLUG_OK)
    assert not (tmp_path / "leak.txt").exists()


def test_fenced_block_is_used_when_there_is_prose_around_it():
    assert score("slugify_fn", "Sure:\n\n" + SLUG_OK + "\n\nHope that helps!")[0] == 1.0


# --- SQL --------------------------------------------------------------------------------------------------------

SQL_OK = "SELECT customer, SUM(amount) AS total FROM orders WHERE status = 'paid' GROUP BY customer HAVING total > 100 ORDER BY total DESC"


def test_sql_right_query_scores_1():
    for sql in (SQL_OK, SQL_OK + ";", f"```sql\n{SQL_OK};\n```", SQL_OK.replace("HAVING total", "HAVING SUM(amount)")):
        assert score("sql_query", sql)[0] == 1.0, sql


@pytest.mark.parametrize("sql", [
    SQL_OK.replace("DESC", "ASC"),
    SQL_OK.replace("'paid'", "'pending'"),
    SQL_OK.replace("> 100", ">= 100"),
    SQL_OK.replace("WHERE status = 'paid' ", ""),
    SQL_OK.replace(" HAVING total > 100", ""),
    "SELECT customer FROM orders",
    "SELEKT nonsense",
    "",
    "DROP TABLE orders",
    SQL_OK + "; DELETE FROM orders",
    "DELETE FROM orders",
    "INSERT INTO orders VALUES (99, 'x', 1, 'paid')",
    "PRAGMA table_info(orders)",
    "ATTACH DATABASE 'x.db' AS x",
    "SELECT load_extension('x')",
])
def test_sql_wrong_or_dangerous_queries_score_0(sql):
    assert score("sql_query", sql)[0] == 0.0


def test_sql_write_attempts_leave_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    score("sql_query", "ATTACH DATABASE 'leak.db' AS x")
    assert list(tmp_path.iterdir()) == []


# --- legacy tasks that code can check ---------------------------------------------------------------------------


def test_explain_concept_checks_retrieval_generation_and_length():
    good = "RAG retrieves relevant documents first, then a model generates an answer grounded in them."
    assert score("explain_concept", good)[0] == 1.0
    assert 0.0 < score("explain_concept", "RAG retrieves documents.")[0] < 1.0
    assert score("explain_concept", "It is a thing.")[0] == 0.0
    assert score("explain_concept", good + " word" * 60)[0] < 1.0
    assert score("explain_concept", "")[0] == 0.0


def test_memory_recall_wants_blue(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plant(tmp_path)  # setup plants a random color; this one is blue
    assert score("memory_recall", "Your favorite color is blue.")[0] == 1.0
    assert score("memory_recall", "BLUE")[0] == 1.0
    assert score("memory_recall", "Your favorite color is red.")[0] == 0.0
    assert score("memory_recall", "")[0] == 0.0


@pytest.mark.parametrize("text,ok", [
    ("$50 x 26 weeks = $1,300", True), ("About $1300.", True), ("1200", True), ("$1,350 at most", True),
    ("$50 * 26 = 13000", False), ("300", False), ("I saved nothing", False), ("", False), ("$50 per week", False),
])
def test_math_accepts_the_range_around_1300(text, ok):
    assert (score("math", text)[0] == 1.0) is ok


# --- style tasks: code floor, judge on top ------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["greeting", "personality", "poem"])
def test_style_tasks_score_0_with_no_judge_configured(name, monkeypatch):
    monkeypatch.delenv("TANISHI_PRACTICE_JUDGE", raising=False)
    text = "Hey! Doing well.\nHow about the coffee on your side?" if name != "personality" else "That sounds draining.\nRest."
    assert score(name, text)[0] == 0.0


@pytest.mark.parametrize("name", ["greeting", "personality", "poem"])
def test_style_tasks_score_0_on_blank_without_calling_the_judge(name, monkeypatch):
    monkeypatch.setenv("TANISHI_PRACTICE_JUDGE", "practice_judges:explode")
    assert score(name, "")[0] == 0.0
    assert score(name, "   ")[0] == 0.0
    assert score(name, None)[0] == 0.0


@pytest.fixture
def judges(tmp_path, monkeypatch):
    (tmp_path / "practice_judges.py").write_text(
        "def generous(prompt, criteria, output):\n    return 0.8\n"
        "def over(prompt, criteria, output):\n    return 7\n"
        "def nan(prompt, criteria, output):\n    return float('nan')\n"
        "def text(prompt, criteria, output):\n    return 'great'\n"
        "def explode(prompt, criteria, output):\n    raise RuntimeError('x')\n"
        "def seen(prompt, criteria, output):\n    import json, os\n"
        "    open(os.environ['JUDGE_LOG'], 'w').write(json.dumps([prompt, criteria, output]))\n    return 1.0\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("JUDGE_LOG", str(tmp_path / "log.json"))
    return tmp_path


def test_judge_score_is_used_when_the_floor_passes(judges, monkeypatch):
    monkeypatch.setenv("TANISHI_PRACTICE_JUDGE", "practice_judges:generous")
    assert score("greeting", "Hey, I'm good. You?")[0] == pytest.approx(0.8)
    assert score("personality", "That sounds exhausting. Go rest.")[0] == pytest.approx(0.8)
    assert score("poem", "Steam curls up\nfrom the coffee cup")[0] == pytest.approx(0.8)


@pytest.mark.parametrize("bad", ["over", "nan", "text", "explode", "missing_function"])
def test_a_judge_that_fails_or_lies_gives_no_credit_beyond_range(judges, monkeypatch, bad):
    monkeypatch.setenv("TANISHI_PRACTICE_JUDGE", f"practice_judges:{bad}")
    value, _ = score("greeting", "Hey, I'm good. You?")
    assert 0.0 <= value <= 1.0
    if bad != "over":
        assert value == 0.0


def test_judge_gets_the_prompt_the_criteria_and_the_answer(judges, monkeypatch):
    import json
    monkeypatch.setenv("TANISHI_PRACTICE_JUDGE", "practice_judges:seen")
    score("greeting", "Hey there.")
    prompt, criteria, output = json.loads((judges / "log.json").read_text())
    assert "how's it going" in prompt
    assert output == "Hey there."
    assert criteria and all(isinstance(c, str) for c in criteria)


def test_poem_floor_rejects_prose_and_off_topic_before_any_judge(judges, monkeypatch):
    monkeypatch.setenv("TANISHI_PRACTICE_JUDGE", "practice_judges:generous")
    assert score("poem", "Coffee is a drink.")[0] == 0.0  # one line is not a poem
    assert score("poem", "Roses are red\nViolets are blue")[0] == 0.0  # not about coffee
    assert score("poem", "Dark roast at dawn\nCoffee wakes the quiet hours")[0] == pytest.approx(0.8)


def test_very_long_style_answers_are_not_judged(judges, monkeypatch):
    monkeypatch.setenv("TANISHI_PRACTICE_JUDGE", "practice_judges:generous")
    assert score("greeting", "hello " * 1000)[0] == 0.0
