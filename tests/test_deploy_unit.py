"""Structural checks on the systemd unit.

These parse the unit and assert the directives that carry a security or
availability decision. They are NOT a substitute for `systemd-analyze verify
gable.service`, which only runs on the droplet — see deploy/PROVISION.md. What
they do catch is a misspelled section header, a directive dropped in a refactor,
or ExecStart drifting away from the package it is supposed to launch.

Every assertion below traces to a rule: unprivileged user and read-only
filesystem (ARCHITECTURE.md 7), bounded restart rather than a crash loop
(ARCHITECTURE.md 6), and no inbound listener (ARCHITECTURE.md 2.2).
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

UNIT_PATH: Path = Path(__file__).resolve().parent.parent / "deploy" / "gable.service"
MAKEFILE_PATH: Path = Path(__file__).resolve().parent.parent / "Makefile"


@pytest.fixture(scope="module")
def unit() -> configparser.ConfigParser:
    # strict=False: systemd permits repeated keys (Environment=, ReadWritePaths=)
    # that configparser would otherwise reject outright.
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    # Preserve case; systemd directives are case-sensitive and the default
    # optionxform would lowercase every one of them.
    parser.optionxform = str  # type: ignore[method-assign,assignment]
    parser.read_string(UNIT_PATH.read_text(encoding="utf-8"))
    return parser


def test_unit_has_required_sections(unit: configparser.ConfigParser) -> None:
    assert unit.has_section("Unit")
    assert unit.has_section("Service")
    assert unit.has_section("Install")


def test_runs_as_unprivileged_gable_user(unit: configparser.ConfigParser) -> None:
    """Never root (ARCHITECTURE.md section 7)."""
    assert unit.get("Service", "User") == "gable"
    assert unit.get("Service", "Group") == "gable"
    assert unit.get("Service", "NoNewPrivileges") == "true"


def test_runtime_files_default_to_owner_only_permissions(
    unit: configparser.ConfigParser,
) -> None:
    """SQLite, WAL and SHM files may contain private listing information."""
    assert unit.get("Service", "UMask") == "0077"


def test_execstart_launches_the_installed_package(unit: configparser.ConfigParser) -> None:
    """ExecStart must point at the venv interpreter and a `gable.` module.

    A bare `python` would pick up the system interpreter, which has none of the
    dependencies installed.
    """
    exec_start = unit.get("Service", "ExecStart")
    assert "/opt/gable/.venv/bin/python" in exec_start
    assert "-m gable.slackapp.runtime" in exec_start


def test_restart_is_bounded(unit: configparser.ConfigParser) -> None:
    """Restart on failure, but stop rather than hammer external APIs forever."""
    assert unit.get("Service", "Restart") == "on-failure"
    assert unit.has_option("Service", "RestartSec")


def test_start_limits_are_in_the_unit_section_not_service(
    unit: configparser.ConfigParser,
) -> None:
    """StartLimit* in [Service] is rejected by systemd — silently, which is the danger.

    Found on the real droplet: `systemd-analyze verify` reported "Unknown key
    name 'StartLimitIntervalSec' in section 'Service', ignoring", meaning the
    crash-loop guard was doing nothing at all while looking correct in the file.
    `systemctl show` now confirms StartLimitIntervalUSec=5min, Burst=5.
    """
    assert unit.has_option("Unit", "StartLimitIntervalSec")
    assert unit.has_option("Unit", "StartLimitBurst")
    assert int(unit.get("Unit", "StartLimitBurst")) <= 5
    # The bug this test exists to prevent.
    assert not unit.has_option("Service", "StartLimitIntervalSec")
    assert not unit.has_option("Service", "StartLimitBurst")


def test_filesystem_is_read_only_except_runtime_data_paths(
    unit: configparser.ConfigParser,
) -> None:
    assert unit.get("Service", "ProtectSystem") == "strict"
    assert unit.get("Service", "ProtectHome") == "true"
    assert unit.get("Service", "ReadWritePaths") == "/opt/gable/var /var/www/gable-photos"


def test_every_deploy_repairs_the_photo_directory_ownership() -> None:
    """The unprivileged service must be able to publish fitted photos.

    The real droplet had this directory owned by root even though systemd
    allowed the path. The kernel still denied Gable's writes, so a valid Slack
    upload failed after it had already been resized.
    """
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "install -d -o gable -g gable -m 0755 /var/www/gable-photos" in makefile


def test_every_deploy_installs_private_runtime_storage() -> None:
    """The directory and pre-existing SQLite files become private on deploy."""
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "install -d -o gable -g gable -m 0700 $(GABLE_DIR)/var" in makefile
    assert "-name 'gable.db*' -exec chmod 0600" in makefile


def test_every_deploy_installs_and_reloads_the_service_unit_before_restart() -> None:
    """Repository unit changes must reach systemd on the same deploy."""
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    install = "install -m 0644 deploy/gable.service /etc/systemd/system/gable.service"
    reload = "systemctl daemon-reload"
    restart = "systemctl restart gable"
    assert install in makefile
    assert reload in makefile
    assert restart in makefile
    assert makefile.index(install) < makefile.index(reload) < makefile.index(restart)


def test_no_inbound_listener_is_configured() -> None:
    """Socket Mode opens an outbound socket; nothing here may open a port.

    A `ListenStream`/`ListenDatagram` directive appearing in this unit would
    mean someone quietly reintroduced HTTP events, which is the decision
    ARCHITECTURE.md 2.2 explicitly made against.
    """
    text = UNIT_PATH.read_text(encoding="utf-8")
    assert "ListenStream" not in text
    assert "ListenDatagram" not in text


def test_env_file_is_the_only_secret_source(unit: configparser.ConfigParser) -> None:
    """Secrets arrive via EnvironmentFile, never inline in the unit."""
    assert unit.get("Service", "EnvironmentFile") == "/opt/gable/.env"
    # Inline Environment= lines are fine for switches, but must never carry a
    # value that looks like a credential.
    text = UNIT_PATH.read_text(encoding="utf-8")
    for marker in ("xoxb-", "xapp-", "SECRET=", "API_KEY=", "PRIVATE_KEY"):
        assert marker not in text, f"possible secret in the unit file: {marker}"
