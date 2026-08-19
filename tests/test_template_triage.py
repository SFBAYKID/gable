"""New source templates are measured once and rechecked from their owned thread."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.pipeline.questions import ReconcileState, Reconciliation
from gable.pipeline.template_triage import TemplateTriage, drain_template_notifications
from gable.pipeline.vision import Inspection
from gable.slides import fitting
from gable.slides.library import TemplateFile
from gable.voice import violations


def _say_into(messages: list[str]) -> Callable[[str, str | None], str]:
    """Return a typed Slack recorder with a stable root per posted message."""

    def say(text: str, _thread: str | None) -> str:
        messages.append(text)
        return f"thread-{len(messages)}"

    return say


def _text(object_id: str, text: str, width_pt: float) -> dict[str, Any]:
    return {
        "objectId": object_id,
        "size": {
            "width": {"magnitude": width_pt * fitting.EMU_PER_POINT},
            "height": {"magnitude": 30 * fitting.EMU_PER_POINT},
        },
        "transform": {"scaleX": 1, "scaleY": 1},
        "shape": {
            "shapeType": "TEXT_BOX",
            "text": {
                "textElements": [
                    {
                        "textRun": {
                            "content": text,
                            "style": {"fontSize": {"magnitude": 20, "unit": "PT"}},
                        }
                    }
                ]
            },
        },
    }


def _presentation(email_width: float = 700, name_width: float = 900) -> dict[str, Any]:
    return {
        "pageSize": {
            "width": {"magnitude": 10_000_000},
            "height": {"magnitude": 12_500_000},
        },
        "slides": [
            {
                "objectId": "page-1",
                "pageElements": [
                    _text("address", "[PROPERTY ADDRESS]", 900),
                    _text("name", "AGENT NAME", name_width),
                    _text("email", "Email", email_width),
                    {
                        "objectId": "hero",
                        "size": {
                            "width": {"magnitude": 8_000_000},
                            "height": {"magnitude": 6_000_000},
                        },
                        "transform": {"scaleX": 1, "scaleY": 1},
                        "shape": {"shapeProperties": {"shapeBackgroundFill": {}}},
                    },
                ],
            }
        ],
    }


def test_first_scan_adopts_existing_files_without_flooding_slack(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files = [TemplateFile("old-1", "New Listing", "one")]
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: files,
        lambda _file_id: _presentation(),
        _say_into(said),
    )

    assert triage.scan_new() == 0
    assert said == []
    assert store.template_catalog_adopted(connection)
    assert store.template_audit(connection, "old-1") is not None
    assert triage.scan_new() == 0
    assert said == []
    connection.close()


def test_transient_empty_first_read_waits_for_a_real_catalogue_before_adoption(
    tmp_path: Path,
) -> None:
    """A missing-folder-shaped empty read cannot turn the catalogue into new files."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    inspections = 0
    posts: list[str] = []

    def inspect(_file_id: str) -> Inspection:
        nonlocal inspections
        inspections += 1
        return Inspection(True, True)

    def post(text: str, _thread: str | None) -> str:
        posts.append(text)
        return "unexpected"

    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        post,
        look_at=inspect,
    )

    assert triage.scan_new() == 0
    assert not store.template_catalog_adopted(connection)
    files.extend(
        [
            TemplateFile("established-one", "New Listing", "one"),
            TemplateFile("established-two", "Sold", "one"),
        ]
    )
    assert triage.scan_new() == 0
    assert store.template_catalog_adopted(connection)
    assert inspections == 0
    assert posts == []
    assert triage.scan_new() == 0
    connection.close()


