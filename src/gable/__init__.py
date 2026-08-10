"""Gable — turns real-estate listing form submissions into Canva Bulk Create files.

The package is deliberately importable without Slack, Google, or network access:
`gable.pipeline.orchestrator` is driven by `gable.cli` for local development, and
`gable.slackapp` is the only subpackage that knows Slack exists. Nothing outside
`gable.slackapp` may import from it (CLAUDE.md section 6).

Assumes: nothing at import time. Configuration is read once in `gable.config`,
never scattered across modules.

Does not handle: any I/O at import time. No module here opens a socket, reads a
file, or touches the environment as a side effect of being imported.
"""

from __future__ import annotations

__version__: str = "0.1.0"
