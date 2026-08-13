"""One run's two-phase contact gate and official-profile cache."""

from __future__ import annotations

from pathlib import Path

from gable.agents.website import OfficialProfile, ProfileLookup
from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.pipeline.contact_gate import ContactGate
from tests.runner_support import record, submission


def test_missing_contact_and_required_title_share_one_official_lookup(tmp_path: Path) -> None:
    connection = connect(tmp_path / "contact-gate.db")
    apply_migrations(connection)
    item = submission(
        rid="rid-contact-gate-cache",
        name="Mike Kulnich",
        email="mike@cornerhouserealty.com",
    )
    record(connection, item)
    run = store.start_run(connection, item.response_row_id)
    calls: list[tuple[str, str]] = []

    def lookup(name: str, email: str) -> ProfileLookup:
        calls.append((name, email))
        return ProfileLookup(
            profile=OfficialProfile(
                name=name,
                email=email,
                phone="410.456.3564",
                title="REALTOR®",
                source_url="https://cornerhouserealty.com/mike-kulnich/",
            )
        )

    gate = ContactGate(connection, item.intake, lookup)

    assert gate.check(run.run_id).ready is True
    titled = gate.check(run.run_id, require_title=True)

    assert titled.ready is True
    assert titled.title == "REALTOR®"
    assert calls == [("Mike Kulnich", "mike@cornerhouserealty.com")]


def test_failed_official_lookup_is_not_retried_in_the_title_phase(tmp_path: Path) -> None:
    connection = connect(tmp_path / "contact-gate-failure.db")
    apply_migrations(connection)
    item = submission(
        rid="rid-contact-gate-failure",
        name="Mike Kulnich",
        email="mike@cornerhouserealty.com",
    )
    record(connection, item)
    run = store.start_run(connection, item.response_row_id)
    calls = 0

    def unavailable(_name: str, _email: str) -> ProfileLookup:
        nonlocal calls
        calls += 1
        raise OSError("test official-site outage")

    gate = ContactGate(connection, item.intake, unavailable)

    first = gate.check(run.run_id)
    second = gate.check(run.run_id, require_title=True)

    assert first.ready is False
    assert second.ready is False
    assert calls == 1


def test_successful_gate_writes_provenance_without_contact_values(tmp_path: Path) -> None:
    connection = connect(tmp_path / "contact-gate-provenance.db")
    apply_migrations(connection)
    item = submission(rid="rid-contact-gate-provenance")
    record(connection, item)
    store.upsert_salesperson(
        connection,
        email=item.intake.agent_email,
        first_name="Lolo",
        last_name="Simmons",
        phone="(443) 854-8554",
    )
    run = store.start_run(connection, item.response_row_id)
    gate = ContactGate(
        connection,
        item.intake,
        lambda _name, _email: ProfileLookup(problem="must not be called"),
    )

    assert gate.check(run.run_id).ready is True

    details = [
        str(row["detail"])
        for row in connection.execute(
            "SELECT detail FROM run_events WHERE run_id = ? ORDER BY id", (run.run_id,)
        )
    ]
    assert any("phone from contact_workbook" in detail for detail in details)
    assert all("443" not in detail and "@" not in detail for detail in details)
