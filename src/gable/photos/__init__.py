"""Photo resolution, enhancement, and hosting.

The cascade order is fixed (CLAUDE.md 8): form upload, Drive folder, brokerage
site, web search, ask Carmen, and only then generate — and only if
`GABLE_PHOTO_POLICY` permits it. Enhancement of a real photo and generation of a
synthetic one are different operations on deliberately separate code paths.
"""

from __future__ import annotations