def test_new_file_is_measured_and_owns_a_recheck_thread(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    presentations: dict[str, dict[str, Any]] = {}
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda file_id: presentations[file_id],
        _say_into(said),
        look_at=lambda _file_id: Inspection(True, True),
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])

    files.append(TemplateFile("new-1", "New Listing", "one"))
    presentations["new-1"] = _presentation(email_width=100)
    assert triage.scan_new() == 1
    assert len(said) == 1
    assert "agent email" in said[0]
    assert not violations(said[0])
    audit = store.template_for_thread(connection, "thread-1")
    # A tight slot is advice, not a refusal: the design stays usable and every
    # real value is measured against that box before a flyer is built.
    assert audit is not None and audit.status == "ready"

    presentations["new-1"] = _presentation(email_width=700)
    files[0] = TemplateFile("new-1", "New Listing", "two")
    # A listing rebuild reloads the design and confirms it is safe to fill; it
    # does not re-certify how the artwork looks. The design's own thread does.
    outcome = triage.recheck_file("new-1")
    assert "found no structural or text-capacity problem" in outcome
    refreshed = store.template_for_thread(connection, "thread-1")
    assert refreshed is not None and refreshed.modified_time == "two"
    assert refreshed.status == "ready"
    connection.close()


def test_a_missing_source_revokes_its_prior_ready_audit(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        lambda _text, thread: thread or "thread-one",
        look_at=lambda _file_id: Inspection(True, True),
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])
    files.append(TemplateFile("new-1", "New Listing", "one"))
    assert triage.scan_new() == 1
    ready = store.template_audit(connection, "new-1")
    assert ready is not None and ready.status == "ready"

    files.clear()
    outcome = triage.recheck_file("new-1")

    assert "could not find the New Listing design" in outcome
    missing = store.template_audit(connection, "new-1")
    assert missing is not None and missing.status == "needs_template"
    assert missing.slack_thread_ts == "thread-one"
    connection.close()


def test_a_certified_template_is_rechecked_when_its_drive_revision_changes(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    presentations: dict[str, dict[str, Any]] = {}
    said: list[str] = []
    visual_calls = 0

    def look(_file_id: str) -> Inspection:
        nonlocal visual_calls
        visual_calls += 1
        return Inspection(True, True)

    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda file_id: presentations[file_id],
        _say_into(said),
        look_at=look,
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])
    files.append(TemplateFile("new-1", "New Listing", "revision-one"))
    presentations["new-1"] = _presentation(email_width=700)
    assert triage.scan_new() == 1
    first = store.template_audit(connection, "new-1")
    assert first is not None and first.status == "ready"

    files[0] = TemplateFile("new-1", "New Listing", "revision-two")
    presentations["new-1"] = _presentation(email_width=100)
    assert triage.scan_new() == 1

    changed = store.template_audit(connection, "new-1")
    assert changed is not None and changed.status == "ready"
    assert changed.modified_time == "revision-two"
    assert changed.slack_thread_ts == "thread-1"
    assert "updated New Listing design" in said[-1]
    assert "agent email" in said[-1]
    # Both revisions were inspected. A tight slot no longer refuses the design,
    # so it no longer skips the visual certification either — the design still
    # has to be looked at before Carmen is told it is ready.
    assert visual_calls == 2
    connection.close()


def test_duplicate_new_template_names_are_rejected_without_guessing(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        _say_into(said),
        look_at=lambda _file_id: Inspection(True, True),
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])
    files.extend(
        [
            TemplateFile("one", "New Listing", "one"),
            TemplateFile("two", " New   Listing ", "one"),
        ]
    )

    assert triage.scan_new() == 2
    assert all("another file has the same name" in message for message in said)

    still_duplicate = triage.recheck("thread-1")
    assert "another file has the same name" in still_duplicate
    duplicate_audit = store.template_for_thread(connection, "thread-1")
    assert duplicate_audit is not None and duplicate_audit.status == "needs_template"

    files.pop()
    resolved = triage.recheck("thread-1")
    assert "did not find a structural, text-capacity, or visible layout problem" in resolved
    resolved_audit = store.template_for_thread(connection, "thread-1")
    assert resolved_audit is not None and resolved_audit.status == "ready"
    connection.close()


def test_a_new_powerpoint_is_named_as_unsupported_instead_of_ignored(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        _say_into(said),
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])
    files.append(
        TemplateFile(
            "pptx-1",
            "New Listing.pptx",
            "one",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    )

    assert triage.scan_new() == 1
    assert "not a Google Slides design" in said[0]
    assert not violations(said[0])
    connection.close()


def test_visual_uncertainty_blocks_template_certification(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        _say_into(said),
        look_at=lambda _file_id: Inspection(False, False, checked=False),
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])
    files.append(TemplateFile("new-1", "New Listing", "one"))

    assert triage.scan_new() == 1
    assert "could not complete its visual inspection" in said[0]
    audit = store.template_audit(connection, "new-1")
    assert audit is not None and audit.status == "needs_template"
    connection.close()


