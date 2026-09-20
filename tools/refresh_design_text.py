"""Capture every live design's text, so an edit that breaks a slot fails the suite.

`tests/fixtures/live_design_text.json` is what the six designs in Generic
Templates actually say right now. `tests/test_live_design_text.py` holds the
code to it: every slot a design displays must resolve to a field, because a
slot that does not resolve is never replaced and the design's own sample — a
real agent's name, cell or address — prints on somebody else's flyer.

That is not hypothetical. Carmen edited three designs on 2026-08-26 and their
sample agent changed with them; nothing noticed until a rehearsal flyer built
for Andy Jang came back carrying Lina Mariner's name above Andy's phone, email
and face, on 2026-09-20. The unit suite was green and the canary, which builds
the same design, reported nothing wrong — it checks fields, frames and geometry,
not whether every slot was filled.

Run this after any design edit, and read the diff:

    PYTHONPATH=src .venv/bin/python tools/refresh_design_text.py

It reads through the service account and never writes to Drive.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from google.oauth2 import service_account

from gable.config import Settings
from gable.google_client import build_google_service
from gable.slides.library import list_files

#: Read-only: this tool must never be able to change a design.
SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/presentations.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
)

FIXTURE: Path = Path(__file__).resolve().parent.parent / "tests/fixtures/live_design_text.json"


def design_text(slides: Any, file_id: str) -> list[str]:  # noqa: ANN401 - discovery resource
    """Return every non-empty top-level text box on a design's only slide.

    Args:
        slides: A Slides v1 resource.
        file_id: The design to read.

    Returns:
        The text of each box, in the order the page lists them.

    Raises:
        KeyError: If the response omits the slide list, which would mean the
            Slides contract changed under us.
    """
    presentation = slides.presentations().get(presentationId=file_id).execute()
    found: list[str] = []
    for element in presentation["slides"][0]["pageElements"]:
        text = element.get("shape", {}).get("text")
        if not text:
            continue
        written = "".join(
            run.get("textRun", {}).get("content", "") for run in text.get("textElements", [])
        ).strip()
        if written:
            found.append(written)
    return found


def main() -> int:
    """Rewrite the fixture from the live designs and say what it holds.

    Returns:
        0 always; the diff is the output, and `git diff` is how it is read.

    Raises:
        ConfigError: If the environment is not configured.
        Exception: Google client errors propagate; a partial read is not a
            fixture worth committing.
    """
    settings = Settings.load()
    credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        str(settings.google_service_account_file), scopes=list(SCOPES)
    )
    slides = build_google_service("slides", "v1", credentials)
    drive = build_google_service("drive", "v3", credentials)
    captured = {
        item.name: design_text(slides, item.file_id)
        for item in sorted(
            list_files(drive, settings.drive_id, settings.drive_templates_folder_id),
            key=lambda item: item.name,
        )
    }
    FIXTURE.write_text(json.dumps(captured, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, texts in captured.items():
        print(f"{name}: {len(texts)} text box(es)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
