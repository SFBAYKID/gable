"""Reading the intake Sheet. Read-only by construction.

Gable never writes to the Sheet. CLAUDE.md §11 forbids mutating form responses,
and everything Gable derives lives in its own database instead — so this client
is built with the read-only scope and has no write method to misuse.

Retries are bounded and jittered. Google returns 429 under load and a naive
retry loop turns one slow minute into a quota ban.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

#: Read-only. Widening this is a decision, not a convenience.
SCOPES: Final[tuple[str, ...]] = ("https://www.googleapis.com/auth/spreadsheets.readonly",)

#: Statuses worth retrying. Everything else is a real error and retrying it just
#: delays the report.
RETRYABLE: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})

MAX_ATTEMPTS: Final[int] = 5


class SheetError(Exception):
    """Raised when the Sheet cannot be read."""


class ReadsRanges(Protocol):
    """The only thing the pipeline needs from a sheet client.

    Narrower than `SheetClient` on purpose: the poller and the repository read
    ranges and nothing else, so they depend on that rather than on a concrete
    class holding credentials. It also means a test can supply canned rows
    without a Google account.
    """

    def read(self, a1_range: str) -> list[list[str]]:
        """Read an A1 range."""
        ...


@dataclass(frozen=True, slots=True)
class SheetClient:
    """A read-only handle on one spreadsheet."""

    spreadsheet_id: str
    service: Any

    @classmethod
    def build(cls, service_account_file: Path | str, spreadsheet_id: str) -> SheetClient:
        """Construct a client from a service-account key.

        Args:
            service_account_file: Path to the JSON key.
            spreadsheet_id: The workbook to read.

        Returns:
            A ready client.

        Raises:
            SheetError: if the credentials cannot be loaded. Raised here rather
                than at first use so a misconfigured deploy fails at startup.
        """
        try:
            credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                str(service_account_file), scopes=list(SCOPES)
            )
            service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        except Exception as exc:
            msg = f"could not load the service account key: {type(exc).__name__}"
            raise SheetError(msg) from exc
        return cls(spreadsheet_id=spreadsheet_id, service=service)

    def read(self, a1_range: str) -> list[list[str]]:
        """Read a range, retrying the failures worth retrying.

        Args:
            a1_range: An A1 range such as `'Form Responses 1'!A1:T`.

        Returns:
            Rows of strings. Trailing empty cells are omitted by Sheets, so rows
            are ragged and every caller must bounds-check.

        Raises:
            SheetError: after the retry budget is spent, or immediately on an
                error that retrying cannot fix.
        """
        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = (
                    self.service.spreadsheets()
                    .values()
                    .get(spreadsheetId=self.spreadsheet_id, range=a1_range)
                    .execute()
                )
                rows: list[list[str]] = response.get("values", [])
                return rows
            except HttpError as exc:
                last = exc
                if exc.resp.status not in RETRYABLE or attempt == MAX_ATTEMPTS - 1:
                    break
                # Jittered, so several retries do not synchronise into a spike.
                delay = (2**attempt) + random.uniform(0, 1)
                time.sleep(delay)
            except Exception as exc:
                last = exc
                break
        msg = f"could not read {a1_range}: {type(last).__name__}"
        raise SheetError(msg) from last

    def tab_names(self) -> list[str]:
        """Every tab in the workbook.

        Returns:
            Tab titles in sheet order.

        Raises:
            SheetError: if the workbook cannot be read.
        """
        try:
            meta = (
                self.service.spreadsheets()
                .get(spreadsheetId=self.spreadsheet_id, fields="sheets.properties.title")
                .execute()
            )
        except Exception as exc:
            msg = f"could not list tabs: {type(exc).__name__}"
            raise SheetError(msg) from exc
        return [s["properties"]["title"] for s in meta.get("sheets", [])]
