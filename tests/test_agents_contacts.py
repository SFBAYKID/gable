"""The roster workbook: located by header and mirrored without guessing.

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
    ContactsError,
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
    """Just enough Drive to find and download one workbook."""

    def __init__(self, data: bytes) -> None:  # noqa: D107
        self.data = data

    def files(self) -> FakeDrive:  # noqa: D102
        return self

    def list(self, **kwargs: Any) -> FakeDrive:  # noqa: D102, ANN401
        self._query = str(kwargs.get("q", ""))
        return self

    def get_media(self, **_kwargs: Any) -> FakeDrive:  # noqa: D102, ANN401
        self._query = "media"
        return self

    def execute(self) -> Any:  # noqa: D102, ANN401
        if self._query == "media":
            return self.data
        if "mimeType='application/vnd.google-apps.folder'" in self._query:
            return {"files": [{"id": "folder-1", "name": "Agents Contact Information"}]}
        return {"files": [{"id": "book-1", "name": "Sales_Agents_Contact_Information.xlsx"}]}


class AmbiguousDrive(FakeDrive):
    """Return two exact roster folders or two workbooks on demand."""

    def __init__(self, data: bytes, *, duplicate_folders: bool) -> None:
        """Choose which Drive level is ambiguous."""
        super().__init__(data)
        self.duplicate_folders = duplicate_folders

    def execute(self) -> Any:  # noqa: ANN401
        """Return a duplicated source at the configured level."""
        if self._query == "media":
            return self.data
        if "mimeType='application/vnd.google-apps.folder'" in self._query:
            count = 2 if self.duplicate_folders else 1
            return {
                "files": [
                    {"id": f"folder-{index}", "name": "Agents Contact Information"}
                    for index in range(count)
                ]
            }
        return {
            "files": [{"id": f"book-{index}", "name": f"Roster {index}.xlsx"} for index in range(2)]
        }


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
    assert (people[0].first_name, people[0].last_name) == ("Andy", "Jang")


def test_a_missing_last_name_still_gives_a_usable_person() -> None:
    """The live workbook has Annie with no surname."""
    people = parse_contacts([HEADER, ["annie@x.com", "Annie", "", "410.624.8504"]])
    assert (people[0].first_name, people[0].last_name) == ("Annie", "")
    assert people[0].phone == "410.624.8504"


def test_a_duplicated_email_is_refused_instead_of_choosing_a_row() -> None:
    rows = [HEADER, ["a@x.com", "First", "Row", "1"], ["a@x.com", "Second", "Row", "2"]]
    with pytest.raises(ContactsError, match="more than once"):
        parse_contacts(rows)


def test_a_header_only_workbook_is_not_treated_as_an_empty_roster() -> None:
    """A malformed refresh must preserve the last complete local snapshot."""
    with pytest.raises(ContactsError, match="no usable agent rows"):
        parse_contacts([HEADER])


def test_syncing_mirrors_the_workbook_into_the_roster_lookup() -> None:
    connection = _db()
    drive = FakeDrive(_workbook([HEADER, ANDY, ANNIE]))

    assert sync_contacts(drive, connection, "drive-1", "templates-1") == 2

    found = repo.find_salesperson(connection, "andy@cornerhouserealty.com")
    assert found["first_name"] == "Andy"
    assert found["last_name"] == "Jang"
    assert found["phone"] == "410.218.2786"


def test_sync_removes_a_contact_deleted_from_the_source_workbook() -> None:
    """The local cache must not preserve an obsolete client-facing phone number."""
    connection = _db()
    sync_contacts(
        FakeDrive(_workbook([HEADER, ANDY, ANNIE])),
        connection,
        "drive-1",
        "templates-1",
    )

    sync_contacts(FakeDrive(_workbook([HEADER, ANDY])), connection, "drive-1", "templates-1")

    assert repo.find_salesperson(connection, "annie@cornerhouserealty.com") == {}
    assert repo.find_salesperson(connection, "andy@cornerhouserealty.com")["phone"] == ANDY[3]


def test_a_rejected_empty_refresh_preserves_the_prior_complete_roster() -> None:
    connection = _db()
    sync_contacts(FakeDrive(_workbook([HEADER, ANDY])), connection, "drive-1", "templates-1")

    with pytest.raises(ContactsError, match="no usable agent rows"):
        sync_contacts(FakeDrive(_workbook([HEADER])), connection, "drive-1", "templates-1")

    assert repo.find_salesperson(connection, "andy@cornerhouserealty.com")["phone"] == ANDY[3]


@pytest.mark.parametrize("duplicate_folders", [True, False])
def test_sync_refuses_ambiguous_roster_sources(duplicate_folders: bool) -> None:
    """Choosing the first of two human-maintained files would be a guess."""
    connection = _db()
    drive = AmbiguousDrive(_workbook([HEADER, ANDY]), duplicate_folders=duplicate_folders)

    with pytest.raises(ContactsError, match="more than one"):
        sync_contacts(drive, connection, "drive-1", "templates-1")
