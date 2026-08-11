"""Tests for publishing a photo where Slides can fetch it.

`publish` shells out to `ssh`, so these put a fake `ssh` on `PATH` rather than
mocking `subprocess` — that way the argv this module really builds is the argv
under test, including the quoting.

The bias is toward the two things that would be bad: writing outside the web
root, and certifying a half-written file as good.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from gable.photos.store import (
    PhotoHost,
    PublishError,
    content_name,
    publish,
    publish_local,
)

HOST = PhotoHost(
    ssh_target="root@198.51.100.7",
    ssh_key_path="/dev/null",
    public_base="http://198.51.100.7",
)
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture
def fake_ssh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in `ssh` that records its argv and stdin instead of connecting."""
    log = tmp_path / "argv.log"
    script = tmp_path / "ssh"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {log}\ncat > /dev/null\nexit 0\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    return log


# --- naming -----------------------------------------------------------------


def test_the_name_is_derived_from_content() -> None:
    """Content-addressing makes republishing identical bytes a no-op."""
    assert content_name(PNG) == content_name(PNG)
    assert content_name(PNG) != content_name(PNG + b"x")


def test_the_name_looks_like_a_hash_and_a_suffix() -> None:
    name = content_name(PNG)
    assert len(name) == len("0123456789abcdef.jpg")
    assert name.endswith(".jpg")


def test_an_empty_image_cannot_be_named() -> None:
    with pytest.raises(ValueError, match="empty image"):
        content_name(b"")


@pytest.mark.parametrize("suffix", ["/../../evil.conf", ".sh", ".conf", ""])
def test_a_suffix_slides_will_not_accept_is_refused(suffix: str) -> None:
    """`content_name(b, suffix='/../../evil.conf')` would traverse too."""
    with pytest.raises(ValueError, match="not a format"):
        content_name(PNG, suffix=suffix)


# --- the traversal guard ----------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../../../root/.ssh/authorized_keys",
        "../../etc/nginx/conf.d/evil.conf",
        "/etc/passwd",
        "a b.jpg;rm -rf /",
        "nothash.jpg",
        "0123456789abcdef.sh",
    ],
)
def test_publish_refuses_a_name_that_could_escape_the_web_root(name: str, fake_ssh: Path) -> None:
    """shlex.quote blocks metacharacters but says nothing about `..`.

    The SSH target may be root, so an unchecked name is an arbitrary root write
    the moment any caller derives one from form input.
    """
    with pytest.raises(PublishError, match="refusing to publish"):
        publish(HOST, PNG, name=name)
    assert not fake_ssh.exists(), "nothing should have been sent"


def test_a_content_hash_name_is_allowed(fake_ssh: Path) -> None:
    url = publish(HOST, PNG)
    assert url.startswith("http://198.51.100.7/")
    assert fake_ssh.exists()


# --- the remote command -----------------------------------------------------


def test_the_write_is_atomic(fake_ssh: Path) -> None:
    """Nginx must never serve a half-written file to Google.

    A truncated JPEG still starts FFD8FF, so a byte-header check would certify
    it. An intra-filesystem rename removes the window entirely.
    """
    publish(HOST, PNG)
    argv = fake_ssh.read_text()
    assert ".part" in argv
    assert "mv -f" in argv


def test_the_file_is_made_world_readable(fake_ssh: Path) -> None:
    """Nginx — and therefore Google — must be able to read it."""
    publish(HOST, PNG)
    assert "chmod 644" in fake_ssh.read_text()


def test_it_writes_inside_the_configured_root(fake_ssh: Path) -> None:
    publish(HOST, PNG)
    assert HOST.remote_root in fake_ssh.read_text()


def test_batch_mode_is_set_so_it_cannot_hang_on_a_prompt(fake_ssh: Path) -> None:
    publish(HOST, PNG)
    assert "BatchMode=yes" in fake_ssh.read_text()


# --- failure paths ----------------------------------------------------------


def test_a_failing_transfer_raises_with_the_transports_own_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A silent failure here surfaces much later as an opaque Google error."""
    script = tmp_path / "ssh"
    script.write_text("#!/bin/sh\ncat > /dev/null\necho 'no route to host' >&2\nexit 255\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(PublishError, match="no route to host"):
        publish(HOST, PNG)


def test_url_for_joins_cleanly_whatever_the_base_looks_like() -> None:
    trailing = PhotoHost(ssh_target="x", ssh_key_path="y", public_base="http://h/")
    assert trailing.url_for("a.jpg") == "http://h/a.jpg"


def test_the_host_is_frozen() -> None:
    with pytest.raises(AttributeError):
        HOST.public_base = "http://elsewhere"  # type: ignore[misc]


# --- same-box production publishing ----------------------------------------


def test_local_publish_writes_a_world_readable_content_addressed_file(tmp_path: Path) -> None:
    url = publish_local(tmp_path / "gable-photos", "http://198.51.100.7", PNG)
    published = tmp_path / "gable-photos" / url.rsplit("/", 1)[-1]

    assert published.read_bytes() == PNG
    assert stat.S_IMODE(published.stat().st_mode) == 0o644


def test_local_publish_leaves_an_existing_content_file_untouched(tmp_path: Path) -> None:
    root = tmp_path / "gable-photos"
    first = publish_local(root, "http://198.51.100.7", PNG)
    published = root / first.rsplit("/", 1)[-1]
    first_inode = published.stat().st_ino

    second = publish_local(root, "http://198.51.100.7", PNG)

    assert second == first
    assert published.stat().st_ino == first_inode


def test_local_publish_refuses_a_broad_or_relative_root() -> None:
    with pytest.raises(PublishError, match="specific absolute"):
        publish_local(Path("relative"), "http://198.51.100.7", PNG)
    with pytest.raises(PublishError, match="specific absolute"):
        publish_local(Path("/"), "http://198.51.100.7", PNG)


def test_local_publish_refuses_a_traversing_name(tmp_path: Path) -> None:
    with pytest.raises(PublishError, match="content hash"):
        publish_local(
            tmp_path / "gable-photos",
            "http://198.51.100.7",
            PNG,
            name="../../outside.jpg",
        )
