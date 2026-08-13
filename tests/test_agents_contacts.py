"""The roster workbook: located by header, mirrored, and appended to.

The shapes here are the real ones, read from
`Sales_Agents_Contact_Information.xlsx` through the service account on
2026-08-12: one worksheet, `Email | First Name | Last Name | Phone`, header on
the first row.
"""

from __future__ import annotations

import io
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from gable.agents.contacts import (
    Contact,
    ContactsError,
    append_contact,
    find_header,
    parse_contacts,
    sync_contacts,
)
from gable.db.schema import apply_migrations, connect
from gable.sheets import repository as repo

HEADER = ["Email", "First Name", "Last Name", "Phone"]
ANDY = ["andy@cornerhouserealty.com", "Andy", "Jang", "410.218.2786"]
ANNIE = ["annie@cornerhouserealty.com", "Annie", None, "410.624.8504"]


def _workbook(rows: list[list[Any]]) -> bytes:
    """An xlsx in memory, the way Drive hands one back."""
    book = openpyxl.Workbook()
    sheet = book.worksheets[0]
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


class FakeDrive:
    """Just enough Drive to find, download and replace one workbook."""

    def __init__(self, data: bytes) -> None:  # noqa: D107
        self.data = data
        self.uploaded: bytes | None = None

    def files(self) -> FakeDrive:  # noqa: D102
        return self

    def list(self, **kwargs: Any) -> FakeDrive:  # noqa: D102, ANN401
        self._query = str(kwargs.get("q", ""))
        return self

    def get_media(self, **_kwargs: Any) -> FakeDrive:  # noqa: D102, ANN401
        self._query = "media"
        return self

    def update(self, **kwargs: Any) -> FakeDrive:  # noqa: D102, ANN401
        media = kwargs["media_body"]
        # MediaIoBaseUpload keeps the buffer it was handed on `_fd`.
        self.uploaded = media._fd.getvalue() if hasattr(media, "_fd") else b""
        self._query = "update"
        return self

    def execute(self) -> Any:  # noqa: D102, ANN401
        if self._query == "media":
            return self.data
        if self._query == "update":
            return {}
        if "mimeType='application/vnd.google-apps.folder'" in self._query:
            return {"files": [{"id": "folder-1", "name": "Agents Contact Information"}]}
        return {"files": [{"id": "book-1", "name": "Sales_Agents_Contact_Information.xlsx"}]}


def _db() -> sqlite3.Connection:
    connection = connect(Path(tempfile.mkdtemp()) / "contacts.db")
    apply_migrations(connection)
    return connection


def test_the_header_is_found_on_the_first_row() -> None:
    index, columns = find_header([HEADER, [str(c) for c in ANDY]])
    assert index == 0
    assert columns["email"] == 0
    assert columns["phone"] == 3


def test_the_header_is_found_under_a_blank_row() -> None:
    """It has already moved once. Finding it is cheaper than being wrong."""
    index, _ = find_header([[], HEADER])
    assert index == 1


def test_a_sheet_with_no_email_column_is_refused_not_read_as_empty() -> None:
    """Storing nobody silently is what put the office number on a real flyer."""
    with pytest.raises(ContactsError):
        find_header([[str(c) for c in ANDY]])


def test_every_agent_below_the_header_is_parsed() -> None:
    rows = [HEADER, [str(c) for c in ANDY], ["", "", "", ""], ["annie@x.com", "Annie", "", "410"]]
    people = parse_contacts(rows)
    assert [p.email for p in people] == ["andy@cornerhouserealty.com", "annie@x.com"]
    assert people[0].full_name == "Andy Jang"


def test_a_missing_last_name_still_gives_a_usable_person() -> None:
    """The live workbook has Annie with no surname."""
    people = parse_contacts([HEADER, ["annie@x.com", "Annie", "", "410.624.8504"]])
    assert people[0].full_name == "Annie"
    assert people[0].phone == "410.624.8504"


def test_a_duplicated_email_keeps_the_first_row() -> None:
    rows = [HEADER, ["a@x.com", "First", "Row", "1"], ["a@x.com", "Second", "Row", "2"]]
    assert [p.first_name for p in parse_contacts(rows)] == ["First"]


def test_syncing_mirrors_the_workbook_into_the_roster_lookup() -> None:
    connection = _db()
    drive = FakeDrive(_workbook([HEADER, ANDY, ANNIE]))

    assert sync_contacts(drive, connection, "drive-1", "templates-1") == 2

    found = repo.find_salesperson(connection, "andy@cornerhouserealty.com")
    assert found["first_name"] == "Andy"
    assert found["last_name"] == "Jang"
    assert found["phone"] == "410.218.2786"


def test_an_agent_reached_by_name_resolves_too() -> None:
    """A co-agent is named in prose, with no email to look them up by."""
    connection = _db()
    sync_contacts(FakeDrive(_workbook([HEADER, ANDY])), connection, "drive-1", "templates-1")
    assert repo.find_salesperson_by_name(connection, "Andy Jang")["phone"] == "410.218.2786"


def test_appending_adds_a_row_and_leaves_the_others_alone() -> None:
    drive = FakeDrive(_workbook([HEADER, ANDY]))
    added = append_contact(
        drive,
        "drive-1",
        "templates-1",
        Contact("jane@cornerhouserealty.com", "Jane", "Doe", "410.555.0134"),
    )
    assert added is True
    assert drive.uploaded is not None

    people = parse_contacts(_rows(drive.uploaded))
    assert [p.email for p in people] == ["andy@cornerhouserealty.com", "jane@cornerhouserealty.com"]
    assert people[0].phone == "410.218.2786", "an existing row must survive untouched"


def test_appending_someone_already_there_changes_nothing() -> None:
    """Never overwrite what a human wrote. A wrong number reaches a client."""
    drive = FakeDrive(_workbook([HEADER, ANDY]))
    added = append_contact(
        drive,
        "drive-1",
        "templates-1",
        Contact("andy@cornerhouserealty.com", "Andrew", "Jang", "000.000.0000"),
    )
    assert added is False
    assert drive.uploaded is None


def _rows(data: bytes) -> list[list[str]]:
    book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        return [
            ["" if cell is None else str(cell).strip() for cell in row]
            for row in book.worksheets[0].iter_rows(values_only=True)
        ]
    finally:
        book.close()
