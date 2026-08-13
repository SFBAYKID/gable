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
        reprocessing_enabled=True,
        max_image_calls_per_listing=1,
        openai_image_api_key="image-key",
        image_model_hq="gpt-image-2",
    )
    intake = SimpleNamespace(
        agent_name="Mike Clunch",
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

    monkeypatch.setattr("tools.run_row.Settings.load", lambda **_kwargs: settings)
    monkeypatch.setattr("tools.run_row.configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr(
        "tools.run_row._google_clients", lambda _settings: (object(), object(), object())
    )
    monkeypatch.setattr("tools.run_row.SheetClient", lambda **_kwargs: object())
    monkeypatch.setattr("tools.run_row.read_one", lambda _client, _tab, _row: submission)
    monkeypatch.setattr("tools.run_row.connect", lambda _path: connection)
    monkeypatch.setattr("tools.run_row.apply_migrations", lambda _connection: 0)
    monkeypatch.setattr("tools.run_row.sync_contacts", lambda *_args: None)
    monkeypatch.setattr("tools.run_row.repo.reconcile_identity", lambda _connection, item: item)
    monkeypatch.setattr("tools.run_row.store.record_submission", lambda *_args: None)
    monkeypatch.setattr("tools.run_row.store.latest_run", lambda *_args: existing)

    import slack_sdk

    class _Slack:
        def chat_postMessage(self, **_kwargs: object) -> dict[str, str]:  # noqa: N802
            return {}

    monkeypatch.setattr(slack_sdk, "WebClient", lambda **_kwargs: _Slack())

    class _Runner:
        def run(self, item: object) -> SimpleNamespace:
            assert item is submission
            return SimpleNamespace(run_id="run-test", status=result_status, said=[])

        def resume(self, item: object, run_id: str) -> SimpleNamespace:
            assert item is submission
            assert run_id == "run-existing"
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
def test_manual_runs_use_the_guarded_photo_enlargement_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resume: bool,
) -> None:
    """A tiny retained upload gets the same budgeted edit as a Slack handoff."""
    guarded_calls: list[tuple[object, ...]] = []

    def guarded(*args: object, **kwargs: object) -> bytes:
        guarded_calls.append((*args, kwargs))
        return b"enhanced"

    monkeypatch.setattr("tools.run_row.guarded_upscale_photo", guarded)
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
    upscale = cast(Callable[[str, bytes, int, int], bytes], kwargs["upscale_photo"])
    assert upscale("run-existing", b"small", 1078, 504) == b"enhanced"
    guarded_args = guarded_calls[0]
    assert guarded_args[1:5] == ("run-existing", b"small", 1078, 504)
    assert guarded_args[-1] == {
        "enabled": True,
        "max_calls": 1,
        "api_key": "image-key",
        "model": "gpt-image-2",
    }
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
