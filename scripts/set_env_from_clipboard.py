"""Write the macOS clipboard into a single `.env` key, without ever printing it.

Why this exists
---------------
CLAUDE.md §3 says the building agent consumes secrets from `.env` and never
acquires them, and that no secret may ever be logged. Reading a live token into
an agent's context would put it in a transcript, which is exactly the leak that
rule prevents.

This closes the gap: the browser's "Copy" button puts the value on the
clipboard, and this script moves it into `.env`. The value is never printed,
never echoed, and never passes through anything that records it. The only
output is the key name and the character count, so a mistake is still visible.

Usage
-----
    # click Copy in the Slack UI, then:
    .venv/bin/python scripts/set_env_from_clipboard.py SLACK_BOT_TOKEN

    # sanity-check what is set without revealing values:
    .venv/bin/python scripts/set_env_from_clipboard.py --status

Assumes: macOS (`pbpaste`), and that `.env` already exists with the key present
(blank or not). It rewrites in place, preserving comments, order, and every
other value.

Does not handle: creating `.env`, or adding a key that is not already there.
Both are deliberate — `.env.example` is the list of keys that exist, and
silently inventing one would hide a typo.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ENV_PATH: Path = Path(__file__).resolve().parent.parent / ".env"

#: Values shorter than this are almost certainly a mis-copy — an empty
#: clipboard, or a stray click that copied a label instead of a token.
MIN_PLAUSIBLE_LENGTH: int = 8

#: Prefixes worth confirming out loud. Knowing a value *starts* with `xoxb-`
#: leaks nothing — it is the documented public format marker — and it catches
#: the common error of pasting the wrong token into the wrong key.
KNOWN_PREFIXES: dict[str, tuple[str, ...]] = {
    "SLACK_BOT_TOKEN": ("xoxb-",),
    "SLACK_APP_TOKEN": ("xapp-",),
}


def read_clipboard() -> str:
    """Return the clipboard contents, stripped.

    Raises:
        RuntimeError: if `pbpaste` is unavailable or fails.
    """
    try:
        done = subprocess.run(["pbpaste"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"could not read the clipboard: {exc}") from exc
    return done.stdout.strip()


def set_key(env_path: Path, key: str, value: str) -> None:
    """Replace one key's value in `.env`, in place.

    Raises:
        KeyError: if the key is not already present in the file.
    """
    lines = env_path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^{re.escape(key)}=")
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = f"{key}={value}"
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            env_path.chmod(0o600)
            return
    raise KeyError(key)


def status(env_path: Path) -> int:
    """Print which keys are set, by length only. Never prints a value."""
    print(f"{env_path}:")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        mark = "set" if value.strip() else "EMPTY"
        detail = f"{len(value.strip())} chars" if value.strip() else "-"
        print(f"  {key.strip():<36} {mark:<6} {detail}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Move the clipboard into one `.env` key.

    Returns:
        0 on success, 1 on any refusal — empty clipboard, implausibly short
        value, unknown key, or a value that does not match the key's expected
        prefix.
    """
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument("key", nargs="?", help="the .env key to set, e.g. SLACK_BOT_TOKEN")
    parser.add_argument("--status", action="store_true", help="show which keys are set")
    args = parser.parse_args(argv)

    if not ENV_PATH.exists():
        print(f"error: {ENV_PATH} does not exist", file=sys.stderr)
        return 1
    if args.status:
        return status(ENV_PATH)
    if not args.key:
        parser.error("give a key, or --status")

    try:
        value = read_clipboard()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not value:
        print("error: clipboard is empty — click Copy first", file=sys.stderr)
        return 1
    if len(value) < MIN_PLAUSIBLE_LENGTH:
        # Deliberately does not echo the value; a short mis-copy is often a
        # label or a stray character, and printing it helps nobody.
        print(
            f"error: clipboard holds only {len(value)} characters, "
            "which does not look like a credential. Nothing written.",
            file=sys.stderr,
        )
        return 1
    if "\n" in value:
        print("error: clipboard holds multiple lines. Nothing written.", file=sys.stderr)
        return 1

    expected = KNOWN_PREFIXES.get(args.key)
    if expected and not value.startswith(expected):
        print(
            f"error: {args.key} should start with {' or '.join(expected)} — "
            "the clipboard holds something else. Nothing written.",
            file=sys.stderr,
        )
        return 1

    try:
        set_key(ENV_PATH, args.key, value)
    except KeyError:
        print(
            f"error: {args.key} is not a key in .env. "
            "Add it to .env.example first so the two stay in step.",
            file=sys.stderr,
        )
        return 1

    print(f"{args.key} set — {len(value)} characters. Value not shown, by design.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