def test_missing_visual_provider_fails_closed_instead_of_certifying(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        _say_into(said),
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])
    files.append(TemplateFile("new-1", "New Listing", "one"))

    assert triage.scan_new() == 1
    audit = store.template_audit(connection, "new-1")
    assert audit is not None and audit.status == "needs_template"
    assert "could not complete its visual inspection" in said[0]
    connection.close()


def test_recheck_names_the_visual_stage_and_reports_a_visible_defect(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        _say_into(said),
        look_at=lambda _file_id: Inspection(
            False,
            True,
            ["The contact details overlap the divider line."],
        ),
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])
    files.append(TemplateFile("new-1", "New Listing", "one"))
    triage.scan_new()
    stages: list[str] = []

    outcome = triage.recheck("thread-1", stages.append)

    assert stages == ["is inspecting the updated template..."]
    assert "contact details overlap the divider line" in outcome
    assert not violations(outcome)
    connection.close()


def test_a_failed_slack_post_retries_the_stored_verdict_without_reinspection(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    visual_calls = 0
    posts = 0

    def look(_file_id: str) -> Inspection:
        nonlocal visual_calls
        visual_calls += 1
        return Inspection(True, True)

    def say(_text: str, _thread: str | None) -> str:
        nonlocal posts
        posts += 1
        return "" if posts == 1 else "thread-retry"

    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        say,
        look_at=look,
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])
    files.append(TemplateFile("new-1", "New Listing", "one"))

    assert triage.scan_new() == 1
    first = store.template_audit(connection, "new-1")
    assert first is not None and first.slack_thread_ts == ""
    triage.reconcile = lambda *_args: Reconciliation(
        ReconcileState.FOUND,
        "thread-retry",
    )
    assert triage.scan_new() == 0
    retried = store.template_audit(connection, "new-1")
    assert retried is not None and retried.slack_thread_ts == "thread-retry"
    assert visual_calls == 1
    assert posts == 1
    connection.close()


def test_a_changed_template_retries_a_failed_thread_notice_without_reinspection(
    tmp_path: Path,
) -> None:
    """A notice that failed to post is retried without paying for vision again.

    The changed revision must have something to say. A clean unprompted re-read
    is silent now, so this drives the retry with a design whose second revision
    fails its visual inspection.
    """
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    visual_calls = 0
    posts = 0

    def look(_file_id: str) -> Inspection:
        nonlocal visual_calls
        visual_calls += 1
        if visual_calls == 2:
            return Inspection(False, True, problems=["the title overlaps the photo"])
        return Inspection(True, True)

    def say(_text: str, thread: str | None) -> str:
        nonlocal posts
        posts += 1
        if posts == 2:
            return ""
        return thread or "thread-one"

    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        say,
        look_at=look,
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])
    files.append(TemplateFile("new-1", "New Listing", "one"))
    assert triage.scan_new() == 1

    files[0] = TemplateFile("new-1", "New Listing", "two")
    assert triage.scan_new() == 1
    waiting = store.template_audit(connection, "new-1")
    assert waiting is not None and waiting.notification_pending

    triage.reconcile = lambda *_args: Reconciliation(
        ReconcileState.FOUND,
        "thread-update",
    )
    assert triage.scan_new() == 0
    delivered = store.template_audit(connection, "new-1")
    assert delivered is not None and not delivered.notification_pending
    assert delivered.slack_thread_ts == "thread-one"
    assert visual_calls == 2
    assert posts == 2
    connection.close()


