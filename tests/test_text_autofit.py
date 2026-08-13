"""End-to-end unit coverage for readable automatic Slides text fitting."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from gable.agents.website import OfficialProfile, ProfileLookup
from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.pipeline.vision import Inspection
from gable.slides import fitting
from gable.slides.preflight import Issue, Report
from tests.runner_support import Recorder, record, runner, submission


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """Return one migrated database for a runner test."""
    connection = connect(tmp_path / "g.db")
    apply_migrations(connection)
    return connection


def test_mike_sold_text_autofits_in_slides_and_still_reads_back(
    db: sqlite3.Connection,
) -> None:
    """Mike's exact Sold flow asks only for the photo, then posts one outcome."""
    item = submission(
        rid="rid-mike-sold-autofit",
        email="mike@cornerhouserealty.com",
        name="Mike Kulnich",
        request_type="Sold",
        address="703 Perception Way, Aberdeen, MD 21001",
        closing_price="615000",
    )
    record(db, item)
    db.execute(
        "INSERT INTO salespeople "
        "(email, first_name, last_name, phone, template, synced_at) "
        "VALUES (?, ?, ?, ?, '', 'now')",
        ("mike@cornerhouserealty.com", "Mike", "Kulnich", "410.456.3564"),
    )
    rec = Recorder(
        slide_text=[
            "32 S Prospect Ave Baltimore, MD 21228",
            "Kelli Kulnich",
            "Realtor",
            "443.326.7170",
            "kelli@cornerhouserealty.com",
        ],
        template_label="Sold",
    )

    def official_profile(name: str, email: str, _phone: str = "") -> ProfileLookup:
        return ProfileLookup(
            profile=OfficialProfile(
                name=name,
                email=email,
                phone="410.456.3564",
                title="REALTOR®",
                source_url="https://cornerhouserealty.com/mike-kulnich/",
            )
        )

    waiting = runner(db, rec)
    waiting.hero_photo_url = ""
    waiting.official_contact_lookup = official_profile

    paused = waiting.run(item)

    assert paused.status == "needs_photo"
    assert rec.said == [
        "New Sold request from Mike Kulnich — 703 Perception Way, Aberdeen, MD 21001",
        "Can you send me the image?",
    ]
    assert rec.threads == [None, "1786.0"]
    assert rec.copied is False

    flyer = runner(db, rec)
    flyer.origin_thread_ts = "1786.0"
    flyer.official_contact_lookup = official_profile
    flyer.preflight_template = lambda *_args: Report(
        issues=(
            Issue(
                "large_photo_crop",
                "I center-cropped and fitted the photo to the current frame.",
                advisory="I center-cropped and fitted the photo to the current frame.",
            ),
        )
    )
    applied: list[dict[str, Any]] = []
    flyer.read_text_boxes = lambda _file_id: [
        fitting.TextBox(
            "sold-address",
            "703 Perception Way, Aberdeen, MD 21001",
            44.23,
            522.87 * fitting.EMU_PER_POINT,
            lines=2,
        ),
        fitting.TextBox(
            "sold-agent-name",
            "Mike Kulnich",
            32.91,
            235.72 * fitting.EMU_PER_POINT,
            weight=700,
        ),
        fitting.TextBox(
            "sold-agent-title",
            "REALTOR®",
            23.95,
            80.30 * fitting.EMU_PER_POINT,
        ),
        fitting.TextBox(
            "sold-agent-phone",
            "410.456.3564",
            16.69,
            98.04 * fitting.EMU_PER_POINT,
        ),
        fitting.TextBox(
            "sold-agent-email",
            "mike@cornerhouserealty.com",
            16.69,
            250 * fitting.EMU_PER_POINT,
        ),
    ]
    flyer.apply = lambda _file_id, requests: applied.extend(requests)
    inspected: list[str] = []

    def inspect_render(run_id: str, _image: bytes) -> Inspection:
        """Record that the final rendered-flyer gate actually ran."""
        inspected.append(run_id)
        return Inspection(looks_right=True, confident=True)

    flyer.look_at = inspect_render

    result = flyer.resume(
        item,
        paused.run_id,
        resume_fields={
            "photo_url": flyer.hero_photo_url,
            "photo_source": "slack_upload",
        },
    )

    assert result.status == "delivered"
    assert rec.output_text == [
        "703 Perception Way, Aberdeen, MD 21001",
        "Mike Kulnich",
        "REALTOR®",
        "410.456.3564",
        "mike@cornerhouserealty.com",
    ]
    updates = [request["updateTextStyle"] for request in applied]
    assert {update["objectId"] for update in updates} == {
        "sold-agent-title",
        "sold-agent-phone",
    }
    sizes = {
        update["objectId"]: float(update["style"]["fontSize"]["magnitude"]) for update in updates
    }
    assert sizes == {"sold-agent-title": 14.93, "sold-agent-phone": 13.13}
    assert inspected == [paused.run_id]
    assert store.run_attempt_count(db, item.response_row_id) == 1
    assert len(result.said) == 1
    assert len(rec.said) == 3
    assert rec.threads[-1] == "1786.0"
    final = result.said[0]
    assert "Open the flyer" in final
    assert "center-cropped and fitted" in final
    assert "reduced the agent title and agent phone text sizes" in final
    assert all(
        forbidden not in final.casefold()
        for forbidden in ("run anyway", "update the template", "widen", "?")
    )


def test_an_unreadable_fit_never_emits_a_font_request() -> None:
    """The autofit path cannot hide overflow by applying sub-minimum text."""
    fit = fitting.fit_for(
        "email",
        "a.very.long.agent.address@cornerhouserealty.com",
        20,
        40 * fitting.EMU_PER_POINT,
        weight=700,
    )

    assert fit.too_small_to_read
    assert fitting.requests_for([fit]) == []


def test_rounding_cannot_make_a_near_boundary_fit_overflow() -> None:
    """An 8.04-point requirement stays safely above the blocked boundary."""
    text = "Mike Kulnich"
    current = 20.0
    required = 8.04
    needed = fitting.estimate_width_pt(text, current, 700)
    box_width_pt = needed * required / current
    width_emu = box_width_pt / fitting.SAFETY * fitting.EMU_PER_POINT

    fit = fitting.fit_for("name", text, current, width_emu, weight=700)

    assert fit.required_pt == pytest.approx(required)
    assert fit.fitted_pt <= required
    assert fit.fitted_pt > fitting.MIN_READABLE_PT
    assert not fit.too_small_to_read
    assert fitting.requests_for([fit])


def test_rounding_at_the_eight_point_boundary_remains_blocked() -> None:
    """A fit whose safe hundredth-point size is 8pt is never applied."""
    text = "Mike Kulnich"
    current = 20.0
    required = 8.009
    needed = fitting.estimate_width_pt(text, current, 700)
    box_width_pt = needed * required / current
    width_emu = box_width_pt / fitting.SAFETY * fitting.EMU_PER_POINT

    fit = fitting.fit_for("name", text, current, width_emu, weight=700)

    assert fit.fitted_pt == fitting.MIN_READABLE_PT
    assert fit.too_small_to_read
    assert fitting.requests_for([fit]) == []
