"""Generate the two test files for Spike A (CLAUDE.md section 4.3, item 1).

Spike A asks whether an uploaded CSV/XLSX can carry image URLs into a Canva Bulk
Create image column. This script writes the files Chase uploads. It writes both
formats because the CSV-vs-XLSX difference is itself unverified — ARCHITECTURE.md
section 2.4 prefers xlsx on reasoning, not on observation.

The two rows deliberately carry *different* photos. If Bulk Create renders the
same picture twice, it reused the template placeholder and did not fetch the
URLs, which reads as a pass but is a failure.

Assumes: nothing about Canva. It only produces files.

Does not handle: uploading to Canva, or interpreting the result. A human runs the
spike; see spikes/SPIKE_A.md.

Usage:
    python spikes/make_spike_a_files.py
    python spikes/make_spike_a_files.py --photo-url URL_A --photo-url URL_B
    python spikes/make_spike_a_files.py --skip-verify   # offline
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import httpx
from openpyxl import Workbook

#: Verified 2026-08-10 by fetching both: HTTP 200, Content-Type image/jpeg,
#: ~128KB and ~167KB. They are a green mountain landscape and a canal
#: streetscape — obviously different at a glance, which is the only property
#: that matters here. Neither is a house; the question is whether Canva fetches
#: a URL into an image frame at all, not what the picture shows.
#:
#: Wikimedia Commons was the first choice and was rejected after testing: it
#: returns HTTP 403 to non-browser User-Agents and 429 to browser ones. Canva's
#: image fetcher sends its own User-Agent, so a Wikimedia URL could fail for
#: reasons having nothing to do with Bulk Create — a false negative that would
#: cost a day. Lorem Picsum does no User-Agent gating.
#:
#: Better still: pass --photo-url pointing at our own Spaces bucket. That is
#: what production will emit, so it tests the real thing.
DEFAULT_PHOTO_URLS: tuple[str, str] = (
    "https://picsum.photos/id/1018/1200/800.jpg",
    "https://picsum.photos/id/164/1200/800.jpg",
)

#: Verified from Canva's Apps SDK data-connector docs (CLAUDE.md 4.2): image
#: cell URLs are capped at 4,096 characters. Whether the *upload* path shares
#: that cap is unknown -- checking here means a long URL cannot silently become
#: the reason the spike fails.
MAX_IMAGE_URL_CHARS: int = 4096

#: Column headers. Deliberately the ones bulk_export.py would emit, so a pass
#: here transfers directly to production without Carmen remapping fields
#: (ARCHITECTURE.md 4.7 -- renaming a header breaks her saved connections).
HEADERS: tuple[str, ...] = ("address", "price", "agent_name", "photo_url")

#: Row 1's address is long on purpose: it doubles as the overflow probe for
#: CLAUDE.md 4.3 item 3.
ROW_TEXT: tuple[tuple[str, str, str], ...] = (
    ("123 Anywhere Street, Any City, ST 12345", "$1,200,000", "Jane Doe"),
    ("456 Oak Ave, Any City, ST 12345", "$845,000", "John Smith"),
)

HTTP_TIMEOUT_SECONDS: float = 20.0


def verify_photo_url(url: str, client: httpx.Client) -> str | None:
    """Check that a URL is fetchable and serves an image.

    Args:
        url: The candidate image URL.
        client: An open httpx client, so one connection serves both checks.

    Returns:
        None if the URL looks usable, otherwise a human-readable reason it does
        not. Never raises: a network failure is a finding, not a crash.
    """
    if len(url) > MAX_IMAGE_URL_CHARS:
        return f"{len(url)} chars, over Canva's documented {MAX_IMAGE_URL_CHARS} limit"
    if not url.startswith("https://"):
        # Canva's documented contract is an external HTTPS URL or a data URL.
        return "not an https:// URL"

    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        return f"request failed: {exc.__class__.__name__}: {exc}"

    if response.status_code != 200:
        return f"HTTP {response.status_code}"
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        return f"Content-Type is {content_type!r}, not an image"
    return None


def build_rows(photo_urls: tuple[str, ...]) -> list[tuple[str, ...]]:
    """Pair the fixed text rows with the supplied photo URLs.

    Raises:
        ValueError: if the number of URLs does not match the number of rows.
    """
    if len(photo_urls) != len(ROW_TEXT):
        raise ValueError(f"need exactly {len(ROW_TEXT)} photo URLs, got {len(photo_urls)}")
    return [(*text, url) for text, url in zip(ROW_TEXT, photo_urls, strict=True)]


def write_csv(path: Path, rows: list[tuple[str, ...]]) -> None:
    """Write the spike CSV. newline='' is required by the csv module on Windows."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        writer.writerows(rows)


def write_xlsx(path: Path, rows: list[tuple[str, ...]]) -> None:
    """Write the spike XLSX with every cell forced to text.

    Prices and addresses are written as strings so Excel and Canva cannot
    reinterpret "$1,200,000" as a number or an address fragment as a date —
    the reason ARCHITECTURE.md 2.4 prefers xlsx over CSV in the first place.
    """
    workbook = Workbook()
    sheet = workbook.active
    if sheet is None:  # pragma: no cover - openpyxl always creates one sheet
        raise RuntimeError("openpyxl returned a workbook with no active sheet")
    sheet.title = "Listings"
    sheet.append(list(HEADERS))
    for row in rows:
        sheet.append(list(row))
    for cells in sheet.iter_rows():
        for cell in cells:
            cell.number_format = "@"
    workbook.save(path)


def main(argv: list[str] | None = None) -> int:
    """Generate spike_a.csv and spike_a.xlsx, verifying the photo URLs first.

    Args:
        argv: Argument vector to parse. Defaults to `sys.argv[1:]`.

    Returns:
        0 on success, 1 if a photo URL failed verification.
    """
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument(
        "--photo-url",
        action="append",
        default=[],
        metavar="URL",
        help="repeat exactly twice to override the default images",
    )
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).parent / "out")
    parser.add_argument("--skip-verify", action="store_true", help="do not fetch the URLs")
    args = parser.parse_args(argv)

    urls: tuple[str, ...] = tuple(args.photo_url) if args.photo_url else DEFAULT_PHOTO_URLS
    try:
        rows = build_rows(urls)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not args.skip_verify:
        print("Verifying photo URLs before writing anything...")
        failures: list[str] = []
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            for url in urls:
                problem = verify_photo_url(url, client)
                print(f"  {'ok  ' if problem is None else 'FAIL'}  {url}")
                if problem is not None:
                    failures.append(f"{url}: {problem}")
        if failures:
            print("\nRefusing to write files — a bad URL would make Spike A", file=sys.stderr)
            print("look like a Canva failure when it is not:", file=sys.stderr)
            for failure in failures:
                print(f"  {failure}", file=sys.stderr)
            return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "spike_a.csv"
    xlsx_path = args.out_dir / "spike_a.xlsx"
    write_csv(csv_path, rows)
    write_xlsx(xlsx_path, rows)

    print(f"\nWrote {csv_path}")
    print(f"Wrote {xlsx_path}")
    print("\nNext: follow spikes/SPIKE_A.md. Upload the xlsx first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
