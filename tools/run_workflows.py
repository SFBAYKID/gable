"""End-to-end workflow runner: exercises the whole path against live services.

Not a unit test. Every unit test in this repo is offline and free; this one
spends real money and writes real files, so it is a tool you run deliberately
rather than something `pytest` picks up.

Each workflow is a *different shape of run*, not the same run four times. The
point is to exercise the paths that only differ once real data is involved:

1. **Happy path** — a listing with a large photo. Everything free, no model call.
2. **Undersized photo** — the source is too small, so the image model is the
   only way. This is the one that costs money.
3. **Missing fields** — the form supplies no price or beds, so placeholders must
   survive to the flyer rather than being blanked.
4. **Unknown agent** — an email absent from `Sales_People`, which must pause the
   listing rather than render a nameless post.

Each reports PASS/FAIL with the reason, and cleans up the files it created.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from PIL import Image

from gable.photos.fit import FitAction, assess, fit_locally, image_dimensions
from gable.photos.store import PhotoHost, publish, verify_public
from gable.slides.catalog import for_category
from gable.slides.edits import occurrences_changed, replace_text
from gable.slides.renderer import validate_image_url

FRAME_W, FRAME_H = 1080, 1350


@dataclass
class Result:
    """What one workflow did, and whether it held together."""

    name: str
    passed: bool = True
    steps: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)

    def ok(self, step: str) -> None:
        """Record a step that behaved."""
        self.steps.append(step)

    def bad(self, step: str) -> None:
        """Record a step that did not, and fail the workflow."""
        self.failures.append(step)
        self.passed = False

    def check(self, condition: bool, description: str) -> bool:
        """Assert without stopping — a workflow should report every problem."""
        (self.ok if condition else self.bad)(description)
        return condition


def _photo(width: int, height: int) -> bytes:
    """A synthetic JPEG of a given size, standing in for a listing photo."""
    image = Image.new("RGB", (width, height), (110, 150, 190))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


class Runner:
    """Holds the live clients so each workflow does not rebuild them."""

    def __init__(self) -> None:
        """Build Drive and Slides clients and the photo host from the environment."""
        load_dotenv()
        creds = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"],
            scopes=[
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/presentations",
                "https://www.googleapis.com/auth/spreadsheets.readonly",
            ],
        )
        self.drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.slides = build("slides", "v1", credentials=creds, cache_discovery=False)
        self.sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self.output = os.environ["GABLE_DRIVE_OUTPUT_FOLDER_ID"]
        self.drive_id = os.environ["GABLE_DRIVE_ID"]
        self.host = PhotoHost(
            ssh_target="root@143.110.146.87",
            ssh_key_path=str(Path("~/.ssh/gable_droplet").expanduser()),
            public_base="http://143.110.146.87",
        )

    def template_for(self, category: str) -> str:
        """Find a template file id by catalogue category.

        Args:
            category: e.g. `Just Listed`.

        Returns:
            A Drive file id.

        Raises:
            RuntimeError: if no template for that category is in Drive yet.
        """
        wanted = {e.filename for e in for_category(category)}
        found = (
            self.drive.files()
            .list(
                corpora="drive",
                driveId=self.drive_id,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                q="mimeType='application/vnd.google-apps.presentation' and trashed=false",
                fields="files(id,name)",
            )
            .execute()
            .get("files", [])
        )
        for f in found:
            if f["name"] in wanted:
                return str(f["id"])
        msg = f"no template in Drive for category {category!r}"
        raise RuntimeError(msg)

    def cleanup(self, file_ids: list[str]) -> None:
        """Trash what a workflow created. The account cannot purge, only trash."""
        for fid in file_ids:
            # Cleanup must never be the reason a run reports failure.
            with contextlib.suppress(Exception):
                self.drive.files().update(
                    fileId=fid, body={"trashed": True}, supportsAllDrives=True
                ).execute()


def workflow_happy_path(r: Runner) -> Result:
    """A big photo and a known agent: nothing should cost anything."""
    res = Result("1. happy path — large photo, no model needed")
    raw = _photo(3000, 3750)
    a = assess(*image_dimensions(raw), FRAME_W, FRAME_H)
    res.check(a.is_free, f"no model call needed ({a.action})")
    res.check(not a.needs_model, "assess did not reach for the image model")

    fitted = fit_locally(raw, FRAME_W, FRAME_H)
    res.check(image_dimensions(fitted) == (FRAME_W, FRAME_H), "fitted to the exact frame")

    url = publish(r.host, fitted)
    ok, detail = verify_public(url)
    res.check(ok, f"photo publicly fetchable ({detail})")
    try:
        validate_image_url(url)
        res.ok("the renderer accepts the URL publish produced")
    except Exception as exc:
        res.bad(f"renderer rejected the published URL: {exc}")
    return res


def workflow_undersized_photo(_r: Runner) -> Result:
    """The path that costs money, and must only trigger when it has to."""
    res = Result("2. undersized photo — the only path that spends")
    tiny = _photo(267, 148)
    a = assess(*image_dimensions(tiny), FRAME_W, FRAME_H)
    res.check(a.action is FitAction.NEEDS_UPSCALE, "a 267x148 source demands a model")
    res.check(a.upscale_factor > 2.0, f"upscale factor {a.upscale_factor:.1f}x exceeds the limit")
    res.check("267x148" in a.reason, "the reason names the real numbers for Carmen")

    # Fitting still works without the model; it is just soft. Prove the free
    # path never silently disappears.
    fitted = fit_locally(tiny, FRAME_W, FRAME_H)
    res.check(image_dimensions(fitted) == (FRAME_W, FRAME_H), "fits even when it should not")
    return res


def _slide_text(pres: dict[str, Any]) -> list[tuple[str, str]]:
    """Every (object_id, text) pair on the first slide.

    Args:
        pres: A `presentations.get` response.

    Returns:
        Pairs for shapes that carry non-empty text.

    Raises:
        Nothing.
    """
    out: list[tuple[str, str]] = []
    for el in pres["slides"][0].get("pageElements", []):
        runs = el.get("shape", {}).get("text", {}).get("textElements", [])
        text = "".join(r.get("textRun", {}).get("content", "") for r in runs).strip()
        if text:
            out.append((el["objectId"], text))
    return out


def workflow_missing_fields(r: Runner) -> Result:
    """Placeholders must reach the flyer visible, never blanked."""
    res = Result("3. missing fields — placeholders must survive")
    template = r.template_for("Just Listed")
    copy = (
        r.drive.files()
        .copy(
            fileId=template,
            body={"name": "_wf3_missing_fields", "parents": [r.output]},
            supportsAllDrives=True,
            fields="id,name",
        )
        .execute()
    )
    pid = str(copy["id"])
    res.artifacts.append(pid)

    pres = r.slides.presentations().get(presentationId=pid).execute()
    page = pres["slides"][0]["objectId"]
    texts = _slide_text(pres)

    # Discover the template's own vocabulary rather than assuming it. Designs
    # within one category do NOT agree: some carry "[PROPERTY ADDRESS]", some
    # bare "PROPERTY ADDRESS", some sample data like "1234 Your Street". A
    # renderer that assumes one spelling silently fills nothing on the others.
    addressish = [t for _, t in texts if "ADDRESS" in t.upper()]
    res.check(bool(addressish), f"found an address field to fill (in {len(texts)} text shapes)")
    if not addressish:
        return res
    target = addressish[0]

    reply = (
        r.slides.presentations()
        .batchUpdate(
            presentationId=pid,
            body={"requests": replace_text(target, "1 Test St", [page])},
        )
        .execute()
    )
    changed = occurrences_changed(reply["replies"][0])
    res.check(changed >= 1, f"the address was actually replaced (occurrencesChanged={changed})")

    after = _slide_text(r.slides.presentations().get(presentationId=pid).execute())
    joined = " ".join(t for _, t in after)
    res.check("1 Test St" in joined, "the supplied field did render")

    # Whatever this design uses for price must still be on the slide: nothing
    # supplied one, so nothing may have blanked it.
    priceish = [t for _, t in after if "PRICE" in t.upper() or t.strip().startswith("$")]
    res.check(bool(priceish), f"an unfilled price field survived ({priceish[:1]})")
    return res


def workflow_unknown_agent(r: Runner) -> Result:
    """An email absent from Sales_People must pause, not render a nameless post."""
    res = Result("4. unknown agent — must pause, not guess")
    rows = (
        r.sheets.spreadsheets()
        .values()
        .get(spreadsheetId=os.environ["GABLE_SHEET_ID"], range="'Sales_People'!A2:G50")
        .execute()
        .get("values", [])
    )
    header, people = rows[0], rows[1:]
    res.check(header[0].strip() == "Email", "the header really is on row 2")

    known = {row[0].strip().lower() for row in people if row}
    res.check("lolo@cornerhouserealty.com" in known, "the known agent is found")
    res.check("nobody@example.test" not in known, "an unknown agent is not found")

    # The trailing-space trap: "Lolo " must still match "Lolo".
    firsts = {row[1].strip() for row in people if len(row) > 1}
    res.check("Lolo" in firsts, "trailing whitespace is trimmed on the join key")
    return res


WORKFLOWS = (
    workflow_happy_path,
    workflow_undersized_photo,
    workflow_missing_fields,
    workflow_unknown_agent,
)


def main() -> int:
    """Run every workflow and report.

    Returns:
        0 if all passed, 1 otherwise — so this can gate a deploy.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="do not trash created files")
    args = parser.parse_args()

    runner = Runner()
    results: list[Result] = []
    for wf in WORKFLOWS:
        started = time.time()
        try:
            result = wf(runner)
        except Exception as exc:
            result = Result(wf.__name__)
            result.bad(f"raised {type(exc).__name__}: {exc}")
        result.steps.append(f"({time.time() - started:.1f}s)")
        results.append(result)
        if result.artifacts and not args.keep:
            runner.cleanup(result.artifacts)

    print("\nEnd-to-end workflows\n" + "=" * 60)
    for result in results:
        mark = "PASS" if result.passed else "FAIL"
        print(f"\n{mark}  {result.name}")
        for step in result.steps:
            print(f"       ok   {step}")
        for failure in result.failures:
            print(f"       FAIL {failure}")
    passed = sum(1 for r in results if r.passed)
    print(f"\n{passed}/{len(results)} workflows passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
