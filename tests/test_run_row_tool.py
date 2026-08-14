"""Regression tests for the explicit one-row operator tool.

All external clients are replaced before ``main`` runs. These tests exercise
the command's refusal and exit-code boundaries without reading Google Sheets,
posting to Slack, or opening a real database.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from gable import spend
from gable.db.schema import apply_migrations, connect
from gable.pipeline.questions import ReconcileState, Reconciliation
from gable.sheets import repository as sheet_repo

run_row_main = cast(
    Callable[[list[str] | None], int],
    importlib.import_module("tools.run_row").main,
)

BuildCall = tuple[tuple[object, ...], dict[str, object]]


class _Connection:
    """The only connection behavior ``run_row.main`` needs after stubbing."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _exercise_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    existing: object | None = None,
    result_status: str = "delivered",
    resume: bool = False,
) -> tuple[int, list[BuildCall], _Connection]:
    credential = tmp_path / "service-account.json"
    credential.write_text("{}", encoding="utf-8")
    settings = SimpleNamespace(
        log_level="INFO",
        log_format="console",
        log_redact_secrets=True,
        google_service_account_file=credential,
        sheet_id="sheet-id",
        drive_id="0Adrive",
        drive_templates_folder_id="templates",
        db_path=tmp_path / "gable.db",
        slack_bot_token="xoxb-test",
        slack_channel_id="C0B02721MNK",
        spend_ceiling_usd=int(spend.DEFAULT_CEILING_USD),
    )
    intake = SimpleNamespace(
        agent_name="Mike Kulnich",
        request_type="Sold",
        address="123 Main St",
    )
    submission = SimpleNamespace(
        response_row_id="response-47",
        sheet_row=47,
        submitted_at="2026-08-13T12:00:00Z",
        intake=intake,
        content_hash="content-hash",
        source_tab="Form Responses 1",
    )
    connection = _Connection()
    build_calls: list[BuildCall] = []
    resume_calls: list[tuple[object, object]] = []

    monkeypatch.setattr("tools.run_row.Settings.load", lambda **_kwargs: settings)
    monkeypatch.setattr("tools.run_row.configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr(
        "tools.run_row._google_clients", lambda _settings: (object(), object(), object())
    )
    monkeypatch.setattr("tools.run_row.SheetClient", lambda **_kwargs: object())
    monkeypatch.setattr(
        "tools.run_row.read_one",
        lambda _client, _tab, _row, _connection=None: submission,
    )
    monkeypatch.setattr("tools.run_row.connect", lambda _path: connection)
    monkeypatch.setattr("tools.run_row.apply_migrations", lambda _connection: 0)
    monkeypatch.setattr("tools.run_row.sync_contacts", lambda *_args: None)
    monkeypatch.setattr("tools.run_row.store.record_submission", lambda *_args: None)
    monkeypatch.setattr("tools.run_row.store.latest_run", lambda *_args: existing)

    class _Slack:
        def auth_test(self) -> dict[str, str]:
            return {"user_id": "UGABLE", "bot_id": "BGABLE"}

        def chat_postMessage(self, **kwargs: object) -> dict[str, str]:  # noqa: N802
            return {"ts": "durable"} if kwargs.get("client_msg_id") else {}

        def conversations_history(self, **_kwargs: object) -> dict[str, object]:
            return {"messages": [], "response_metadata": {"next_cursor": ""}}

        def conversations_replies(self, **_kwargs: object) -> dict[str, object]:
            return {"messages": [], "response_metadata": {"next_cursor": ""}}

    monkeypatch.setattr("tools.run_row.build_web_client", lambda _token: _Slack())

    class _Runner:
        def run(self, item: object) -> SimpleNamespace:
            assert item is submission
            return SimpleNamespace(run_id="run-test", status=result_status, said=[])

        def resume(
            self,
            item: object,
            run_id: str,
            *,
            resume_fields: dict[str, object] | None = None,
            expected_status: str | None = None,
        ) -> SimpleNamespace:
            assert item is submission
            assert run_id == "run-existing"
            resume_calls.append((resume_fields, expected_status))
            return SimpleNamespace(run_id=run_id, status=result_status, said=[])

    def _build_runner(*args: object, **kwargs: object) -> _Runner:
        build_calls.append((args, kwargs))
        return _Runner()

    monkeypatch.setattr("tools.run_row.build_runner", _build_runner)
    argv = ["Form Responses 1", "47"]
    if resume:
        argv.append("--resume")
    exit_code = run_row_main(argv)
    return exit_code, build_calls, connection


@pytest.mark.parametrize(
    ("status", "is_paused"),
    [("pending", False), ("building", False), ("needs_photo", True)],
)
def test_fresh_start_refuses_an_existing_active_or_paused_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: str,
    is_paused: bool,
) -> None:
    existing = SimpleNamespace(status=status, is_paused=is_paused)
    exit_code, build_calls, connection = _exercise_main(
        monkeypatch,
        tmp_path,
        existing=existing,
    )

    assert exit_code == 2
    assert build_calls == []
    assert connection.closed is True