def test_template_notice_retries_once_after_history_proves_the_failed_write_absent(
    tmp_path: Path,
) -> None:
    """A definite outage recovers after grace without repeating paid inspection."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])
    files = [TemplateFile("new-1", "New Listing", "one")]
    inspections = 0
    posts: list[str] = []
    history_calls = 0

    def inspect(_file_id: str) -> Inspection:
        nonlocal inspections
        inspections += 1
        return Inspection(True, True)

    def post_once(_text: str, _thread: str | None, client_id: str) -> str:
        posts.append(client_id)
        if len(posts) == 1:
            raise ConnectionError("definite test outage")
        return "template-confirmed"

    def initial_history(*_args: object) -> Reconciliation:
        nonlocal history_calls
        history_calls += 1
        return Reconciliation(
            ReconcileState.ABSENT if history_calls == 1 else ReconcileState.UNKNOWN
        )

    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        lambda _text, _thread: "unused",
        post_once=post_once,
        reconcile=initial_history,
        look_at=inspect,
    )
    assert triage.scan_new() == 1
    pending = store.template_audit(connection, "new-1")
    assert pending is not None and pending.notification_attempt_count == 1
    old = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    connection.execute(
        "UPDATE template_audits SET notification_attempted_at = ? WHERE file_id = ?",
        (old, "new-1"),
    )

    assert (
        drain_template_notifications(
            connection,
            lambda _text, _thread: "unused",
            post_once,
            lambda *_args: Reconciliation(ReconcileState.ABSENT),
        )
        == 1
    )
    delivered = store.template_audit(connection, "new-1")
    assert delivered is not None and not delivered.notification_pending
    assert delivered.notification_attempt_count == 2
    assert delivered.slack_thread_ts == "template-confirmed"
    assert len(posts) == 2 and posts[0] == posts[1]
    assert inspections == 1
    connection.close()


def test_a_design_never_scanned_is_measured_rather_than_refused(tmp_path: Path) -> None:
    """The live catalogue was empty, so a rebuild was refused over a scan nobody ran."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files = [TemplateFile("live-1", "Open House", "one")]
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(email_width=700),
        lambda _text, thread: thread or "thread-one",
        look_at=lambda _file_id: Inspection(True, True),
    )
    assert store.template_audit(connection, "live-1") is None

    outcome = triage.recheck_file("live-1")

    assert "found no structural or text-capacity problem" in outcome
    recorded = store.template_audit(connection, "live-1")
    assert recorded is not None and recorded.status == "ready"
    assert recorded.modified_time == "one"
    connection.close()


def test_a_design_that_is_not_in_the_folder_at_all_is_still_refused(tmp_path: Path) -> None:
    """Measuring nothing is not better than saying where to put the file back."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    triage = TemplateTriage(
        connection,
        list,
        lambda _file_id: _presentation(),
        lambda _text, thread: thread or "thread-one",
        look_at=lambda _file_id: Inspection(True, True),
    )

    outcome = triage.recheck_file("gone-1")

    assert "could not find this listing's design in Generic Templates" in outcome
    assert store.template_audit(connection, "gone-1") is None
    connection.close()


def test_a_listing_rebuild_is_not_refused_over_a_hypothetical_long_name(
    tmp_path: Path,
) -> None:
    """Tambria Eaton's flyer was refused over a 28-character name nobody has."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files = [TemplateFile("live-1", "Open House", "one")]
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(email_width=700, name_width=60),
        lambda _text, thread: thread or "thread-one",
        look_at=lambda _file_id: Inspection(True, True),
    )

    outcome = triage.recheck_file("live-1")

    assert "agent name" not in outcome
    recorded = store.template_audit(connection, "live-1")
    assert recorded is not None and recorded.status == "ready"
    connection.close()


