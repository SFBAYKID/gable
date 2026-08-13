"""Shared fakes and factories for the runner's focused test modules."""

from __future__ import annotations

import sqlite3

from gable.db import store
from gable.listings.enrich import Facts
from gable.listings.intake import from_row
from gable.pipeline.runner import Runner
from gable.pipeline.vision import Inspection
from gable.sheets import repository as repo


def submission(**over: str) -> repo.Submission:
    """Build one representative parsed form submission."""
    row = [
        over.get("ts", "8/11/2026 09:00:00"),
        over.get("email", "lolo@cornerhouserealty.com"),
        over.get("name", "Lolo Simmons"),
        "ack",
        over.get("request_type", "New Listing"),
        "",
        "",
        "",
        "",
        "",
        "Static",
        over.get("address", "7940 Oakwood Rd, Glen Burnie, MD 21061"),
        "",
        over.get("details", ""),
        over.get("open_house", ""),
        over.get("new_price", ""),
        over.get("closing_price", ""),
    ]
    return repo.Submission(
        response_row_id=over.get("rid", "rid-1"),
        sheet_row=100,
        submitted_at=row[0],
        intake=from_row(row),
        content_hash="hash",
    )


class Recorder:
    """Capture the external work a runner attempted."""

    def __init__(self, slide_text: list[str] | None = None) -> None:
        """Start with a template whose text the runner can resolve."""
        self.said: list[str] = []
        self.threads: list[str | None] = []
        self.filled: dict[str, str] = {}
        self.copied = False
        self.photo_placed = False
        self.slide_text = slide_text or [
            "[PROPERTY ADDRESS]",
            "[PRICE]",
            "[ 4 BEDS ]",
            "[ 4 BATHS ]",
            "[ SQFT ]",
            "AGENT NAME",
            "Phone",
        ]
        self.output_text: list[str] = []

    def say(self, text: str, thread: str | None = None) -> str:
        """Record a message and the thread it went to."""
        self.said.append(text)
        self.threads.append(thread)
        return "1786.0"

    def pick(self, category: str, intake: object = None) -> tuple[str, str]:  # noqa: ARG002
        """Always find a representative template."""
        return ("tmpl-1", f"{category} — Bracket Placeholders (cleanest)")

    def read(self, file_id: str) -> list[str]:
        """Return template text before fill and simulated output text after."""
        return self.output_text if file_id == "out-1" and self.output_text else self.slide_text

    def copy(self, template_id: str, name: str) -> tuple[str, str]:  # noqa: ARG002
        """Pretend to copy a template and remember that it happened."""
        self.copied = True
        return ("out-1", "https://docs.google.com/presentation/d/out-1/edit")

    def place_photo(
        self,
        _run_id: str,
        _file_id: str,
        _url: str,
        _template_label: str,
    ) -> bool:
        """Pretend the hero photo was placed."""
        self.photo_placed = True
        return True

    def fill(self, file_id: str, pairs: dict[str, str]) -> int:  # noqa: ARG002
        """Record replacements and simulate their effects."""
        self.filled = pairs
        self.output_text = [pairs.get(text, text) for text in self.slide_text]
        return len(pairs)


def runner(db: sqlite3.Connection, rec: Recorder, facts: Facts | None = None) -> Runner:
    """Build a runner with deterministic external seams."""
    db.execute(
        "INSERT INTO salespeople (email, first_name, last_name, phone, template, synced_at)"
        " VALUES ('lolo@cornerhouserealty.com','Lolo','Simmons',"
        "'(443) 854-8554','Just Listed','now')"
        " ON CONFLICT(email) DO NOTHING"
    )
    return Runner(
        connection=db,
        hero_photo_url="http://198.51.100.7/abcdef0123456789.jpg",
        place_photo=rec.place_photo,
        say=rec.say,
        pick_template=rec.pick,
        read_slide_text=rec.read,
        copy_template=rec.copy,
        fill=rec.fill,
        look_at=lambda _run_id, _image: Inspection(looks_right=True, confident=True),
        research=lambda _address: (
            facts
            or Facts(
                beds="4",
                baths="3",
                square_feet="1,804",
                list_price="$515,000",
                source_url="https://redfin.test",
                confidence=0.95,
            )
        ),
    )


def record(db: sqlite3.Connection, item: repo.Submission) -> None:
    """Persist a submission before opening a runner attempt."""
    store.record_submission(
        db,
        item.response_row_id,
        item.sheet_row,
        item.submitted_at,
        item.intake,
        item.content_hash,
    )
