"""Run-scoped contact prerequisites before Gable speaks in Slack.

The gate binds one submission to the freshly mirrored contact row and memoizes
at most one official-profile lookup.  That lets the runner validate the three
unconditional fields immediately, then ask the selected source whether it also
needs a credential, without fetching the same public profile twice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from sqlite3 import Connection

from gable.agents import website
from gable.db import store
from gable.listings.intake import Intake
from gable.sheets import repository as repo


@dataclass(slots=True)
class ContactGate:
    """Validate one submission's agent with one optional official-site read."""

    connection: Connection
    intake: Intake
    official_lookup: Callable[[str, str], website.ProfileLookup]
    _looked_up: bool = field(default=False, init=False)
    _official_result: website.ProfileLookup = field(
        default_factory=website.ProfileLookup,
        init=False,
    )

    def _lookup_once(self, name: str, email: str) -> website.ProfileLookup:
        """Return the first official lookup result for every phase of this run."""
        if not self._looked_up:
            self._looked_up = True
            try:
                self._official_result = self.official_lookup(name, email)
            except Exception:
                self._official_result = website.ProfileLookup(
                    problem=(
                        "I could not complete the check against the official "
                        "Corner House Realty website"
                    )
                )
        return self._official_result

    def check(self, run_id: str, *, require_title: bool = False) -> website.ContactCheck:
        """Validate, then append value-free field provenance when successful.

        Args:
            run_id: Current run whose event log records this prerequisite.
            require_title: Whether the selected source contains an agent-title
                field that needs an exact credential from the official profile.

        Returns:
            A ready contact or one human-actionable pause reason.

        Raises:
            sqlite3.Error: If the local roster or run event cannot be read or
                written. The runner's shared failure boundary records that.
        """
        contact = website.validate_contact(
            self.intake.agent_name,
            self.intake.agent_email,
            website.contact_from_record(
                repo.find_salesperson(self.connection, self.intake.agent_email)
            ),
            self._lookup_once,
            require_title=require_title,
        )
        if contact.ready:
            store.set_status(
                self.connection,
                run_id,
                "pending",
                contact.provenance_detail(),
            )
        return contact
