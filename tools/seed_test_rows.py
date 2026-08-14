"""Append test submissions to a Testing tab, by header, never by position.

Gable's runtime is read-only against the workbook and must stay that way. A
deliberate test campaign still needs rows to exist, because there is no
synthetic-submission path and there should not be: the point of a live test is
that it walks the same header discovery, identity assignment, and reconciliation
a real submission does.

So this is the one write-scoped tool, and it is fenced accordingly:

- It refuses any tab whose name does not begin with ``Testing``. The production
  response tab is never a legal target, whatever is typed.
- It appends. It never updates or deletes an existing row.
- It writes by header text, so a tab whose columns sit in a different order
  from the live form is filled correctly rather than plausibly.
- It refuses a request type with no design in Generic Templates, and an agent
  who is not in the contact workbook, because both produce a run that stops
  before it can test anything.

Run it, then start each row with ``python -m tools.run_row <tab> <row>``.

Does not handle: starting runs, Slack, or anything about the campaign's order.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from google.oauth2 import service_account

from gable.agents.contacts import Contact, read_contacts
from gable.config import ConfigError, Settings
from gable.google_client import build_google_service
from gable.listings.intake import columns_from_header, maps_a_response_row
from gable.sheets import repository as repo
from gable.slides.library import list_files as list_template_files

#: The only tabs this tool may write to. Checked before any Google client is
#: built, so a mistyped production tab cannot even reach the API.
TAB_PREFIX: Final[str] = "Testing"

#: Read-write on the workbook, read-only on Drive. The narrowest pair that can
#: append a row and still confirm the agent and design exist first.
SCOPES: Final[tuple[str, ...]] = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
)

#: The acknowledgement every real submission carries. Copied verbatim so a
#: seeded row is shaped like the thing it stands in for.
ACKNOWLEDGEMENT: Final[str] = (
    "I understand that requests are fulfilled Monday through Friday, 9:00 AM to "
    "5:00 PM EST., I acknowledge that there is a 24-hour turnaround time for all "
    "requests. (48-hour for Video requests), I understand that listing posts are "
    "completed by request only and are not automatically generated., I acknowledge "
    "that social media posts and reposts are handled at the discretion of the "
    "brokerage."
)


class SeedError(Exception):
    """A row was refused before anything was written."""


@dataclass(frozen=True, slots=True)
class Listing:
    """One test submission, in the words the form would have collected."""

    request_type: str
    address: str
    open_house: str = ""
    new_price: str = ""
    closing_price: str = ""
    post_details: str = ""
    side: str = ""
    extra_notes: str = ""


def _timestamp() -> str:
    """Return a form-shaped submission time. Column A must never be empty."""
    return datetime.now(UTC).strftime("%m/%d/%Y %H:%M:%S")


def check_tab(tab: str) -> str:
    """Return the tab name, or refuse anything outside the Testing tabs.

    Args:
        tab: The tab a person named on the command line.

    Returns:
        The same name, stripped.

    Raises:
        SeedError: for any tab that is not a Testing tab. This is the whole
            safety of the tool, so it is checked first and checked once.
    """
    cleaned = tab.strip()
    if not cleaned.casefold().startswith(TAB_PREFIX.casefold()):
        msg = (
            f"refusing to write to {cleaned!r}: this tool only appends to a tab "
            f"whose name begins with {TAB_PREFIX!r}. The form's own response tab "
            "is read-only and always will be."
        )
        raise SeedError(msg)
    return cleaned


def find_agent(contacts: list[Contact], wanted: str) -> Contact:
    """Resolve one salesperson by email or full name.

    Args:
        contacts: The mirrored contact workbook.
        wanted: An email address, or a full name as the workbook spells it.

    Returns:
        The matching contact.

    Raises:
        SeedError: when nobody matches, or more than one does. An unknown agent
            stops the run at the contact gate before it can test a design, so
            it is better refused here with the near matches named.
    """
    folded = wanted.strip().casefold()
    by_email = [person for person in contacts if person.email.casefold() == folded]
    if len(by_email) == 1:
        return by_email[0]
    by_name = [
        person
        for person in contacts
        if f"{person.first_name} {person.last_name}".strip().casefold() == folded
    ]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_email) > 1 or len(by_name) > 1:
        msg = (
            f"{wanted!r} matches more than one person in the contact workbook; "
            "name them by their exact email address instead"
        )
        raise SeedError(msg)
    close = [
        person.email
        for person in contacts
        if folded
        and (
            folded in person.email.casefold()
            or folded in f"{person.first_name} {person.last_name}".casefold()
        )
    ]
    msg = f"no salesperson in the contact workbook matches {wanted!r}"
    if close:
        msg += f". Did you mean: {', '.join(sorted(close)[:5])}"
    raise SeedError(msg)


def check_design(designs: list[str], request_type: str) -> str:
    """Return the request type, or refuse one with no design to build from.

    Args:
        designs: File names currently in Generic Templates.
        request_type: The request type to seed.

    Returns:
        The design's exact name, so a case difference cannot open a run that
        stops at ``needs_template``.

    Raises:
        SeedError: when no design carries that name.
    """
    folded = request_type.strip().casefold()
    for name in designs:
        if name.casefold() == folded:
            return name
    msg = (
        f"no design named {request_type!r} is in Generic Templates, so this row "
        f"would stop before it built anything. Available: {', '.join(sorted(designs))}"
    )
    raise SeedError(msg)


def row_for(header: list[str], agent: Contact, listing: Listing) -> list[str]:
    """Lay one submission out under the tab's own header.

    Args:
        header: The header row exactly as the tab carries it.
        agent: The submitting salesperson.
        listing: What the form collected.

    Returns:
        A row the same width as the header, with the timestamp in column A.

    Raises:
        SeedError: when the header names no email, request type, or address
            column. Writing positionally into a tab of unknown shape is the one
            thing that could put real-looking values in the wrong fields.
    """
    columns = columns_from_header(header)
    if not maps_a_response_row(columns):
        msg = (
            "that tab's header does not name the email, request type and property "
            "address columns, so I will not guess which column is which"
        )
        raise SeedError(msg)
    row = [""] * len(header)
    row[0] = _timestamp()
    values = {
        "agent_email": agent.email,
        "agent_first_name": agent.first_name,
        "agent_last_name": agent.last_name,
        "agent_name": f"{agent.first_name} {agent.last_name}".strip(),
        "request_type": listing.request_type,
        "address": listing.address,
        "open_house": listing.open_house,
        "new_price": listing.new_price,
        "closing_price": listing.closing_price,
        "post_details": listing.post_details,
        "side": listing.side,
        "extra_notes": listing.extra_notes,
    }
    for field, value in values.items():
        index = columns.get(field)
        if index is not None and index < len(row) and value:
            row[index] = value
    for index, name in enumerate(header):
        if "acknowledgment" in name.casefold() or "acknowledgement" in name.casefold():
            row[index] = ACKNOWLEDGEMENT
    return row


def read_tab(sheets: Any, sheet_id: str, tab: str) -> list[list[str]]:  # noqa: ANN401
    """Return every row of a tab, as the sheet stores it."""
    response = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"'{tab}'!{repo.RESPONSES_RANGE}")
        .execute()
    )
    return [list(row) for row in response.get("values", [])]


def append_rows(
    sheets: Any,  # noqa: ANN401 - googleapiclient resource, untyped upstream
    sheet_id: str,
    tab: str,
    rows: list[list[str]],
    first_row: int,
) -> None:
    """Write new rows at an exact range below everything already there.

    An explicit range is used rather than the append API's own row-finding, so
    the caller knows the row numbers it just created and can start each one.

    Args:
        sheets: A Sheets v4 resource.
        sheet_id: The workbook.
        tab: An already-checked Testing tab.
        rows: Rows to write, header-width.
        first_row: One-based row number the first row lands on.

    Raises:
        Exception: whatever the Sheets client raises on a write failure.
    """
    last_row = first_row + len(rows) - 1
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{tab}'!A{first_row}:{_column_letter(len(rows[0]))}{last_row}",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()


def _column_letter(count: int) -> str:
    """Return the spreadsheet letter for a one-based column count."""
    letters = ""
    while count > 0:
        count, remainder = divmod(count - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _parse(argv: list[str] | None) -> argparse.Namespace:
    """Read the command line."""
    parser = argparse.ArgumentParser(
        description="Append test submissions to a Testing tab, by header.",
    )
    parser.add_argument("tab", help="Testing tab to append to, e.g. Testing_1")
    parser.add_argument("agent", help="Salesperson's email address, or exact full name")
    parser.add_argument(
        "--request-type",
        action="append",
        default=None,
        help="Request type to seed. Repeat for several; omit for every live design.",
    )
    parser.add_argument("--address", required=True, help="Property address, with state or ZIP")
    parser.add_argument("--open-house", default="", help="Open house date and time")
    parser.add_argument("--new-price", default="", help="New price, for a price reduction")
    parser.add_argument("--closing-price", default="", help="Closing price, for a sold post")
    parser.add_argument("--details", default="", help="Post details, required for a client review")
    parser.add_argument("--side", default="", help="Buyer or seller side")
    parser.add_argument("--notes", default="", help="Additional notes for the social media team")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the rows and the row numbers they would take, and write nothing.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Append one row per request type and print where each landed."""
    args = _parse(argv)
    try:
        tab = check_tab(args.tab)
    except SeedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        settings = Settings.load(require_credentials=False)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        str(settings.google_service_account_file), scopes=list(SCOPES)
    )
    drive = build_google_service("drive", "v3", credentials)
    sheets = build_google_service("sheets", "v4", credentials)

    try:
        designs = [
            found.name
            for found in list_template_files(
                drive, settings.drive_id, settings.drive_templates_folder_id
            )
            if found.is_slides
        ]
        contacts = read_contacts(drive, settings.drive_id, settings.drive_templates_folder_id)
        agent = find_agent(contacts, args.agent)
        wanted = args.request_type or sorted(designs)
        request_types = [check_design(designs, name) for name in wanted]
    except SeedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"could not read the designs or the contact workbook: {exc}", file=sys.stderr)
        return 2

    existing = read_tab(sheets, settings.sheet_id, tab)
    try:
        header_index, _ = repo.find_header(existing)
    except Exception as exc:
        print(f"could not read that tab's header: {exc}", file=sys.stderr)
        return 2
    header = existing[header_index]
    filled = (
        number for number, row in enumerate(existing, start=1) if any(str(c).strip() for c in row)
    )
    used = max(filled, default=header_index + 1)

    rows: list[list[str]] = []
    try:
        for request_type in request_types:
            rows.append(
                row_for(
                    header,
                    agent,
                    Listing(
                        request_type=request_type,
                        address=args.address,
                        open_house=args.open_house,
                        new_price=args.new_price,
                        closing_price=args.closing_price,
                        post_details=args.details,
                        side=args.side,
                        extra_notes=args.notes,
                    ),
                )
            )
    except SeedError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    first_row = used + 1
    who = f"{agent.first_name} {agent.last_name}".strip()
    for offset, request_type in enumerate(request_types):
        print(f"row {first_row + offset}: {request_type} — {who} — {args.address}")
    if args.dry_run:
        print(f"\ndry run: nothing written to {tab}.")
        return 0
    try:
        append_rows(sheets, settings.sheet_id, tab, rows, first_row)
    except Exception as exc:
        print(f"the rows could not be written: {exc}", file=sys.stderr)
        return 2
    print(f"\nwrote {len(rows)} row(s) to {tab}. Start one with:")
    print(f"  python -m tools.run_row '{tab}' {first_row}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
