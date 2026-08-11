"""Publishing a photo to a URL that Google Slides will actually fetch.

Slides fetches the image **anonymously, once, at insertion**, then stores a copy
inside the presentation. Two consequences shape this module:

* The URL must be reachable with no credentials. Verified 2026-08-10: Google
  Drive does **not** work even when the file is world-readable and returns valid
  bytes to an anonymous client — Slides declines it. Slack does not work either
  (`files.sharedPublicURL` needs a user token, not a bot token). A plain host is
  required, and `http://` is accepted, so no certificate is needed.
* Nothing here has to be durable. Once the render succeeds the presentation owns
  its own copy, so these files exist only to survive a single fetch.

The host is the Gable droplet running nginx over `http://`. That is deliberate:
it costs nothing beyond the droplet already being paid for, needs no new
credential, and the only thing published is a listing photo that is on its way
to public marketing anyway.

Does not handle: fetching from Slack, fitting, or deletion. Files are small and
the droplet has a disk; `prune` exists for when that stops being true.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: Published names must look exactly like this. `shlex.quote` blocks shell
#: metacharacters but says nothing about `..`, and the SSH target may be root —
#: so `name="../../../root/.ssh/authorized_keys"` would be an arbitrary root
#: write the moment any caller passes a name derived from form input.
SAFE_NAME: Final[re.Pattern[str]] = re.compile(r"^[a-f0-9]{16}\.(jpg|jpeg|png|gif)$")

#: Where nginx serves from on the droplet.
REMOTE_ROOT: Final[str] = "/var/www/gable-photos"

#: How long to allow a single scp/ssh call. A photo is a few hundred kB; past
#: this something is wrong with the network, not the file.
TRANSFER_TIMEOUT_SECONDS: Final[int] = 60


class PublishError(Exception):
    """Raised when a photo could not be made fetchable."""


@dataclass(frozen=True, slots=True)
class PhotoHost:
    """Where published photos live, and how to reach the box that serves them.

    Frozen because it is configuration. Holds no credential — authentication is
    an SSH key path, and the key never leaves disk.
    """

    ssh_target: str
    ssh_key_path: str
    public_base: str
    remote_root: str = REMOTE_ROOT

    def url_for(self, name: str) -> str:
        """The public URL a published file will have.

        Args:
            name: The published filename.

        Returns:
            An absolute URL.

        Raises:
            Nothing.
        """
        return f"{self.public_base.rstrip('/')}/{name}"


def content_name(image_bytes: bytes, suffix: str = ".jpg") -> str:
    """A filename derived from the image's own content.

    Content-addressing means republishing an identical photo is a no-op and the
    URL is stable, which is what makes repeated test runs free. It also means two
    listings that share a photo share one file.

    Args:
        image_bytes: The image.
        suffix: File extension, including the dot. Slides requires the URL to
            end in a format it accepts.

    Returns:
        Something like `a1b2c3d4e5f6a7b8.jpg`.

    Raises:
        ValueError: if `image_bytes` is empty.
    """
    if not image_bytes:
        msg = "cannot name an empty image"
        raise ValueError(msg)
    if suffix not in {".jpg", ".jpeg", ".png", ".gif"}:
        msg = f"suffix {suffix!r} is not a format Slides accepts"
        raise ValueError(msg)
    digest = hashlib.sha256(image_bytes).hexdigest()[:16]
    return f"{digest}{suffix}"


def publish(host: PhotoHost, image_bytes: bytes, name: str | None = None) -> str:
    """Put an image where Slides can fetch it, and return its URL.

    Args:
        host: The droplet to publish to.
        image_bytes: The image, already fitted to the frame.
        name: Filename to use. Defaults to a content hash, which makes
            republishing the same bytes idempotent.

    Returns:
        The public `http://` URL.

    Raises:
        ValueError: if `image_bytes` is empty.
        PublishError: if the transfer fails, with the transport's own stderr
            included — a silent failure here surfaces much later as an opaque
            "problem retrieving the image" from Google.
    """
    filename = name or content_name(image_bytes)
    if not SAFE_NAME.match(filename):
        msg = (
            f"refusing to publish {filename!r}: a published name must be a content hash "
            "plus an image suffix. Anything else can traverse out of the web root."
        )
        raise PublishError(msg)
    remote_path = f"{host.remote_root}/{filename}"
    # Write to a temporary name and rename into place. An intra-filesystem
    # rename is atomic, so nginx never serves a half-written file — and a
    # truncated JPEG still starts FFD8FF, which means `verify_public` would
    # otherwise certify it as valid.
    staging = f"{remote_path}.part"
    command = [
        "ssh",
        "-i",
        host.ssh_key_path,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        host.ssh_target,
        # A quoted heredoc-free write: cat the bytes straight to the file, then
        # make it world-readable so nginx (and therefore Google) can read it.
        f"cat > {shlex.quote(staging)} && chmod 644 {shlex.quote(staging)} "
        f"&& mv -f {shlex.quote(staging)} {shlex.quote(remote_path)}",
    ]
    try:
        result = subprocess.run(
            command,
            input=image_bytes,
            capture_output=True,
            timeout=TRANSFER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"publishing {filename} timed out after {TRANSFER_TIMEOUT_SECONDS}s"
        raise PublishError(msg) from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        msg = f"publishing {filename} failed: {stderr[:300]}"
        raise PublishError(msg)
    return host.url_for(filename)


def publish_local(
    root: Path,
    public_base: str,
    image_bytes: bytes,
    name: str | None = None,
) -> str:
    """Publish through the nginx directory on the same machine as Gable.

    Production runs on the photo host itself. SSHing from the unprivileged
    service back into its own box required a root key that systemd correctly
    hides, so the live path writes to the one explicitly permitted web root.
    A unique staging file is atomically renamed into place; existing
    content-addressed files are left untouched.

    Args:
        root: nginx's writable document root.
        public_base: Public origin Google Slides can fetch.
        image_bytes: The already-fitted image.
        name: Optional safe content-addressed filename.

    Returns:
        The public URL.

    Raises:
        ValueError: if the image is empty.
        PublishError: if the name or filesystem write is unsafe or fails.
    """
    filename = name or content_name(image_bytes)
    if not SAFE_NAME.fullmatch(filename):
        msg = "refusing to publish a filename that is not a content hash and image suffix"
        raise PublishError(msg)
    if not image_bytes:
        msg = "cannot publish an empty image"
        raise ValueError(msg)
    if not root.is_absolute() or len(root.parts) < 3:
        msg = "the local photo root must be a specific absolute directory"
        raise PublishError(msg)

    try:
        root.mkdir(parents=True, exist_ok=True)
        destination = root / filename
        if destination.is_file():
            return f"{public_base.rstrip('/')}/{filename}"
        staging = root / f".{filename}.{uuid.uuid4().hex}.part"
        with staging.open("xb") as handle:
            handle.write(image_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        staging.chmod(0o644)
        staging.replace(destination)
    except OSError as exc:
        msg = f"publishing {filename} into the local photo host failed"
        raise PublishError(msg) from exc
    return f"{public_base.rstrip('/')}/{filename}"


def verify_public(url: str, timeout: int = 20) -> tuple[bool, str]:
    """Check that a published URL really is fetchable without credentials.

    Worth doing before handing the URL to Slides, because Google's failure
    message — "There was a problem retrieving the image" — does not distinguish
    "not found" from "not public" from "wrong format".

    Args:
        url: The URL to check.
        timeout: Seconds to wait.

    Returns:
        `(ok, detail)`. `detail` is a short human-readable reason on failure and
        the detected content type on success.

    Raises:
        Nothing. A check that raises is worse than one that reports.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            head = response.read(4)
            content_type = response.headers.get("Content-Type", "")
            if not head.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8")):
                return False, f"served {content_type!r} but the bytes are not an image"
            return True, content_type
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"unreachable: {type(exc).__name__}"
