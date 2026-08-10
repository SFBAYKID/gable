"""Canva Connect API — Autofill feasibility spike.

PURPOSE
-------
Answer one question definitively: on a Canva Teams plan, can we call the
Autofill API, and how many trial uses do we get before the Enterprise gate
closes?

This script walks the full happy path:

    1. OAuth 2.0 Authorization Code flow with PKCE (S256).
    2. GET  /v1/brand-templates              -> pick a template
    3. GET  /v1/brand-templates/{id}/dataset -> discover its data fields
    4. POST /v1/autofills                    -> submit the fill job
    5. GET  /v1/autofills/{jobId}            -> poll until done
    6. Print the resulting design URL and any `trial_information`.

DESIGN NOTE — WHY THIS DUMPS RAW JSON
-------------------------------------
The endpoint URLs, HTTP methods, scope names, and token semantics below were
read directly from Canva's published documentation (see REFERENCES). The exact
*response body shapes* were NOT verified against a live API by the author of
this script. Rather than guess at nested key names and fail with a confusing
KeyError, every step prints the raw JSON response when `--verbose` is set, and
all field access goes through `dig()`, which returns None instead of raising.

If a shape assumption is wrong, you will see the real payload and can correct
the accessor in one line. Do not "clean this up" by assuming shapes until you
have seen real responses.

REFERENCES
----------
Authentication:   https://www.canva.dev/docs/connect/authentication/
Token endpoint:   https://www.canva.dev/docs/connect/api-reference/authentication/generate-access-token/
Scopes:           https://www.canva.dev/docs/connect/appendix/scopes/
Autofill:         https://www.canva.dev/docs/connect/api-reference/autofills/
Autofill guide:   https://www.canva.dev/docs/connect/autofill-guide/
Brand templates:  https://www.canva.dev/docs/connect/api-reference/brand-templates/

REQUIREMENTS
------------
    pip install requests

    Environment variables (or a .env-style export before running):
        CANVA_CLIENT_ID
        CANVA_CLIENT_SECRET

    In the Canva Developer Portal, the integration must have this exact
    redirect URL registered:
        http://127.0.0.1:8910/callback

USAGE
-----
    python canva_autofill_spike.py login
    python canva_autofill_spike.py templates
    python canva_autofill_spike.py dataset  --template-id <ID>
    python canva_autofill_spike.py autofill --template-id <ID> --field name=value ...
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - guidance for a missing dependency
    sys.exit("Missing dependency. Run: pip install requests")


# ---------------------------------------------------------------------------
# Constants — all taken verbatim from Canva's published documentation.
# ---------------------------------------------------------------------------

AUTHORIZE_URL: str = "https://www.canva.com/api/oauth/authorize"
TOKEN_URL: str = "https://api.canva.com/rest/v1/oauth/token"
API_BASE: str = "https://api.canva.com/rest/v1"

#: Loopback redirect. Must match the Developer Portal entry byte for byte.
REDIRECT_HOST: str = "127.0.0.1"
REDIRECT_PORT: int = 8910
REDIRECT_URI: str = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}/callback"

#: Scopes needed for the full autofill flow. Names verified against
#: https://www.canva.dev/docs/connect/appendix/scopes/
SCOPES: tuple[str, ...] = (
    "design:content:write",
    "design:meta:read",
    "brandtemplate:meta:read",
    "brandtemplate:content:read",
    "asset:read",
    "asset:write",
    "profile:read",
)

#: Where refresh/access tokens are cached between runs.
TOKEN_FILE: Path = Path(__file__).with_name(".canva_tokens.json")

#: Autofill jobs are asynchronous; these bound the polling loop.
POLL_INTERVAL_SECONDS: float = 2.0
POLL_TIMEOUT_SECONDS: float = 120.0


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def dig(payload: Any, *keys: str) -> Any:
    """Walk nested dict keys, returning None rather than raising on a miss.

    Used everywhere response shapes are unverified. `dig(r, "job", "status")`
    is safe even if `job` is absent or is a list.
    """
    current = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def show(label: str, payload: Any, verbose: bool) -> None:
    """Pretty-print a JSON payload under a labelled banner when verbose."""
    if not verbose:
        return
    print(f"\n----- {label} -----")
    print(json.dumps(payload, indent=2, sort_keys=True)[:8000])
    print("-" * (12 + len(label)))


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PkcePair:
    """A PKCE verifier/challenge pair using the S256 method.

    Canva requires the verifier to be 43-128 characters of high-entropy
    random data, and the challenge to be the URL-safe base64 (unpadded)
    encoding of its SHA-256 digest.
    """

    verifier: str
    challenge: str

    @classmethod
    def generate(cls) -> PkcePair:
        """Generate a fresh verifier/challenge pair. Never reuse one across flows."""
        # token_urlsafe(96) yields ~128 chars, comfortably inside Canva's range.
        verifier = secrets.token_urlsafe(96)[:128]
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return cls(verifier=verifier, challenge=challenge)


# ---------------------------------------------------------------------------
# Loopback callback server
# ---------------------------------------------------------------------------


@dataclass
class CallbackResult:
    """Captured query parameters from Canva's redirect back to localhost."""

    code: str | None = None
    state: str | None = None
    error: str | None = None
    error_description: str | None = None


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Single-shot handler that records the OAuth redirect parameters."""

    result: CallbackResult  # injected by the factory below
    done: threading.Event

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)
        self.result.code = _first(params.get("code"))
        self.result.state = _first(params.get("state"))
        self.result.error = _first(params.get("error"))
        self.result.error_description = _first(params.get("error_description"))

        body = (
            b"<html><body style='font-family:sans-serif;padding:3rem'>"
            b"<h2>Canva authorization received.</h2>"
            b"<p>You can close this tab and return to the terminal.</p>"
            b"</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.done.set()

    def log_message(self, *_args: Any) -> None:
        """Silence the default stderr access log."""
        return


def _first(values: Sequence[str] | None) -> str | None:
    """Return the first element of a parse_qs list, or None."""
    return values[0] if values else None


def wait_for_callback(timeout_seconds: float = 300.0) -> CallbackResult:
    """Serve exactly one request on the loopback redirect URI and return it."""
    result = CallbackResult()
    done = threading.Event()

    handler = type(
        "_BoundCallbackHandler",
        (_CallbackHandler,),
        {"result": result, "done": done},
    )
    server = http.server.HTTPServer((REDIRECT_HOST, REDIRECT_PORT), handler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not done.wait(timeout=timeout_seconds):
            raise TimeoutError(
                f"No OAuth redirect received within {timeout_seconds:.0f}s. "
                f"Confirm {REDIRECT_URI} is registered in the Developer Portal."
            )
    finally:
        server.shutdown()
        server.server_close()
    return result


# ---------------------------------------------------------------------------
# Token handling
# ---------------------------------------------------------------------------


@dataclass
class Credentials:
    """Client ID/secret pulled from the environment."""

    client_id: str
    client_secret: str

    @classmethod
    def from_env(cls) -> Credentials:
        """Read the client ID/secret from the environment, exiting if either is unset.

        Raises:
            SystemExit: if either variable is missing or blank.
        """
        client_id = os.environ.get("CANVA_CLIENT_ID", "").strip()
        client_secret = os.environ.get("CANVA_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            sys.exit(
                "Set CANVA_CLIENT_ID and CANVA_CLIENT_SECRET before running.\n"
                "Both come from your integration in the Canva Developer Portal."
            )
        return cls(client_id=client_id, client_secret=client_secret)

    def basic_auth_header(self) -> str:
        """Build the HTTP Basic credential Canva recommends for /oauth/token."""
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")


@dataclass
class TokenSet:
    """Access + refresh tokens, with a locally computed expiry timestamp."""

    access_token: str
    refresh_token: str | None
    expires_at: float
    scope: str | None = None

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> TokenSet:
        """Build a TokenSet from an /oauth/token response body.

        Raises:
            KeyError: if the response carries no `access_token`.
        """
        # `expires_in` is documented as seconds (currently 14400 = 4 hours).
        # 60s of slack avoids using a token that expires mid-request.
        expires_in = float(payload.get("expires_in", 0) or 0)
        return cls(
            access_token=str(payload["access_token"]),
            refresh_token=payload.get("refresh_token"),
            expires_at=time.time() + max(expires_in - 60.0, 0.0),
            scope=payload.get("scope"),
        )

    @property
    def is_expired(self) -> bool:
        """True once the access token is inside its 60-second safety margin."""
        return time.time() >= self.expires_at

    def save(self, path: Path = TOKEN_FILE) -> None:
        """Write the token set to disk, owner-readable only."""
        path.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")
        # Tokens are credentials; keep them owner-readable only.
        # Non-POSIX filesystems cannot chmod; not worth failing the run over.
        with contextlib.suppress(OSError):
            path.chmod(0o600)

    @classmethod
    def load(cls, path: Path = TOKEN_FILE) -> TokenSet | None:
        """Load cached tokens, returning None if absent or unreadable."""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return None


def exchange_code(creds: Credentials, code: str, verifier: str) -> TokenSet:
    """Trade an authorization code for tokens (grant_type=authorization_code)."""
    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": creds.basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            # Required because we supplied redirect_uri on the authorize call.
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )
    _raise_for_api_error(response, "token exchange")
    return TokenSet.from_response(response.json())


def refresh_tokens(creds: Credentials, refresh_token: str) -> TokenSet:
    """Rotate tokens. NOTE: Canva refresh tokens are single-use.

    The response carries a NEW refresh token which must replace the old one on
    disk, or the next refresh will fail.
    """
    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": creds.basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30,
    )
    _raise_for_api_error(response, "token refresh")
    return TokenSet.from_response(response.json())


def login(creds: Credentials, verbose: bool) -> TokenSet:
    """Run the interactive PKCE authorization flow and persist the result."""
    pkce = PkcePair.generate()
    state = secrets.token_urlsafe(24)

    query = urllib.parse.urlencode(
        {
            "code_challenge": pkce.challenge,
            "code_challenge_method": "S256",
            "scope": " ".join(SCOPES),
            "response_type": "code",
            "client_id": creds.client_id,
            "state": state,
            "redirect_uri": REDIRECT_URI,
        }
    )
    url = f"{AUTHORIZE_URL}?{query}"

    print("Opening your browser to authorize this integration.")
    print("If it does not open, paste this URL manually:\n")
    print(url, "\n")
    # Headless environments raise here; the printed URL above is the fallback.
    with contextlib.suppress(Exception):
        webbrowser.open(url)

    result = wait_for_callback()
    if result.error:
        sys.exit(f"Authorization failed: {result.error} — {result.error_description}")
    if result.state != state:
        # Mismatched state means the redirect did not originate from our request.
        sys.exit("State mismatch on OAuth callback. Aborting for safety.")
    if not result.code:
        sys.exit("No authorization code in callback.")

    tokens = exchange_code(creds, result.code, pkce.verifier)
    tokens.save()
    show("TOKEN RESPONSE (redacted)", {"scope": tokens.scope}, verbose)
    print(f"Authorized. Tokens cached at {TOKEN_FILE}")
    return tokens


def get_valid_tokens(creds: Credentials, verbose: bool) -> TokenSet:
    """Load cached tokens, refreshing or re-authorizing as needed."""
    tokens = TokenSet.load()
    if tokens is None:
        return login(creds, verbose)
    if not tokens.is_expired:
        return tokens
    if not tokens.refresh_token:
        return login(creds, verbose)

    try:
        refreshed = refresh_tokens(creds, tokens.refresh_token)
    except RuntimeError as exc:
        print(f"Refresh failed ({exc}); falling back to full re-authorization.")
        return login(creds, verbose)
    refreshed.save()
    return refreshed


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


def _raise_for_api_error(response: requests.Response, context: str) -> None:
    """Turn a non-2xx response into a RuntimeError carrying the response body.

    Canva returns a JSON error envelope; surfacing it verbatim is far more
    useful during a spike than a bare status code.
    """
    if response.ok:
        return
    try:
        detail = json.dumps(response.json(), indent=2)
    except ValueError:
        detail = response.text[:2000]
    raise RuntimeError(f"{context} failed [HTTP {response.status_code}]:\n{detail}")


@dataclass
class CanvaClient:
    """Thin authenticated wrapper over the Connect API."""

    tokens: TokenSet
    verbose: bool = False
    session: requests.Session = field(default_factory=requests.Session)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.tokens.access_token}",
            "Content-Type": "application/json",
        }

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """GET a Connect API path and return the decoded body.

        Raises:
            RuntimeError: on any non-2xx response, carrying Canva's error body.
        """
        response = self.session.get(
            f"{API_BASE}{path}", headers=self._headers(), params=params, timeout=30
        )
        _raise_for_api_error(response, f"GET {path}")
        payload = response.json()
        show(f"GET {path}", payload, self.verbose)
        return payload

    def post(self, path: str, body: Mapping[str, Any]) -> Any:
        """POST a JSON body to a Connect API path and return the decoded response.

        Raises:
            RuntimeError: on any non-2xx response, carrying Canva's error body.
        """
        response = self.session.post(
            f"{API_BASE}{path}", headers=self._headers(), json=body, timeout=60
        )
        _raise_for_api_error(response, f"POST {path}")
        payload = response.json()
        show(f"POST {path}", payload, self.verbose)
        return payload


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def list_brand_templates(client: CanvaClient) -> list[dict[str, Any]]:
    """Fetch brand templates visible to the authorized user.

    SHAPE ASSUMPTION (unverified): the response contains an `items` list whose
    entries carry `id` and `title`. Run with --verbose to see the real payload.
    """
    payload = client.get("/brand-templates")
    items = dig(payload, "items")
    if not isinstance(items, list):
        print(
            "Could not find an 'items' list in the response. "
            "Raw payload follows — adjust list_brand_templates() to match."
        )
        print(json.dumps(payload, indent=2)[:4000])
        return []
    return [item for item in items if isinstance(item, dict)]


def get_template_dataset(client: CanvaClient, template_id: str) -> dict[str, Any]:
    """Fetch the autofillable data fields defined on a brand template.

    SHAPE ASSUMPTION (unverified): `{"dataset": {"<field>": {"type": "text"}}}`.
    """
    payload = client.get(f"/brand-templates/{template_id}/dataset")
    dataset = dig(payload, "dataset")
    if not isinstance(dataset, dict):
        print("No 'dataset' object found. Raw payload:")
        print(json.dumps(payload, indent=2)[:4000])
        return {}
    return dataset


def build_autofill_data(dataset: Mapping[str, Any], overrides: Mapping[str, str]) -> dict[str, Any]:
    """Assemble the `data` object for POST /autofills.

    Text fields take `{"type": "text", "text": ...}`. Image fields take either
    an `asset_id` or an `image_upload` object with a URL — this spike only
    handles text, because images need either a prior asset upload or the
    (documented but untested here) image_upload variant.

    Fields present in the dataset but absent from `overrides` are filled with a
    visible placeholder so you can see exactly which slots mapped where.
    """
    data: dict[str, Any] = {}
    for name, spec in dataset.items():
        field_type = dig(spec, "type") or "text"
        if field_type == "text":
            data[name] = {"type": "text", "text": overrides.get(name, f"<{name}>")}
        else:
            # Skipped deliberately rather than guessed at. See docstring.
            print(f"  ! Skipping non-text field '{name}' (type={field_type})")
    return data


def create_autofill(
    client: CanvaClient, template_id: str, data: Mapping[str, Any], title: str
) -> Any:
    """Submit the autofill job and return the raw, undecoded response payload.

    The return type is deliberately `Any`: this spike exists precisely because
    the response shape has never been observed. Annotating `dict[str, Any]`
    would be a shape claim the rest of this file refuses to make. Callers use
    `dig()`, which tolerates whatever comes back.

    Raises:
        RuntimeError: on a non-2xx response.
    """
    return client.post(
        "/autofills",
        {
            "brand_template_id": template_id,
            "title": title,
            "data": data,
        },
    )


def poll_autofill(client: CanvaClient, job_id: str) -> Any:
    """Poll an autofill job until it leaves the in-progress state.

    SHAPE ASSUMPTION (unverified): `{"job": {"id", "status", "result"}}` with
    status in {"in_progress", "success", "failed"}. Returns `Any` for the same
    reason as `create_autofill`.

    Raises:
        RuntimeError: on a non-2xx response.
        TimeoutError: if the job is still in progress after
            POLL_TIMEOUT_SECONDS.
    """
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        payload = client.get(f"/autofills/{job_id}")
        status = dig(payload, "job", "status")
        print(f"  poll #{attempt}: status={status!r}")
        if status != "in_progress":
            return payload
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"Autofill job {job_id} still running after {POLL_TIMEOUT_SECONDS}s")


def report_trial_information(payload: Any) -> None:
    """Surface the trial quota, which is the whole point of this spike.

    The docs state the autofill response includes a `trial_information` object
    with `uses_remaining` and `upgrade_url` for non-Enterprise callers. Its
    exact nesting is unverified, so search the payload rather than assume.
    """
    found = _find_key(payload, "trial_information")
    print("\n=========== TRIAL QUOTA ===========")
    if found is None:
        print("No 'trial_information' present in the response.")
        print("That may mean the account is not subject to a trial limit,")
        print("or that the field is nested somewhere this search missed.")
        print("Re-run with --verbose and read the raw payload.")
    else:
        print(json.dumps(found, indent=2))
    print("===================================")


def _find_key(payload: Any, target: str) -> Any:
    """Depth-first search for the first occurrence of `target` as a dict key."""
    if isinstance(payload, Mapping):
        if target in payload:
            return payload[target]
        for value in payload.values():
            hit = _find_key(value, target)
            if hit is not None:
                return hit
    elif isinstance(payload, list):
        for item in payload:
            hit = _find_key(item, target)
            if hit is not None:
                return hit
    return None


def parse_field_args(pairs: Iterable[str]) -> dict[str, str]:
    """Turn repeated --field name=value arguments into a dict."""
    result: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            sys.exit(f"--field expects name=value, got: {pair!r}")
        name, _, value = pair.partition("=")
        result[name.strip()] = value
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch a subcommand and return the process exit code.

    Args:
        argv: Argument vector to parse. Defaults to `sys.argv[1:]`.

    Returns:
        0 on success, 1 when a step produced no usable result, 2 on a
        dispatch error.

    Raises:
        SystemExit: from argparse on a usage error, or when credentials are
            missing.
        RuntimeError: on a non-2xx Connect API response.
        TimeoutError: if an autofill job outlives POLL_TIMEOUT_SECONDS.
    """
    # __doc__ is None under `python -OO`, which strips docstrings.
    summary = (__doc__ or "Canva autofill spike").split("\n")[1]
    parser = argparse.ArgumentParser(description=summary)
    parser.add_argument("--verbose", action="store_true", help="dump raw JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="run the OAuth flow and cache tokens")
    sub.add_parser("templates", help="list brand templates")

    p_dataset = sub.add_parser("dataset", help="show a template's data fields")
    p_dataset.add_argument("--template-id", required=True)

    p_fill = sub.add_parser("autofill", help="create a design from a template")
    p_fill.add_argument("--template-id", required=True)
    p_fill.add_argument("--title", default="Autofill spike")
    p_fill.add_argument("--field", action="append", default=[], metavar="NAME=VALUE")

    args = parser.parse_args(argv)
    creds = Credentials.from_env()

    if args.command == "login":
        login(creds, args.verbose)
        return 0

    client = CanvaClient(tokens=get_valid_tokens(creds, args.verbose), verbose=args.verbose)

    if args.command == "templates":
        templates = list_brand_templates(client)
        if not templates:
            print("\nNo brand templates found.")
            print("Create one in Canva, add data fields, and publish it as a")
            print("brand template before re-running.")
            return 1
        print(f"\n{len(templates)} brand template(s):")
        for item in templates:
            print(f"  {item.get('id')}  {item.get('title')!r}")
        return 0

    if args.command == "dataset":
        dataset = get_template_dataset(client, args.template_id)
        if not dataset:
            print("\nThis template exposes no autofillable data fields.")
            print("In Canva, select each element and assign it a data field name.")
            return 1
        print(f"\n{len(dataset)} data field(s):")
        for name, spec in dataset.items():
            print(f"  {name}: {dig(spec, 'type')}")
        return 0

    if args.command == "autofill":
        dataset = get_template_dataset(client, args.template_id)
        if not dataset:
            print("Cannot autofill: template has no data fields.")
            return 1

        overrides = parse_field_args(args.field)
        unknown = set(overrides) - set(dataset)
        if unknown:
            print(f"  ! These --field names are not in the template: {sorted(unknown)}")

        data = build_autofill_data(dataset, overrides)
        print(f"\nSubmitting autofill with {len(data)} field(s)...")

        submitted = create_autofill(client, args.template_id, data, args.title)
        report_trial_information(submitted)

        job_id = dig(submitted, "job", "id")
        if not job_id:
            print("No job id in the submit response; cannot poll. Raw payload:")
            print(json.dumps(submitted, indent=2)[:4000])
            return 1

        final = poll_autofill(client, job_id)
        report_trial_information(final)

        status = dig(final, "job", "status")
        if status != "success":
            print(f"\nJob ended with status={status!r}. Raw payload:")
            print(json.dumps(final, indent=2)[:4000])
            return 1

        design = dig(final, "job", "result", "design")
        print("\n=========== RESULT ===========")
        print(f"Design id:   {dig(design, 'id')}")
        print(f"Edit URL:    {dig(design, 'urls', 'edit_url')}")
        print(f"View URL:    {dig(design, 'urls', 'view_url')}")
        print("==============================")
        if design and not dig(design, "urls", "edit_url"):
            print("\n(No edit_url at the assumed path. Raw design object:)")
            print(json.dumps(design, indent=2)[:2000])
        return 0

    # Defensive only: `required=True` on the subparser means argparse rejects an
    # unknown command before reaching here. It exists so that adding a
    # subcommand without a branch above fails loudly instead of returning None.
    # `raise` rather than `parser.error()` because the latter is NoReturn --
    # mypy calls anything after it unreachable, while ruff (RET503) demands a
    # terminal statement. A raise satisfies both.
    raise SystemExit(f"Unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
