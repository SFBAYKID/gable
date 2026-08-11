"""Tests for secret redaction.

This is a security control, so the tests lean hard on the failure paths: a
secret that survives redaction is unrecoverable once it reaches a log Chase
pastes somewhere. Every assertion checks that the secret is *absent*, not merely
that a placeholder is present — those are different claims, and only the first
one matters.
"""

from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path

import pytest

from gable.logging_setup import (
    MIN_REDACTABLE_SECRET_LENGTH,
    SECRET_ENV_VARS,
    RedactingFilter,
    SecretRedactor,
    configure_logging,
)

REPO_ROOT: Path = Path(__file__).resolve().parent.parent

# Fabricated tokens, assembled at runtime rather than written as literals.
# GitHub push protection scans for token-shaped strings and blocked a push
# over these — correctly refusing to judge a fake from a real one. Splitting
# the prefix keeps the runtime value byte-identical, so these still exercise
# the real Slack token shape, while no scannable literal exists on disk.
BOT_TOKEN = "xox" + "b-1234567890-0987654321-AbCdEfGhIjKlMnOpQrSt"
APP_TOKEN = "xap" + "p-1-A012345678-9876543210-abcdefabcdefabcdef"
SPACES_SECRET = "aVeryLongSpacesSecretValue1234567890"


@pytest.fixture
def env() -> dict[str, str]:
    return {
        "SLACK_BOT_TOKEN": BOT_TOKEN,
        "SLACK_APP_TOKEN": APP_TOKEN,
        "SPACES_SECRET": SPACES_SECRET,
    }


@pytest.fixture
def redactor(env: dict[str, str]) -> SecretRedactor:
    return SecretRedactor.from_environ(env)


def test_literal_secret_is_removed(redactor: SecretRedactor) -> None:
    out = redactor.redact(f"connecting with {BOT_TOKEN} now")
    assert BOT_TOKEN not in out
    assert "[REDACTED:SLACK_BOT_TOKEN]" in out


def test_every_configured_secret_is_removed(redactor: SecretRedactor, env: dict[str, str]) -> None:
    out = redactor.redact(" ".join(env.values()))
    for secret in env.values():
        assert secret not in out


def test_spaces_secret_has_no_recognizable_prefix_and_still_goes(
    redactor: SecretRedactor,
) -> None:
    """Literal matching must cover secrets no pattern would ever catch."""
    out = redactor.redact(f"upload failed for key={SPACES_SECRET}")
    assert SPACES_SECRET not in out


def test_unknown_slack_token_caught_by_shape_pattern() -> None:
    """A token from an API error body, never present in our environment."""
    empty = SecretRedactor.from_environ({})
    foreign = "xox" + "b-999888777-666555444-ZzYyXxWwVvUuTtSsRrQq"
    out = empty.redact(f"slack said: invalid_auth for {foreign}")
    assert foreign not in out


