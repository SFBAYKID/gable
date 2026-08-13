"""Template values contain only facts supplied by an owned source."""

from __future__ import annotations

from pathlib import Path

from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.listings.intake import Intake
from gable.pipeline.run_values import for_intake


def test_an_agent_title_is_not_invented_when_no_source_collects_one(tmp_path: Path) -> None:
    connection = connect(tmp_path / "gable.db")
    apply_migrations(connection)
    store.upsert_salesperson(
        connection,
        email="agent@example.com",
        first_name="Avery",
        last_name="Agent",
        phone="410.555.0100",
    )
    intake = Intake(
        agent_email="agent@example.com",
        agent_name="Avery Agent",
        request_type="Sold",
        address="1 Main St, Baltimore, MD 21201",
        post_details="",
        open_house="",
        new_price="",
        closing_price="",
        extra_notes="",
        side="",
        notes="",
    )

    values = for_intake(connection, intake, {})

    assert values["agent_title"] == ""
    assert values["agent_phone"] == "410.555.0100"
    connection.close()