def test_manual_resume_refuses_a_photo_wait_instead_of_reusing_the_rejected_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    existing = SimpleNamespace(
        status="needs_photo",
        is_paused=True,
        photo_url="http://images.example/rejected-house.jpg",
        slack_thread_ts="1786605927.301519",
        run_id="run-existing",
    )

    exit_code, build_calls, connection = _exercise_main(
        monkeypatch,
        tmp_path,
        existing=existing,
        resume=True,
    )

    assert exit_code == 2
    assert build_calls == []
    assert connection.closed is True


@pytest.mark.parametrize(
    ("status", "exit_code"), [("delivered", 0), ("needs_photo", 0), ("failed", 2)]
)
def test_result_status_controls_the_process_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: str,
    exit_code: int,
) -> None:
    actual, build_calls, connection = _exercise_main(
        monkeypatch,
        tmp_path,
        result_status=status,
    )

    assert actual == exit_code
    assert len(build_calls) == 1
    assert connection.closed is True


@pytest.mark.parametrize("resume", [False, True])
def test_manual_runs_do_not_expose_an_image_model_callback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resume: bool,
) -> None:
    """The operator path cannot invoke the retired generative upscale provider."""
    existing = (
        SimpleNamespace(
            status="needs_template",
            is_paused=True,
            photo_url="http://images.example/mike-small.jpg",
            slack_thread_ts="1786605927.301519",
            run_id="run-existing",
        )
        if resume
        else None
    )
    actual, build_calls, _connection = _exercise_main(
        monkeypatch,
        tmp_path,
        existing=existing,
        resume=resume,
    )

    assert actual == 0
    assert len(build_calls) == 1
    _args, kwargs = build_calls[0]
    assert "upscale_photo" not in kwargs
    if resume:
        assert kwargs["hero_photo_url"] == "http://images.example/mike-small.jpg"
        assert kwargs["origin_thread_ts"] == "1786605927.301519"


def test_manual_resume_does_not_treat_the_requested_thread_as_slack_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A blank Slack response cannot turn a ready file into a delivered run."""
    existing = SimpleNamespace(
        status="needs_template",
        is_paused=True,
        photo_url="http://images.example/mike-small.jpg",
        slack_thread_ts="1786605927.301519",
        run_id="run-existing",
    )
    _actual, build_calls, _connection = _exercise_main(
        monkeypatch,
        tmp_path,
        existing=existing,
        resume=True,
    )

    args, _kwargs = build_calls[0]
    say = cast(Callable[[str, str | None], str], args[4])
    assert say("Your flyer is ready.", existing.slack_thread_ts) == ""


@pytest.mark.parametrize("resume", [False, True])
def test_manual_runs_use_the_same_idempotent_question_post_as_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resume: bool,
) -> None:
    """The artificial release path cannot create a duplicate question on lost ACK."""
    existing = (
        SimpleNamespace(
            status="needs_template",
            is_paused=True,
            photo_url="http://images.example/mike-small.jpg",
            slack_thread_ts="1786605927.301519",
            run_id="run-existing",
        )
        if resume
        else None
    )
    _actual, build_calls, _connection = _exercise_main(
        monkeypatch,
        tmp_path,
        existing=existing,
        resume=resume,
    )

    _args, kwargs = build_calls[0]
    post_once = cast(Callable[[str, str | None, str], str], kwargs["post_once"])
    assert post_once("Can you send me the image?", "1786.0", "stable-id") == "durable"
    reconcile = cast(Callable[[str, str | None, str, str], Reconciliation], kwargs["reconcile"])
    assert (
        reconcile(
            "Can you send me the image?",
            "1786.0",
            "2026-08-13T00:00:00+00:00",
            "stable-id",
        ).state
        is ReconcileState.ABSENT
    )


def test_read_one_reconciles_and_selects_one_atomic_sheet_snapshot(tmp_path: Path) -> None:
    header = [
        "Timestamp",
        "Email Address",
        "Name of Agent",
        "Select your request type",
        "Property Address",
    ]
    first = [
        header,
        ["8/13/2026 09:00:00", "agent@example.com", "Agent", "Sold", "1 First St"],
        ["8/13/2026 09:00:00", "agent@example.com", "Agent", "Sold", "1 First St"],
    ]
    changed = [
        header,
        ["8/13/2026 08:00:00", "other@example.com", "Other", "Sold", "New Top"],
        *first[1:],
    ]

    class ChangingSheet:
        def __init__(self) -> None:
            self.reads = 0

        def read(self, _range: str) -> list[list[str]]:
            self.reads += 1
            return first if self.reads == 1 else changed

    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    client = ChangingSheet()

    selected = importlib.import_module("tools.run_row").read_one(
        client,
        "Form Responses 1",
        3,
        connection,
    )

    assert client.reads == 1
    assert selected.sheet_row == 3
    assert selected.intake.address == "1 First St"
    assert connection.execute("SELECT COUNT(*) AS n FROM submissions").fetchone()["n"] == 2
    assert (
        connection.execute("SELECT COUNT(*) AS n FROM submission_source_rows").fetchone()["n"] == 2
    )
    assert (
        selected.response_row_id
        == sheet_repo.reconcile_submissions(
            connection,
            sheet_repo.parse_submissions(first, "Form Responses 1"),
        )[1].response_row_id
    )
