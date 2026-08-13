"""Read-only Google Sheets access for form submissions.

The response tab is located and parsed by header text and is never modified.
SQLite holds submissions, runs, transition history, template audits, spend,
and the local mirror of the Drive contact workbook.
"""

from __future__ import annotations