def test_adopting_a_new_design_still_measures_its_character_capacity(
    tmp_path: Path,
) -> None:
    """The allowance is the standard for a design nobody has built on yet."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files = [TemplateFile("base", "Baseline", "zero")]
    said: list[str] = []
    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(email_width=700, name_width=60),
        _say_into(said),
        look_at=lambda _file_id: Inspection(True, True),
    )
    store.adopt_template_catalog(connection, [("base", "Baseline", "zero")])
    files.append(TemplateFile("new-1", "Open House", "one"))

    assert triage.scan_new() == 1

    recorded = store.template_audit(connection, "new-1")
    assert recorded is not None and recorded.status == "ready"
    assert "agent name" in recorded.summary
    connection.close()


def test_a_design_thread_recheck_still_inspects_how_the_artwork_looks(tmp_path: Path) -> None:
    """Certifying the design is the design thread's question, and it still asks."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files = [TemplateFile("base", "Baseline", "zero")]
    said: list[str] = []
    looked: list[str] = []

    def look(file_id: str) -> Inspection:
        looked.append(file_id)
        return Inspection(False, True, problems=["The open house tag is cut off"])

    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        _say_into(said),
        look_at=look,
    )
    store.adopt_template_catalog(connection, [("base", "Baseline", "zero")])
    files.append(TemplateFile("new-1", "New Listing", "one"))
    assert triage.scan_new() == 1
    looked.clear()

    owned = store.template_for_thread(connection, "thread-1")
    assert owned is not None
    outcome = triage.recheck(owned.slack_thread_ts)

    assert looked == ["new-1"]
    assert "the open house tag is cut off" in outcome


def test_a_listing_rebuild_pays_for_no_source_inspection(tmp_path: Path) -> None:
    """The finished flyer is inspected either way, so this call is waste."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    looked: list[str] = []

    def look(file_id: str) -> Inspection:
        looked.append(file_id)
        return Inspection(True, True)

    triage = TemplateTriage(
        connection,
        lambda: [TemplateFile("live-1", "Open House", "one")],
        lambda _file_id: _presentation(),
        lambda _text, thread: thread or "thread-one",
        look_at=look,
    )

    triage.recheck_file("live-1")

    assert looked == []
    recorded = store.template_audit(connection, "live-1")
    assert recorded is not None and recorded.status == "ready"


def test_an_unprompted_clean_reread_says_nothing(tmp_path: Path) -> None:
    """Carmen edited three designs in four minutes and got three non-events.

    Nobody asked Gable to look, and it found nothing, so there is nothing to
    report — in the channel where real listings arrive. A NEW design still
    announces itself, because that is news.
    """
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    said: list[str] = []

    def say(text: str, thread: str | None) -> str:
        said.append(text)
        return thread or "thread-one"

    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        say,
        look_at=lambda _file_id: Inspection(True, True),
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])

    files.append(TemplateFile("new-1", "New Listing", "one"))
    assert triage.scan_new() == 1
    assert len(said) == 1, "a new design is still announced once"
    assert "I checked the new" in said[0]

    # Carmen edits it three times; none of those is news.
    for revision in ("two", "three", "four"):
        files[0] = TemplateFile("new-1", "New Listing", revision)
        assert triage.scan_new() == 1
    assert len(said) == 1, "a clean re-read of an edited design stays silent"

    stored = store.template_audit(connection, "new-1")
    assert stored is not None
    assert stored.modified_time == "four", "the audit is still recorded"
    assert stored.status == "ready"
    assert not stored.notification_pending, "nothing is left queued to say later"
    connection.close()


def test_a_reread_that_finds_a_problem_still_speaks(tmp_path: Path) -> None:
    """Silence is only for a clean result; a broken edit must always be said."""
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    files: list[TemplateFile] = []
    said: list[str] = []
    looks: list[Inspection] = [
        Inspection(True, True),
        Inspection(False, True, problems=["the title overlaps the photo"]),
    ]

    def say(text: str, thread: str | None) -> str:
        said.append(text)
        return thread or "thread-one"

    triage = TemplateTriage(
        connection,
        lambda: list(files),
        lambda _file_id: _presentation(),
        say,
        look_at=lambda _file_id: looks[min(len(said), len(looks) - 1)],
    )
    store.adopt_template_catalog(connection, [("baseline", "Baseline", "zero")])
    files.append(TemplateFile("new-1", "New Listing", "one"))
    assert triage.scan_new() == 1

    files[0] = TemplateFile("new-1", "New Listing", "two")
    assert triage.scan_new() == 1

    assert len(said) == 2
    assert "overlaps" in said[1]
    stored = store.template_audit(connection, "new-1")
    assert stored is not None and stored.status == "needs_template"
    connection.close()
