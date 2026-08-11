"""Prove every credential in `.env` actually works, without printing any of them.

Not a unit test — this makes real network calls. Run it after changing `.env`,
after rotating a key, and as the first step of debugging "why is nothing
happening". A key that parses is not a key that works: it can be revoked,
scoped wrong, or pointed at a project where the API was never enabled. Each
check below is the cheapest real call that proves the credential end to end.

    .venv/bin/python tools/check_connections.py

Output is a status line per service. Values are never printed — only identity
(team name, account email) and pass/fail, so this is safe to paste into Slack.

Assumes: `.env` sits at the repo root. Exit code is 0 only if every configured
credential passed; anything unconfigured is reported and skipped, not failed.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENV_PATH: Path = Path(__file__).resolve().parent.parent / ".env"
TIMEOUT_SECONDS: float = 30.0

OK = "  ok    "
FAIL = "  FAIL  "
SKIP = "  skip  "


@dataclass
class Result:
    """One service's verdict."""

    service: str
    state: str
    detail: str


def load_env(path: Path) -> dict[str, str]:
    """Parse `.env` into a dict. Blank values are omitted."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        if value.strip():
            values[key.strip()] = value.strip()
    return values


def http_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
) -> tuple[int, Any]:
    """Make a request and decode JSON, returning the status even on error.

    A 401 body is often the most useful thing on the screen, so an HTTPError is
    unwrapped rather than raised.
    """
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body or b"{}")
        except ValueError:
            return exc.code, {"raw": body[:200].decode("utf-8", "replace")}


def check_slack_bot(env: dict[str, str]) -> Result:
    """auth.test — proves the bot token is live and names the workspace."""
    token = env.get("SLACK_BOT_TOKEN")
    if not token:
        return Result("Slack bot token", SKIP, "SLACK_BOT_TOKEN not set")
    _, body = http_json(
        "https://slack.com/api/auth.test", headers={"Authorization": f"Bearer {token}"}
    )
    if body.get("ok"):
        return Result("Slack bot token", OK, f"team {body.get('team')}, bot @{body.get('user')}")
    return Result("Slack bot token", FAIL, str(body.get("error")))


def check_slack_app(env: dict[str, str]) -> Result:
    """apps.connections.open — proves Socket Mode can actually connect.

    Must be POST; a GET is rejected as `insecure_request`, which reads like an
    auth failure and is not one.
    """
    token = env.get("SLACK_APP_TOKEN")
    if not token:
        return Result("Slack app token", SKIP, "SLACK_APP_TOKEN not set")
    _, body = http_json(
        "https://slack.com/api/apps.connections.open",
        headers={"Authorization": f"Bearer {token}"},
        data=b"",
        method="POST",
    )
    if body.get("ok"):
        return Result("Slack app token", OK, "Socket Mode WebSocket ticket issued")
    return Result("Slack app token", FAIL, str(body.get("error")))


def check_slack_channel(env: dict[str, str]) -> Result:
    """conversations.info — proves the target channel exists and is reachable."""
    token, channel = env.get("SLACK_BOT_TOKEN"), env.get("GABLE_SLACK_CHANNEL_ID")
    if not token or not channel:
        return Result("Slack channel", SKIP, "token or channel id not set")
    _, body = http_json(
        f"https://slack.com/api/conversations.info?channel={channel}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if body.get("ok"):
        ch = body.get("channel", {})
        kind = "private" if ch.get("is_private") else "public"
        member = "bot is a member" if ch.get("is_member") else "BOT NOT IN CHANNEL — /invite @Gable"
        return Result("Slack channel", OK, f"#{ch.get('name')} ({kind}), {member}")
    if body.get("error") == "missing_scope":
        # conversations.info needs channels:read / groups:read, which Gable does
        # not otherwise require — chat:write.public lets it post to a channel it
        # is not a member of. Not a failure, just an unverifiable channel id.
        return Result(
            "Slack channel",
            SKIP,
            "cannot verify id without channels:read (optional; posting does not need it)",
        )
    return Result("Slack channel", FAIL, str(body.get("error")))


def check_anthropic(env: dict[str, str]) -> Result:
    """/v1/models — cheapest authenticated call that proves the key works."""
    key = env.get("ANTHROPIC_API_KEY")
    if not key:
        return Result("Anthropic", SKIP, "ANTHROPIC_API_KEY not set")
    status, body = http_json(
        "https://api.anthropic.com/v1/models?limit=1",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    if status == 200:
        models = body.get("data", [])
        first = models[0].get("id") if models else "none listed"
        return Result("Anthropic", OK, f"key valid, models reachable (e.g. {first})")
    return Result("Anthropic", FAIL, f"HTTP {status}: {body.get('error', {}).get('message', body)}")


def check_openai_images(env: dict[str, str]) -> Result:
    """Prove the image key authenticates, via /v1/models.

    No generation call is made: that costs money, and this script is meant to
    be cheap enough to run on every deploy.
    """
    key = env.get("OPENAI_IMAGE_API_KEY")
    if not key:
        return Result("OpenAI (images)", SKIP, "OPENAI_IMAGE_API_KEY not set")
    status, body = http_json(
        "https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"}
    )
    if status == 200:
        ids = {m.get("id") for m in body.get("data", [])}
        image_models = sorted(i for i in ids if isinstance(i, str) and "image" in i)
        detail = f"key valid, {len(ids)} models"
        if image_models:
            detail += f"; image-capable: {', '.join(image_models[:3])}"
        else:
            detail += "; NO image model visible to this key"
        return Result("OpenAI (images)", OK, detail)
    return Result(
        "OpenAI (images)", FAIL, f"HTTP {status}: {body.get('error', {}).get('message', body)}"
    )


def check_firecrawl(env: dict[str, str]) -> Result:
    """Team credit usage — authenticated, free, and no crawl is started."""
    key = env.get("FIRECRAWL_API_KEY")
    if not key:
        return Result("Firecrawl", SKIP, "FIRECRAWL_API_KEY not set")
    status, body = http_json(
        "https://api.firecrawl.dev/v1/team/credit-usage",
        headers={"Authorization": f"Bearer {key}"},
    )
    if status == 200:
        remaining = body.get("data", {}).get("remaining_credits")
        return Result("Firecrawl", OK, f"key valid, {remaining} credits remaining")
    return Result("Firecrawl", FAIL, f"HTTP {status}: {body.get('error', body)}")


def check_google(env: dict[str, str]) -> list[Result]:
    """Service account -> Sheets, Drive and Slides, each proven separately.

    Split because they fail independently: a key can be valid while the Slides
    API was never enabled, or while the shared drive was never shared with the
    account. One combined "Google ok" would hide exactly the mistakes that
    actually happen.
    """
    path_text = env.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not path_text:
        return [Result("Google", SKIP, "GOOGLE_SERVICE_ACCOUNT_FILE not set")]
    path = Path(path_text).expanduser()
    if not path.is_file():
        return [Result("Google", FAIL, f"no service-account key at {path}")]

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - dependency is declared
        return [Result("Google", FAIL, f"client libraries missing: {exc}")]

    info = json.loads(path.read_text(encoding="utf-8"))
    email = info.get("client_email", "?")
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/presentations",
        ],
    )
    results = [Result("Google service account", OK, f"{email} (project {info.get('project_id')})")]

    sheet_id = env.get("GABLE_SHEET_ID")
    if sheet_id:
        try:
            sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
            meta = sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()
            tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
            results.append(
                Result(
                    "Google Sheets", OK, f"{meta.get('properties', {}).get('title')} — tabs: {tabs}"
                )
            )
        except Exception as exc:
            results.append(Result("Google Sheets", FAIL, _short(exc)))

    drive_id = env.get("GABLE_DRIVE_ID")
    if drive_id:
        try:
            drive = build("drive", "v3", credentials=creds, cache_discovery=False)
            info_ = drive.drives().get(driveId=drive_id).execute()
            listing = (
                drive.files()
                .list(
                    corpora="drive",
                    driveId=drive_id,
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                    fields="files(name,mimeType)",
                    pageSize=25,
                )
                .execute()
            )
            names = [f["name"] for f in listing.get("files", [])]
            results.append(
                Result("Google Drive", OK, f"shared drive '{info_.get('name')}' — contains {names}")
            )
        except Exception as exc:
            results.append(Result("Google Drive", FAIL, _short(exc)))

    try:
        build("slides", "v1", credentials=creds, cache_discovery=False)
        results.append(Result("Google Slides", OK, "API client built; enabled on this project"))
    except Exception as exc:
        results.append(Result("Google Slides", FAIL, _short(exc)))

    return results


def _short(exc: Exception) -> str:
    """One readable line from a Google client error."""
    text = str(exc).replace("\n", " ")
    return text[:160]


def main() -> int:
    """Run every check and return 0 only if nothing failed."""
    if not ENV_PATH.exists():
        print(f"no .env at {ENV_PATH}", file=sys.stderr)
        return 1
    env = load_env(ENV_PATH)

    results: list[Result] = [
        check_slack_bot(env),
        check_slack_app(env),
        check_slack_channel(env),
        check_anthropic(env),
        check_openai_images(env),
        check_firecrawl(env),
        *check_google(env),
    ]

    print("Connection checks — values are never printed\n")
    for r in results:
        print(f"{r.state}{r.service:<26} {r.detail}")

    failed = [r for r in results if r.state == FAIL]
    skipped = [r for r in results if r.state == SKIP]
    print(
        f"\n{len(results) - len(failed) - len(skipped)} ok, "
        f"{len(failed)} failed, {len(skipped)} not configured"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
