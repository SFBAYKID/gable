"""Structured JSON logging with mandatory secret redaction.

CLAUDE.md section 3 requires redaction to be a *mechanism*, not a habit: no call
site should have to remember to scrub a token. So redaction happens twice, at
two different layers, on purpose.

1. `RedactingFilter` scrubs `record.msg`, `record.args`, and any `extra=` fields
   before a handler sees them. This is the layer that keeps structured fields
   clean, so a redacted value stays redacted in JSON output.
2. `JsonFormatter` scrubs the fully serialized line as a last backstop. It
   catches what the filter structurally cannot: exception tracebacks, and
   records emitted by third-party libraries that never pass through our filter.

Belt and braces is deliberate. A single missed token in a log Chase pastes into
Slack is unrecoverable — the token has to be rotated.

Two kinds of secret are recognized:

* **Literal values** read from the environment at configure time. This is the
  strong one: it needs no pattern to be correct, only that the variable is
  listed in `SECRET_ENV_VARS`.
* **Shape patterns** for tokens that may arrive from somewhere other than our
  own environment — a Slack token echoed in an API error body, a Bearer header
  in an httpx debug log, a PEM block in a stack trace.

Assumes: the process environment holds the real secret values by the time
`configure_logging` runs. A secret that enters the process later (rotated
mid-run, fetched from an API) is covered by the shape patterns but not by
literal matching. Re-call `configure_logging` after a rotation.

Does not handle: log shipping, rotation, or sampling — journald owns those. Nor
does it scrub anything already written; redaction is applied on the way out.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections.abc import Iterable, Mapping
from typing import IO, Any, Final

#: Environment variables whose values are secrets. Mirrors `.env.example`.
#: Adding a secret-bearing variable to config without adding it here is the
#: mistake this module exists to prevent, so `tests/test_logging_setup.py`
#: cross-checks this tuple against `.env.example` and fails on drift.
SECRET_ENV_VARS: Final[tuple[str, ...]] = (
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_SIGNING_SECRET",
    "SLACK_CLIENT_SECRET",
    "FIRECRAWL_API_KEY",
    "OPENAI_IMAGE_API_KEY",
    "ANTHROPIC_API_KEY",
    "SPACES_KEY",
    "SPACES_SECRET",
)

#: Literal values shorter than this are not redacted. Without this floor an
#: unset-but-present variable (`SPACES_KEY=`) would register the empty string as
#: a secret, and every character of every log line would match it. A short
#: value is also far more likely to be a common substring than a real
#: credential.
MIN_REDACTABLE_SECRET_LENGTH: Final[int] = 8

#: Shape-based patterns, applied after literal matching.
#:
#: `xoxb-`/`xapp-` are verified: `.env.example` documents both as the Slack bot
#: and app-level token prefixes. The PEM block is the format of the
#: `private_key` field inside a Google service-account JSON, which CLAUDE.md
#: section 3 says must never be printed even in a stack trace.
#:
#: ASSUMPTION: `sk-` for OpenAI/Anthropic and `xoxp-`/`xoxa-`/`xoxr-` for other Slack
#: token classes. These are widely repeated prefixes, but I have NOT read them
#: in vendor documentation and have not observed them here. Would be settled by
#: reading Slack's token-types page and OpenAI's API-key docs.
#:
#: The failure mode is asymmetric, which is why an unconfirmed pattern is
#: acceptable: a wrong pattern over-redacts something harmless. It cannot cause
#: a leak. Literal-value matching is what actually protects our own tokens.
REDACTION_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "google-private-key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    ),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{8,}")),
    ("slack-app-token", re.compile(r"xapp-[A-Za-z0-9-]{8,}")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    # Matches the credential in an Authorization header without matching the
    # word "bearer" in prose, because the token part is required.
    ("bearer-token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")),
)

#: Attributes every LogRecord carries. Anything outside this set arrived via
#: `extra=` and is treated as a structured field.
_STANDARD_RECORD_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "taskName", "thread", "threadName",
    }
)  # fmt: skip

_MAX_EXTRA_REPR_CHARS: Final[int] = 2000


class SecretRedactor:
    """Replaces known secrets in text with a labelled placeholder.

    The placeholder names *which* secret was removed (`[REDACTED:SPACES_SECRET]`)
    because that is diagnostically useful and discloses nothing — the variable
    name is already public in `.env.example`.
    """

    def __init__(
        self, literals: Mapping[str, str], patterns: Iterable[tuple[str, re.Pattern[str]]]
    ) -> None:
        """Build a redactor.

        Args:
            literals: Mapping of label to secret value. Values shorter than
                `MIN_REDACTABLE_SECRET_LENGTH` are dropped, not stored.
            patterns: Pairs of label and compiled regex.
        """
        # Longest first: if one secret is a substring of another, the longer
        # must be replaced first or the shorter leaves a recognizable tail.
        self._literals: tuple[tuple[str, str], ...] = tuple(
            sorted(
                (
                    (label, value)
                    for label, value in literals.items()
                    if len(value) >= MIN_REDACTABLE_SECRET_LENGTH
                ),
                key=lambda item: len(item[1]),
                reverse=True,
            )
        )
        self._patterns: tuple[tuple[str, re.Pattern[str]], ...] = tuple(patterns)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> SecretRedactor:
        """Build a redactor from the secret variables present in the environment.

        Args:
            environ: Environment mapping. Defaults to `os.environ`.

        Returns:
            A redactor covering every populated variable in `SECRET_ENV_VARS`
            plus the shape patterns.
        """
        source = os.environ if environ is None else environ
        literals = {name: source[name] for name in SECRET_ENV_VARS if source.get(name)}
        return cls(literals, REDACTION_PATTERNS)

    @property
    def literal_count(self) -> int:
        """How many literal secrets are being tracked. For diagnostics only."""
        return len(self._literals)

    def redact(self, text: str) -> str:
        """Return `text` with every known secret replaced by a placeholder."""
        for label, value in self._literals:
            if value in text:
                text = text.replace(value, f"[REDACTED:{label}]")
        for label, pattern in self._patterns:
            text = pattern.sub(f"[REDACTED:{label}]", text)
        return text

    def redact_value(self, value: object) -> object:
        """Redact a value of any type, recursing into containers.

        Non-string scalars (int, float, bool, None) are returned untouched: they
        cannot carry a token, and stringifying them would corrupt JSON output.
        """
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, Mapping):
            return {key: self.redact_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            redacted = [self.redact_value(item) for item in value]
            return tuple(redacted) if isinstance(value, tuple) else redacted
        if isinstance(value, (int, float, bool, type(None))):
            return value
        # Unknown object: its repr may embed a token (a client holding a
        # config, an exception carrying a request URL), so scrub the repr.
        return self.redact(repr(value)[:_MAX_EXTRA_REPR_CHARS])


class RedactingFilter(logging.Filter):
    """Scrubs a record's message, args, and extra fields in place."""

    def __init__(self, redactor: SecretRedactor) -> None:
        """Install a redactor on this filter."""
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the record in place. Always returns True — nothing is dropped."""
        if isinstance(record.msg, str):
            record.msg = self._redactor.redact(record.msg)
        if record.args:
            # A dict here means %(name)s-style formatting; a tuple means %s.
            record.args = self._redactor.redact_value(record.args)  # type: ignore[assignment]
        for key, value in list(record.__dict__.items()):
            if key not in _STANDARD_RECORD_FIELDS:
                record.__dict__[key] = self._redactor.redact_value(value)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for journald.

    Redacts the serialized line as a backstop for records that never passed
    through `RedactingFilter` — notably tracebacks and third-party loggers.
    """

    def __init__(self, redactor: SecretRedactor) -> None:
        """Install a redactor on this formatter."""
        super().__init__()
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        """Serialize the record to a single-line JSON object."""
        payload: dict[str, Any] = {
            # UTC always. ARCHITECTURE.md 3.3 stores ISO 8601 UTC in `Runs`, and
            # a log in droplet-local time cannot be lined up against it.
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # default=str so an un-serializable extra degrades to its repr instead
        # of raising inside the logging machinery, where the error is invisible.
        line = json.dumps(payload, default=str, ensure_ascii=False)
        return self._redactor.redact(line)


class ConsoleFormatter(logging.Formatter):
    """Human-readable local format. Redacted exactly as strictly as JSON."""

    def __init__(self, redactor: SecretRedactor) -> None:
        """Install a redactor on this formatter."""
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        """Format the record and scrub the result."""
        return self._redactor.redact(super().format(record))


def configure_logging(
    level: str = "INFO",
    log_format: str = "json",
    redact_secrets: bool = True,
    stream: IO[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> SecretRedactor:
    """Install Gable's logging configuration on the root logger.

    Idempotent: existing handlers on the root logger are removed first, so
    calling this twice does not double every line.

    Args:
        level: One of DEBUG, INFO, WARNING, ERROR. Case-insensitive.
        log_format: `json` for journald, `console` for local work.
        redact_secrets: Must stay True in production. False is for testing the
            redactor itself, and is refused when real secrets are present.
        stream: Destination. Defaults to stderr.
        environ: Environment to read secrets from. Defaults to `os.environ`.

    Returns:
        The redactor that was installed, so callers can scrub strings headed
        somewhere other than a log — a Slack message, for instance.

    Raises:
        ValueError: if `level` is not a known level name, if `log_format` is
            not `json` or `console`, or if `redact_secrets` is False while
            real secrets are present in the environment.
    """
    normalized = level.upper()
    if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"unknown log level: {level!r}")
    if log_format not in {"json", "console"}:
        raise ValueError(f"unknown log format: {log_format!r} (expected json or console)")

    redactor = SecretRedactor.from_environ(environ)
    if not redact_secrets:
        # Refusing here rather than trusting the flag: LOG_REDACT_SECRETS is a
        # config value, and a typo in .env must not silently disarm the one
        # mechanism standing between a token and journald.
        if redactor.literal_count:
            raise ValueError(
                "redact_secrets=False refused: "
                f"{redactor.literal_count} secret(s) are set in the environment"
            )
        redactor = SecretRedactor({}, ())

    handler = logging.StreamHandler(sys.stderr if stream is None else stream)
    handler.setFormatter(
        JsonFormatter(redactor) if log_format == "json" else ConsoleFormatter(redactor)
    )
    handler.addFilter(RedactingFilter(redactor))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(normalized)

    # These libraries log request URLs and headers at DEBUG. Even with
    # redaction in place, a query-string token is not worth the risk.
    for noisy in ("httpx", "httpcore", "urllib3", "botocore", "boto3", "slack_bolt", "slack_sdk"):
        logging.getLogger(noisy).setLevel(max(logging.INFO, root.level))

    return redactor
