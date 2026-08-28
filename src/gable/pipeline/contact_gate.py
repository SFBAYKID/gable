"""Run-scoped contact prerequisites before Gable speaks in Slack.

The gate binds one submission to the freshly mirrored contact row and memoizes
at most one official-profile lookup.  That lets the runner validate the three
unconditional fields immediately, then ask the selected source whether it also
needs a credential, without fetching the same public profile twice.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from sqlite3 import Connection

from gable.agents import website
from gable.agents.contacts import Contact
from gable.db import store
from gable.listings.intake import Intake
from gable.sheets import repository as repo

logger = logging.getLogger("gable.contact_gate")


@dataclass(slots=True)
class ContactGate:
    """Validate one submission's agent with one optional official-site read."""

    connection: Connection
    intake: Intake
    #: `(name, email, known_phone) -> profile`. The filed phone travels with the
    #: request so an agent whose official page lists a brokerage address can
    #: still be proven from a personal one.
    official_lookup: Callable[[str, str, str], website.ProfileLookup]
    #: The credential every agent at this brokerage holds. Fills a title only
    #: when the proven profile leaves its own job title blank.
    default_agent_credential: str = ""
    _resolved: tuple[str, Contact | None] | None = field(default=None, init=False)
    _looked_up: bool = field(default=False, init=False)
    _official_result: website.ProfileLookup = field(
        default_factory=website.ProfileLookup,
        init=False,
    )

    def _identify(self) -> tuple[str, Contact | None]:
        """Decide which agent this request is for, and which email proves it.

        The form's email field holds whoever filled the form in. On 2026-08-19
        one person submitted two requests for two other agents, so that address
        identified the submitter and neither agent. When it does not belong to
        the agent the request names, the roster decides instead: exactly one
        filed row with that name, whose own email then stands in as the agent's.
        Zero rows or several is not something to guess at.

        Returns:
            The agent's email and their filed row, or the submitted email and
            whatever that address matched when identity could not be resolved.

        Raises:
            sqlite3.Error: if the roster cannot be read.
        """
        if self._resolved is not None:
            return self._resolved
        name = self.intake.agent_name
        submitted = self.intake.agent_email
        filed = website.contact_from_record(repo.find_salesperson(self.connection, submitted))
        if filed is not None and website.names_agree(name, filed):
            self._resolved = (submitted, filed)
            return self._resolved
        named = [
            contact
            for row in repo.all_salespeople(self.connection)
            if (contact := website.contact_from_record(row)) is not None
            and website.names_agree(name, contact)
        ]
        if len(named) == 1:
            logger.info(
                "the request email is not the named agent's; identified %s from the roster",
                name,
            )
            self._resolved = (named[0].email, named[0])
        else:
            self._resolved = (submitted, filed)
        return self._resolved

    def _lookup_once(self, name: str, email: str) -> website.ProfileLookup:
        """Return the first official lookup result for every phase of this run."""
        if not self._looked_up:
            self._looked_up = True
            _, filed = self._identify()
            try:
                self._official_result = self.official_lookup(
                    name, email, filed.phone if filed else ""
                )
            except Exception:
                # Carmen reads one sentence; whoever has to fix it needs the
                # cause. A lookup that fails silently reads as "the website is
                # down" whether it was a timeout, a spent Firecrawl budget, or a
                # bug, and all three were guessed at once before this line.
                logger.exception("the official profile lookup for %s could not complete", email)
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
                field. The credential comes from the agent's own profile when it
                states one, and otherwise from the brokerage default.

        Returns:
            A ready contact or one human-actionable pause reason.

        Raises:
            sqlite3.Error: If the local roster or run event cannot be read or
                written. The runner's shared failure boundary records that.
        """
        email, filed = self._identify()
        contact = website.validate_contact(
            self.intake.agent_name,
            email,
            filed,
            self._lookup_once,
            require_title=require_title,
            default_title=self.default_agent_credential,
        )
        # An agent with no filed row can still be proven by their own email
        # appearing on their profile — that is how a new agent builds before
        # anyone adds them. What cannot be proven is a request whose email
        # belongs to somebody else and whose name the roster does not carry.
        if not contact.ready and filed is None and self._official_result.found_but_unproven:
            # Counted from the roster this run just mirrored, so the sentence
            # reports the read that actually happened rather than asserting an
            # absence in the abstract. See `website.unidentified_pause`.
            return website.ContactCheck(
                problem=website.unidentified_pause(
                    self.intake.agent_name,
                    len(repo.all_salespeople(self.connection)),
                )
            )
        if contact.ready:
            store.set_status(
                self.connection,
                run_id,
                "pending",
                contact.provenance_detail(),
            )
        return contact