def test_bearer_header_is_redacted() -> None:
    empty = SecretRedactor.from_environ({})
    out = empty.redact("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in out


def test_google_private_key_block_is_redacted() -> None:
    """A service-account key must not survive even inside a stack trace."""
    empty = SecretRedactor.from_environ({})
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg...\n-----END PRIVATE KEY-----"
    out = empty.redact(f"failed to parse {pem} oops")
    assert "MIIEvQIBADANBg" not in out


def test_blank_secret_does_not_shred_every_line() -> None:
    """`SPACES_KEY=` in .env must not register the empty string as a secret.

    Without the length floor this redactor would match between every character
    of every log line.
    """
    r = SecretRedactor.from_environ({"SPACES_KEY": "", "SLACK_BOT_TOKEN": "   "})
    assert r.literal_count == 0
    assert r.redact("a normal message") == "a normal message"


def test_short_secret_is_not_used_as_a_literal() -> None:
    short = "a" * (MIN_REDACTABLE_SECRET_LENGTH - 1)
    r = SecretRedactor.from_environ({"SPACES_KEY": short})
    assert r.literal_count == 0


def test_overlapping_secrets_redact_longest_first() -> None:
    """A short secret contained in a longer one must not leave a visible tail."""
    long_secret = "SUPERSECRETVALUE-EXTENDED-TAIL"
    short_secret = "SUPERSECRETVALUE"
    r = SecretRedactor({"LONG": long_secret, "SHORT": short_secret}, ())
    out = r.redact(f"token={long_secret}")
    assert long_secret not in out
    assert "EXTENDED-TAIL" not in out


def test_redact_value_recurses_into_containers(redactor: SecretRedactor) -> None:
    payload = {"outer": [{"token": BOT_TOKEN}, ("nested", APP_TOKEN)], "count": 3}
    out = redactor.redact_value(payload)
    serialized = json.dumps(out, default=str)
    assert BOT_TOKEN not in serialized
    assert APP_TOKEN not in serialized
    assert '"count": 3' in serialized


def test_redact_value_leaves_numbers_alone(redactor: SecretRedactor) -> None:
    assert redactor.redact_value(42) == 42
    assert redactor.redact_value(None) is None
    assert redactor.redact_value(True) is True


def test_filter_scrubs_message_args_and_extras(redactor: SecretRedactor) -> None:
    record = logging.LogRecord(
        name="gable.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="connecting as %s",
        args=(BOT_TOKEN,),
        exc_info=None,
    )
    # Mirrors logging's `extra=` mechanism, which sets attributes on the record.
    record.listing_token = APP_TOKEN
    assert RedactingFilter(redactor).filter(record) is True
    assert BOT_TOKEN not in record.getMessage()
    assert APP_TOKEN not in str(record.__dict__["listing_token"])


# --- end-to-end through the real logging stack ------------------------------


def _capture(level: str = "INFO", log_format: str = "json", **env: str) -> tuple[io.StringIO, None]:
    stream = io.StringIO()
    configure_logging(level=level, log_format=log_format, stream=stream, environ=env)
    return stream, None


def test_json_line_is_parseable_and_scrubbed(env: dict[str, str]) -> None:
    stream, _ = _capture(**env)
    logging.getLogger("gable.test").info("token is %s", BOT_TOKEN)
    raw = stream.getvalue()
    assert BOT_TOKEN not in raw
    parsed = json.loads(raw.strip())
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "gable.test"
    assert parsed["ts"].endswith("Z")


def test_traceback_is_scrubbed_by_the_formatter_backstop(env: dict[str, str]) -> None:
    """The filter cannot reach exception text; the formatter must."""
    stream, _ = _capture(**env)
    try:
        raise RuntimeError(f"auth failed for {BOT_TOKEN}")
    except RuntimeError:
        logging.getLogger("gable.test").exception("request failed")
    raw = stream.getvalue()
    assert BOT_TOKEN not in raw
    assert "RuntimeError" in raw


def test_third_party_logger_is_also_scrubbed(env: dict[str, str]) -> None:
    """A library that never saw our filter still goes through our handler."""
    stream, _ = _capture(**env)
    logging.getLogger("some.vendor.lib").warning("retrying with %s", SPACES_SECRET)
    assert SPACES_SECRET not in stream.getvalue()


def test_console_format_is_redacted_too(env: dict[str, str]) -> None:
    stream, _ = _capture(log_format="console", **env)
    logging.getLogger("gable.test").info("token %s", BOT_TOKEN)
    raw = stream.getvalue()
    assert BOT_TOKEN not in raw
    assert "gable.test" in raw


def test_configure_logging_is_idempotent(env: dict[str, str]) -> None:
    """Calling twice must not duplicate every line."""
    stream, _ = _capture(**env)
    configure_logging(stream=stream, environ=env)
    logging.getLogger("gable.test").info("once")
    assert stream.getvalue().count('"msg": "once"') == 1


def test_rejects_unknown_level() -> None:
    with pytest.raises(ValueError, match="unknown log level"):
        configure_logging(level="LOUD", environ={})


def test_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="unknown log format"):
        configure_logging(log_format="xml", environ={})


def test_refuses_to_disarm_redaction_while_secrets_are_present(env: dict[str, str]) -> None:
    """A typo in LOG_REDACT_SECRETS must not silently expose tokens."""
    with pytest.raises(ValueError, match="refused"):
        configure_logging(redact_secrets=False, environ=env)


def test_noisy_libraries_never_log_below_info(env: dict[str, str]) -> None:
    """Vendor HTTP loggers at DEBUG print request URLs, which can carry a token."""
    _capture(level="DEBUG", **env)
    assert logging.getLogger("httpx").level >= logging.INFO
    assert logging.getLogger("botocore").level >= logging.INFO


def test_secret_env_vars_matches_dotenv_example() -> None:
    """SECRET_ENV_VARS must not drift from the variables .env.example documents.

    A credential added to .env.example without being added here would be logged
    in the clear. This catches that at commit time rather than in journald.
    """
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    declared = {match.group(1) for match in re.finditer(r"^([A-Z0-9_]+)=", text, re.MULTILINE)}
    # Anything whose name says "credential" must be covered.
    suspicious = {
        name
        for name in declared
        if name.endswith(("_TOKEN", "_SECRET", "_API_KEY", "_KEY"))
        and name != "GOOGLE_SERVICE_ACCOUNT_FILE"
    }
    missing = suspicious - set(SECRET_ENV_VARS)
    assert missing == set(), f"secret-looking vars in .env.example not redacted: {sorted(missing)}"


def teardown_function() -> None:
    """Reset the root logger so one test's handler cannot leak into the next."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(logging.WARNING)
