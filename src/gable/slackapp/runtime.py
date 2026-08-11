"""Production assembly for Slack Socket Mode and the Sheet poller.

All Slack dependencies stay under ``gable.slackapp``. The generic lifecycle in
``gable.runtime`` knows only that one listener connects without blocking and
that the poll loop stays on the main thread. Importing this module performs no
I/O; ``build_components`` constructs clients only after settings validate.
"""

from __future__ import annotations

import logging
import sys

from gable.config import ConfigError, Settings
from gable.db.schema import apply_migrations, connect
from gable.logging_setup import configure_logging
from gable.pipeline.live import build_runner
from gable.pipeline.poller import Poller
from gable.runtime import RuntimeComponents, serve
from gable.sheets import repository as repo
from gable.sheets.client import SheetClient
from gable.slackapp.app import build_app

logger = logging.getLogger("gable.slack.runtime")

GOOGLE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/presentations",
)


def build_components(settings: Settings) -> RuntimeComponents:
    """Construct the real database, Google, Slack, runner and poller clients.

    Args:
        settings: Validated production settings.

    Returns:
        Resources ready for the shared runtime lifecycle.

    Raises:
        Exception: when a credential or external client cannot be constructed.
            Startup logs this through the mandatory redacting formatter.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    connection = connect(settings.db_path)
    apply_migrations(connection)

    # Vendor contract: service-account credentials accept an explicit scope
    # list. https://google-auth.readthedocs.io/en/latest/reference/google.oauth2.service_account.html
    credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        str(settings.google_service_account_file), scopes=list(GOOGLE_SCOPES)
    )
    # Discovery clients are reused by sequential poll callbacks on the main
    # thread. https://googleapis.github.io/google-api-python-client/docs/epy/googleapiclient.discovery-module.html
    sheets = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    slides = build("slides", "v1", credentials=credentials, cache_discovery=False)
    sheet_client = SheetClient(spreadsheet_id=settings.sheet_id, service=sheets)

    app = build_app()

    def slack_post(text: str, thread_ts: str | None) -> str:
        """Post only to Gable's configured channel and return the message id."""
        response = app.client.chat_postMessage(
            channel=settings.slack_channel_id,
            text=text,
            thread_ts=thread_ts,
        )
        return str(response.get("ts") or thread_ts or "")

    def on_submission(submission: repo.Submission) -> None:
        """Give one new submission a fresh runner bound to the live clients."""
        runner = build_runner(settings, connection, drive, slides, slack_post)
        runner.run(submission)

    poller = Poller(
        client=sheet_client,
        connection=connection,
        responses_tab=settings.tab_responses,
        salespeople_tab=settings.tab_agents,
        on_submission=on_submission,
        schedule=settings.poll_schedule,
        max_per_pass=settings.max_batch,
    )
    socket = SocketModeHandler(app, settings.slack_app_token)
    return RuntimeComponents(poller=poller, socket=socket, connection=connection)


def main() -> int:
    """Validate configuration, build clients, and start the production process."""
    try:
        settings = Settings.load()
    except ConfigError as exc:
        # ConfigError never includes secret values. Logging is not configured
        # yet because log settings are part of the object that failed to load.
        print(str(exc), file=sys.stderr)
        return 2

    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        redact_secrets=settings.log_redact_secrets,
    )
    try:
        components = build_components(settings)
    except Exception:
        logger.exception("Gable could not construct its runtime clients")
        return 2
    return serve(components)


if __name__ == "__main__":
    sys.exit(main())
