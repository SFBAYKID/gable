"""Normalize a photo and publish it to a public HTTPS URL.

Canva image cells take an external HTTPS URL: max 4,096 characters, JPEG / PNG /
WebP / SVG+XML / HEIC / TIFF, 50MB ceiling (verified from Canva's Apps SDK docs,
CLAUDE.md 4.2). DigitalOcean Spaces is the recommended host.

Normalization before upload: convert to JPEG, cap the long edge at
`GABLE_PHOTO_MAX_EDGE_PX`, strip EXIF (it can carry the photographer's GPS
coordinates), re-encode at `GABLE_PHOTO_JPEG_QUALITY`.

Assumes: the Spaces bucket is public-read and `SPACES_PUBLIC_BASE` resolves to
it. Whether an uploaded Bulk Create file can carry such a URL at all is Spike A
and is NOT yet answered (CLAUDE.md 4.3 item 1).

Does not handle: retaining a scraped image after upload. It is deleted
immediately (ARCHITECTURE.md 7).

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
