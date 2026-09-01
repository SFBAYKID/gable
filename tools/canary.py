"""Build one test flyer from a named design, report it, and trash the copy.

The scheduled scan runs this automatically when a design is added or edited;
this tool runs the same build on demand, so a design can be exercised in the
playground without editing it. It prints the report and, with `--post`, sends
it to the configured Slack channel as a top-level message — run it with
`GABLE_SLACK_CHANNEL_ID` overridden on the command line so that channel is
the playground, never a design's production thread.

Costs Drive writes and no model spend. The copy is moved to the trash in every
case the build gets far enough to make one.
"""

from __future__ import annotations

import argparse
import sys
from typing import Final

from gable.config import ConfigError, Settings
from gable.google_client import build_google_service
from gable.pipeline.canary_live import dry_builder
from gable.slackapp.client import build_web_client
from gable.slides.library import list_files
from gable.voice import is_clean

SCOPES: Final[tuple[str, ...]] = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/presentations",
)


def main(argv: list[str] | None = None) -> int:
    """Build the named design once and print or post what it showed."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", required=True, help="exact design name in Generic Templates")
    parser.add_argument("--post", action="store_true", help="post the report to the channel")
    args = parser.parse_args(argv)
    try:
        settings = Settings.load()
    except ConfigError as exc:
        print(f"configuration problem: {exc}", file=sys.stderr)
        return 2
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        str(settings.google_service_account_file), scopes=list(SCOPES)
    )
    drive = build_google_service("drive", "v3", credentials)
    slides = build_google_service("slides", "v1", credentials)
    sources = [
        item
        for item in list_files(drive, settings.drive_id, settings.drive_templates_folder_id)
        if item.name.strip().casefold() == args.source.strip().casefold() and item.is_slides
    ]
    if len(sources) != 1:
        print(f"found {len(sources)} design(s) named {args.source!r}", file=sys.stderr)
        return 2
    report = dry_builder(settings, drive, slides)(sources[0])
    text = (
        report or f"I built a test flyer from the {sources[0].name} design and found nothing wrong."
    )
    print(text)
    if args.post:
        if not is_clean(text):
            print("refusing to post a message that breaks the house style", file=sys.stderr)
            return 2
        client = build_web_client(settings.slack_bot_token)
        client.chat_postMessage(channel=settings.slack_channel_id, text=text)
        print(f"posted to {settings.slack_channel_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
