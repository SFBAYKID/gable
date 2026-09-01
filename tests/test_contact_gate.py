"""One run's two-phase contact gate and official-profile cache."""

from __future__ import annotations

from pathlib import Path

from gable.agents.website import OfficialProfile, ProfileLookup
from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.pipeline.contact_gate import ContactGate
from tests.runner_support import record, submission


def _must_not_be_called(_name: str, _email: str, _phone: str = "") -> ProfileLookup:
    """A seam that fails the test if the website is consulted at all."""
    return ProfileLookup(problem="must not be called")


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

    def lookup(name: str, email: str, _phone: str = "") -> ProfileLookup:
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

    def unavailable(_name: str, _email: str, _phone: str = "") -> ProfileLookup:
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
        _must_not_be_called,
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


def test_an_agent_is_identified_by_name_when_the_email_is_the_submitters(
    tmp_path: Path,
) -> None:
    """Paula submitted two requests for two other agents on 2026-08-19.

    The form's email field holds whoever filled it in, so it identified her and
    neither agent. Tonette Campbell's filed row and her official profile agreed
    exactly; only the address on the request did not belong to her.
    """
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    item = submission(
        rid="rid-submitter-email",
        name="Tonette Campbell",
        email="paula@cornerhouserealty.com",
    )
    record(connection, item)
    store.upsert_salesperson(
        connection,
        email="tonette@cornerhouserealty.com",
        first_name="Tonette",
        last_name="Campbell",
        phone="443.360.1692",
    )
    run = store.start_run(connection, item.response_row_id)
    asked: list[tuple[str, str, str]] = []

    def lookup(name: str, email: str, phone: str) -> ProfileLookup:
        asked.append((name, email, phone))
        return ProfileLookup(
            profile=OfficialProfile(
                name="Tonette Campbell",
                email="tonette@cornerhouserealty.com",
                phone="443-360-1692",
                title="Realtor®",
                source_url="https://cornerhouserealty.com/tonette-campbell/",
            )
        )

    gate = ContactGate(connection, item.intake, lookup, default_agent_credential="Realtor")

    checked = gate.check(run.run_id, require_title=True)

    assert checked.ready is True, checked.problem
    assert checked.email == "tonette@cornerhouserealty.com", "the submitter's address is not hers"
    assert checked.phone == "443.360.1692"
    assert checked.name == "Tonette Campbell"
    # The filed phone travels to the lookup, so her profile can prove her even
    # when the request carries somebody else's address.
    assert asked and asked[0][2] == "443.360.1692"
    connection.close()


def test_an_agent_the_roster_does_not_carry_is_not_guessed_at(tmp_path: Path) -> None:
    """Mike Nugent: submitter's email, no filed row, and a profile that proves nothing.

    Picking the same-named page off the website would be guessing whose phone
    number goes onto a client's flyer, so this stops and says what is missing.
    """
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)

    item = submission(
        rid="rid-unidentified",
        name="Mike Nugent",
        email="paula@cornerhouserealty.com",
    )
    record(connection, item)
    run = store.start_run(connection, item.response_row_id)
    gate = ContactGate(
        connection,
        item.intake,
        lambda _name, _email, _phone: ProfileLookup(
            problem="the official profile does not show the submitted email address",
            found_but_unproven=True,
        ),
        default_agent_credential="Realtor",
    )

    checked = gate.check(run.run_id)

    assert checked.ready is False
    assert "belongs to whoever submitted the form" in checked.problem
    assert "Agents Contact Information" in checked.problem
    connection.close()


def test_a_lookup_that_raises_is_recorded_as_the_site_being_silent(tmp_path: Path) -> None:
    """The gate's own failure boundary marks the result unavailable.

    The credential fallback and the honest pause both then see the site's
    silence for what it is rather than as an answer about the agent.
    """
    connection = connect(tmp_path / "contact-gate-silent.db")
    apply_migrations(connection)
    item = submission(
        rid="rid-contact-gate-silent",
        name="Lolo Simmons",
        email="lolo@cornerhouserealty.com",
    )
    record(connection, item)
    connection.execute(
        "INSERT INTO salespeople (email, first_name, last_name, phone, template, synced_at)"
        " VALUES ('lolo@cornerhouserealty.com','Lolo','Simmons','(443) 854-8554','','now')"
    )
    run = store.start_run(connection, item.response_row_id)

    def raising(_name: str, _email: str, _phone: str = "") -> ProfileLookup:
        raise OSError("test official-site outage")

    gate = ContactGate(connection, item.intake, raising, default_agent_credential="Realtor")

    assert gate.check(run.run_id).ready is True
    titled = gate.check(run.run_id, require_title=True)

    assert titled.ready is True
    assert titled.title == "Realtor"
    assert "did not answer" in titled.note
