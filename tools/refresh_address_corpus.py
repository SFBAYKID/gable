"""Rebuild the real-address replay corpus from the live form, read-only.

Every address defect Carmen has met was a real input the suite never saw: a
five-digit house number, a state written out, a trailing country, a condo with
a unit, two listings in one field. This tool reads every address the form has
ever received and writes what Gable currently makes of each one — the tidied
text and the verdict — into `tests/fixtures/address_corpus.tsv`.
`tests/test_address_corpus.py` then holds the code to that file.

The workflow is: run this, read the git diff, commit it. A verdict that changes
is either a fix or a regression, and the diff is where a person decides which.
New submissions appear as new rows.

Reads the response tab through the service account and never writes to it.
Street addresses of listed properties are public; nothing else from the row is
kept. Does not handle: Testing tabs, which hold invented rows.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Final

from gable.config import ConfigError, Settings
from gable.google_client import build_google_service
from gable.listings.address import incomplete_address
from gable.sheets import repository as repo
from gable.sheets.client import SheetClient
from gable.slides.manifest import ADDRESS_SHAPE, names_one_property, normalise_address

CORPUS_PATH: Final[Path] = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
CORPUS_FILE: Final[Path] = CORPUS_PATH / "address_corpus.tsv"
READ_ONLY_SCOPE: Final[str] = "https://www.googleapis.com/auth/spreadsheets.readonly"
HEADER: Final[str] = "raw\ttidied\tverdict"


def verdict_for(raw: str) -> tuple[str, str]:
    """What Gable makes of one address as typed.

    Args:
        raw: The address column, exactly as submitted.

    Returns:
        The tidied text and one of `empty`, `multiple`, `whole`, or
        `incomplete: <what is missing>` in the words the ask would use.

    Raises:
        Nothing.
    """
    # The runner's own reader, so the corpus records what a listing gets.
    tidied = normalise_address(raw)
    if not tidied:
        return "", "empty"
    if not names_one_property(tidied):
        return tidied, "multiple"
    if ADDRESS_SHAPE.match(tidied):
        return tidied, "whole"
    sentence = incomplete_address(tidied)
    fault = sentence.split(", but ", 1)[1].split(", so I cannot", 1)[0]
    return tidied, f"incomplete: {fault}"


def render(addresses: list[str]) -> str:
    """The corpus file's text for a set of raw addresses.

    Args:
        addresses: Raw addresses, any order, duplicates allowed.

    Returns:
        A header line and one tab-separated row per unique address, sorted.

    Raises:
        Nothing.
    """
    rows = [HEADER]
    for raw in sorted({" ".join(item.split()) for item in addresses if item.strip()}):
        tidied, verdict = verdict_for(raw)
        rows.append(f"{raw}\t{tidied}\t{verdict}")
    return "\n".join(rows) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Read the live addresses and rewrite the corpus file."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tab", default="", help="response tab; defaults to the configured one")
    parser.add_argument(
        "--extra",
        default="",
        help="a file of additional addresses, one per line, such as ones people stated in Slack",
    )
    parser.add_argument("--out", default=str(CORPUS_FILE), help="where to write the corpus")
    args = parser.parse_args(argv)
    try:
        settings = Settings.load()
    except ConfigError as exc:
        print(f"configuration problem: {exc}", file=sys.stderr)
        return 2
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        str(settings.google_service_account_file), scopes=[READ_ONLY_SCOPE]
    )
    sheets = build_google_service("sheets", "v4", credentials)
    client = SheetClient(spreadsheet_id=settings.sheet_id, service=sheets)
    tab = args.tab or settings.tab_responses
    addresses = [item.intake.address for item in repo.read_submissions(client, tab)]
    if args.extra:
        addresses.extend(Path(args.extra).read_text(encoding="utf-8").splitlines())
    text = render(addresses)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"wrote {text.count(chr(10)) - 1} addresses to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
