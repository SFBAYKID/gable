"""Stable form-row identity across duplicate timestamps and row movement.

Google Forms timestamps are useful evidence, but they are not unique in the
real workbook.  Physical row numbers are unique at one instant, but insertions
and deletions move them.  This module reconciles the complete current tab with
persisted submissions before treating a row-derived id as new work.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from sqlite3 import Connection

from gable.db import store
from gable.listings.intake import Intake
from gable.sheets.client import SheetError


@dataclass(frozen=True, slots=True)
class Submission:
    """One response row, ready to reconcile or act on."""

    response_row_id: str
    sheet_row: int
    submitted_at: str
    intake: Intake
    content_hash: str
    source_tab: str = ""


def legacy_identity(submitted_at: str, agent_email: str, address: str) -> str:
    """Return the tuple id used before source provenance was deployed."""
    basis = f"{submitted_at.strip()}|{agent_email.strip().lower()}|{address.strip().lower()}"
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def source_identity(source_tab: str, sheet_row: int) -> str:
    """Return a distinct provisional id for one physical response row."""
    basis = f"source-v2|{source_tab.strip().casefold()}|{sheet_row}"
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def content_hash(row: list[str]) -> str:
    """Hash the complete raw row so an in-place edit remains detectable."""
    return hashlib.sha256("\x1f".join(row).encode()).hexdigest()[:16]


def _legacy_id(item: Submission | store.StoredSubmission) -> str:
    return legacy_identity(item.submitted_at, item.intake.agent_email, item.intake.address)


def _stored(connection: Connection) -> list[store.StoredSubmission]:
    """Load the small persisted corpus used as reconciliation evidence."""
    rows = connection.execute(
        "SELECT response_row_id FROM submissions ORDER BY first_seen_at, rowid"
    ).fetchall()
    out: list[store.StoredSubmission] = []
    for row in rows:
        item = store.load_submission(connection, str(row["response_row_id"]))
        if item is not None:
            out.append(item)
    return out


def _active_aliases(connection: Connection, source_tab: str) -> dict[str, bool]:
    """Return whether each historically aliased id is active on this tab.

    Absence means the id predates the alias ledger and remains eligible for its
    first migration snapshot. False means the source row was actually removed;
    content equality must not resurrect it as the identity of a later response.
    """
    rows = connection.execute(
        "SELECT response_row_id, MAX(active) AS active "
        "FROM submission_source_rows WHERE source_tab = ? GROUP BY response_row_id",
        (source_tab,),
    ).fetchall()
    return {str(row["response_row_id"]): bool(row["active"]) for row in rows}


def _group_current(
    indices: set[int],
    submissions: list[Submission],
    key: str,
) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for index in indices:
        item = submissions[index]
        value = (
            item.content_hash
            if key == "content"
            else item.submitted_at
            if key == "timestamp"
            else _legacy_id(item)
        )
        if value:
            grouped[value].append(index)
    for values in grouped.values():
        values.sort(key=lambda index: submissions[index].sheet_row)
    return grouped


def _group_stored(
    items: list[store.StoredSubmission],
    key: str,
) -> dict[str, list[store.StoredSubmission]]:
    grouped: dict[str, list[store.StoredSubmission]] = defaultdict(list)
    for item in items:
        value = (
            item.content_hash
            if key == "content"
            else item.submitted_at
            if key == "timestamp"
            else _legacy_id(item)
        )
        if value:
            grouped[value].append(item)
    for values in grouped.values():
        values.sort(key=lambda item: item.sheet_row)
    return grouped


def _assign(
    assigned: dict[int, str],
    used_ids: set[str],
    index: int,
    response_id: str,
) -> None:
    assigned[index] = response_id
    used_ids.add(response_id)


def _pair_equal_groups(
    current: list[int],
    prior: list[store.StoredSubmission],
    assigned: dict[int, str],
    used_ids: set[str],
) -> None:
    """Pair order-preserving groups after stronger evidence matched first."""
    available = [item for item in prior if item.response_row_id not in used_ids]
    unmatched = [index for index in current if index not in assigned]
    for index, item in zip(unmatched, available, strict=False):
        _assign(assigned, used_ids, index, item.response_row_id)


def _legacy_exact_matches(
    connection: Connection,
    submissions: list[Submission],
    indices: set[int],
    candidates: list[store.StoredSubmission],
    assigned: dict[int, str],
    used_ids: set[str],
) -> None:
    """Adopt deployed tuple ids without replaying collapsed old duplicates."""
    by_id = {item.response_row_id: item for item in candidates}
    for legacy_id, current in _group_current(indices, submissions, "legacy").items():
        prior = by_id.get(legacy_id)
        if prior is None or prior.response_row_id != _legacy_id(prior):
            continue
        alias_rows = connection.execute(
            """
            SELECT sheet_row, content_hash
              FROM submission_source_rows
             WHERE source_tab = ? AND response_row_id = ? AND active = 1
             ORDER BY sheet_row, id
            """,
            (submissions[current[0]].source_tab, legacy_id),
        ).fetchall()
        had_alias_history = connection.execute(
            "SELECT 1 FROM submission_source_rows "
            "WHERE source_tab = ? AND response_row_id = ? LIMIT 1",
            (submissions[current[0]].source_tab, legacy_id),
        ).fetchone()
        if not alias_rows and had_alias_history is None:
            # The first complete snapshot after migration may reveal more than
            # one physical row behind the deployed tuple id. Byte-identical
            # copies really were collapsed by the old database and remain
            # multiplicity aliases of that historical id. Distinct payloads
            # must not be merged: preserve only the payload the old database
            # actually retained and let every other row receive its own source
            # id below. Those newly visible historical rows then appear in the
            # explicit preview/adoption gate instead of being built or silently
            # overwritten.
            distinct_hashes = {submissions[index].content_hash for index in current}
            if len(distinct_hashes) == 1:
                for index in current:
                    assigned[index] = legacy_id
            else:
                exact = [
                    index
                    for index in current
                    if prior.content_hash and submissions[index].content_hash == prior.content_hash
                ]
                if exact:
                    for index in exact:
                        assigned[index] = legacy_id
                else:
                    same_location = [
                        index
                        for index in current
                        if submissions[index].sheet_row == prior.sheet_row
                        and submissions[index].submitted_at == prior.submitted_at
                    ]
                    if len(same_location) != 1:
                        msg = (
                            "a deployed submission id covers distinct response rows, and the "
                            "saved payload does not identify which row it belonged to"
                        )
                        raise SheetError(msg)
                    assigned[same_location[0]] = legacy_id
            used_ids.add(legacy_id)
            continue

        unmatched = set(current)
        unused_aliases = list(alias_rows)
        # Exact content survives insertion, deletion and reordering. Pair one
        # alias at a time so byte-identical multiplicity is retained.
        for index in current:
            match = next(
                (
                    row
                    for row in unused_aliases
                    if str(row["content_hash"]) == submissions[index].content_hash
                ),
                None,
            )
            if match is None:
                continue
            assigned[index] = legacy_id
            unmatched.discard(index)
            unused_aliases.remove(match)
        # An in-place correction keeps the physical position and timestamp.
        for index in sorted(unmatched):
            match = next(
                (
                    row
                    for row in unused_aliases
                    if int(row["sheet_row"]) == submissions[index].sheet_row
                ),
                None,
            )
            if match is None:
                continue
            assigned[index] = legacy_id
            unmatched.discard(index)
            unused_aliases.remove(match)
        # Preserve only as many unresolved rows as there are still-active
        # historical aliases.  Extra aliases are deletions and are retired by
        # remember_source_rows; extra current rows are genuinely new copies
        # and must receive fresh identities below.
        for index, _alias in zip(
            sorted(unmatched, key=lambda item: submissions[item].sheet_row),
            unused_aliases,
            strict=False,
        ):
            assigned[index] = legacy_id
        used_ids.add(legacy_id)


def _match_by_content(
    submissions: list[Submission],
    indices: set[int],
    candidates: list[store.StoredSubmission],
    assigned: dict[int, str],
    used_ids: set[str],
) -> None:
    current_groups = _group_current(indices - assigned.keys(), submissions, "content")
    stored_groups = _group_stored(
        [item for item in candidates if item.response_row_id not in used_ids], "content"
    )
    for value, current in current_groups.items():
        _pair_equal_groups(
            current,
            stored_groups.get(value, []),
            assigned,
            used_ids,
        )


def _match_same_location(
    submissions: list[Submission],
    indices: set[int],
    candidates: list[store.StoredSubmission],
    assigned: dict[int, str],
    used_ids: set[str],
) -> None:
    """Use unchanged tab position plus timestamp as edit evidence."""
    for index in sorted(indices - assigned.keys(), key=lambda i: submissions[i].sheet_row):
        current = submissions[index]
        matches = [
            item
            for item in candidates
            if item.response_row_id not in used_ids
            and item.sheet_row == current.sheet_row
            and item.submitted_at == current.submitted_at
        ]
        if len(matches) == 1:
            _assign(assigned, used_ids, index, matches[0].response_row_id)


def _match_grouped(
    submissions: list[Submission],
    indices: set[int],
    candidates: list[store.StoredSubmission],
    assigned: dict[int, str],
    used_ids: set[str],
    key: str,
) -> None:
    current_groups = _group_current(indices - assigned.keys(), submissions, key)
    stored_groups = _group_stored(
        [item for item in candidates if item.response_row_id not in used_ids], key
    )
    for value, current in current_groups.items():
        prior = stored_groups.get(value, [])
        if not prior:
            continue
        if key == "timestamp" and (len(current) != 1 or len(prior) != 1):
            msg = (
                "multiple form responses share a timestamp and their saved rows no longer "
                "provide enough evidence to match them safely"
            )
            raise SheetError(msg)
        _pair_equal_groups(current, prior, assigned, used_ids)


def _fresh_identity(
    connection: Connection,
    submission: Submission,
    reserved: set[str],
) -> str:
    """Use the row locator unless a moved historical row already owns it."""
    proposed = submission.response_row_id
    if proposed not in reserved and store.load_submission(connection, proposed) is None:
        return proposed
    salt = 0
    while True:
        basis = (
            "source-v3|"
            f"{submission.source_tab.casefold()}|{submission.sheet_row}|"
            f"{submission.submitted_at}|{submission.content_hash}|{salt}"
        )
        candidate = hashlib.sha256(basis.encode()).hexdigest()[:16]
        if candidate not in reserved and store.load_submission(connection, candidate) is None:
            return candidate
        salt += 1


def reconcile_submissions(
    connection: Connection,
    submissions: list[Submission],
) -> list[Submission]:
    """Resolve a complete current read to durable ids before adopting new rows.

    Exact content preserves rows across insertions, deletions and reorderings.
    Position plus timestamp preserves ordinary in-place corrections. Ambiguous
    same-timestamp corrections fail the whole read rather than merge listings.
    """
    if not submissions:
        return []
    persisted = _stored(connection)
    assigned: dict[int, str] = {}
    used_ids: set[str] = set()

    tabs: dict[str, set[int]] = defaultdict(set)
    for index, submission in enumerate(submissions):
        tabs[submission.source_tab].add(index)

    for tab, indices in tabs.items():
        if not tab:
            candidates = persisted
        else:
            aliases = _active_aliases(connection, tab)
            current_legacy_ids = {_legacy_id(submissions[index]) for index in indices}
            current_hashes = {submissions[index].content_hash for index in indices}
            current_locations = {
                (submissions[index].sheet_row, submissions[index].submitted_at) for index in indices
            }
            candidates = [
                item
                for item in persisted
                if aliases.get(item.response_row_id, True)
                and (
                    item.source_tab == tab
                    or (
                        not item.source_tab
                        and (
                            item.response_row_id in current_legacy_ids
                            or (item.content_hash and item.content_hash in current_hashes)
                            or (item.sheet_row, item.submitted_at) in current_locations
                        )
                    )
                )
            ]
        _legacy_exact_matches(connection, submissions, indices, candidates, assigned, used_ids)
        _match_by_content(submissions, indices, candidates, assigned, used_ids)
        _match_same_location(submissions, indices, candidates, assigned, used_ids)
        _match_grouped(submissions, indices, candidates, assigned, used_ids, "legacy")
        _match_grouped(submissions, indices, candidates, assigned, used_ids, "timestamp")

        for index in sorted(indices - assigned.keys()):
            identity = _fresh_identity(connection, submissions[index], used_ids)
            _assign(assigned, used_ids, index, identity)

    return [
        replace(item, response_row_id=assigned[index]) for index, item in enumerate(submissions)
    ]


def remember_source_rows(
    connection: Connection,
    submissions: list[Submission],
    source_tabs: frozenset[str] = frozenset(),
) -> None:
    """Persist complete reconciled tab snapshots, including duplicate counts.

    Missing active aliases are retired. If an identical row later appears, the
    active count increases and reconciliation assigns the new row a fresh id.
    """
    now = datetime.now(UTC).isoformat()
    tabs = {item.source_tab for item in submissions if item.source_tab} | set(source_tabs)
    for tab in tabs:
        current = [item for item in submissions if item.source_tab == tab]
        rows = connection.execute(
            """
            SELECT id, response_row_id, sheet_row, submitted_at, content_hash
              FROM submission_source_rows
             WHERE source_tab = ? AND active = 1
             ORDER BY sheet_row, id
            """,
            (tab,),
        ).fetchall()
        existing: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
        for row in rows:
            key = (str(row["response_row_id"]), str(row["content_hash"]))
            existing[key].append((int(row["id"]), int(row["sheet_row"])))
        grouped: dict[tuple[str, str], list[Submission]] = defaultdict(list)
        for item in current:
            grouped[(item.response_row_id, item.content_hash)].append(item)
        for values in grouped.values():
            values.sort(key=lambda item: item.sheet_row)

        retained: set[int] = set()
        for key, items in grouped.items():
            prior = existing.get(key, [])
            for item, (alias_id, _old_row) in zip(items, prior, strict=False):
                retained.add(alias_id)
                connection.execute(
                    """
                    UPDATE submission_source_rows
                       SET sheet_row = ?, submitted_at = ?, last_seen_at = ?, active = 1
                     WHERE id = ?
                    """,
                    (item.sheet_row, item.submitted_at, now, alias_id),
                )
            for item in items[len(prior) :]:
                cursor = connection.execute(
                    """
                    INSERT INTO submission_source_rows (
                        response_row_id, source_tab, sheet_row, submitted_at,
                        content_hash, active, first_seen_at, last_seen_at
                    ) VALUES (?,?,?,?,?,1,?,?)
                    """,
                    (
                        item.response_row_id,
                        tab,
                        item.sheet_row,
                        item.submitted_at,
                        item.content_hash,
                        now,
                        now,
                    ),
                )
                if cursor.lastrowid is None:  # pragma: no cover - SQLite INSERT contract
                    raise RuntimeError("the source-row alias was not assigned an id")
                retained.add(cursor.lastrowid)

        active_ids = {int(row["id"]) for row in rows}
        retired = active_ids - retained
        if retired:
            placeholders = ",".join("?" for _ in retired)
            connection.execute(
                f"UPDATE submission_source_rows SET active = 0, last_seen_at = ? "
                f"WHERE id IN ({placeholders})",
                (now, *sorted(retired)),
            )
