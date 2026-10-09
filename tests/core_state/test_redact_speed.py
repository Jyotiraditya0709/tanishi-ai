"""redact() stays linear on hostile text (red-team SUB1 R1), and still hides the secrets inside it."""
import time

import pytest

from tanishi.core_state.events import redact

KEY = "sk-ant-api03-" + "A" * 40
SIZE = 320_000  # 320 kB: over two minutes before the fix


@pytest.mark.parametrize("word", ["password", "secret_", "token", "api-key", "Credential", "private_key "])
def test_repeated_secret_words_redact_quickly(word):
    text = word * (SIZE // len(word))
    t0 = time.perf_counter()
    redact(text)
    assert time.perf_counter() - t0 < 0.5, word


def test_adversarial_text_still_hides_real_secrets():
    run = "password" * (SIZE // 16)
    text = f"{run} password=hunter2xyz {run} use {KEY} {run}token=987654321"
    t0 = time.perf_counter()
    out = redact(text)
    assert time.perf_counter() - t0 < 0.5
    assert "hunter2xyz" not in out and KEY not in out and "987654321" not in out
    assert "password=[REDACTED]" in out


@pytest.mark.parametrize("text, expected", [
    ("AWS_SECRET_ACCESS_KEY=abc123", "AWS_SECRET_ACCESS_KEY=[REDACTED]"),
    ("config.password = 'pw1'", "config.password = '[REDACTED]'"),
    ("my-api_key: v", "my-api_key: [REDACTED]"),
    ('{"passwordHash": "h"}', '{"passwordHash": "[REDACTED]"}'),
    ("xpassword=a password", "xpassword=[REDACTED] password"),
    ("tokens are counted", "tokens are counted"),
])
def test_names_are_still_kept_and_values_hidden(text, expected):
    assert redact(text) == expected
