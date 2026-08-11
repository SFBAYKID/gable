"""Normalize a photo and publish it to a public HTTPS URL.

Google Slides takes an external HTTPS URL: max 2 kB of URL, PNG / JPEG / GIF,
50 MB and 25 megapixels (verified from Google's API reference). The image is
fetched once at insertion and stored inside the presentation, so the URL only
has to be reachable for that moment — but it must be public, since a Drive link
requires auth. DigitalOcean Spaces is the recommended host.

Normalization before upload: convert to JPEG, cap the long edge at
`GABLE_PHOTO_MAX_EDGE_PX`, strip EXIF (it can carry the photographer's GPS
coordinates), re-encode at `GABLE_PHOTO_JPEG_QUALITY`.

Assumes: the Spaces bucket is public-read and `SPACES_PUBLIC_BASE` resolves to
it.

Does not handle: retaining a scraped image after upload. It is deleted
immediately (ARCHITECTURE.md 7).

PHASE 0 PLACEHOLDER — no implementation yet. Spike A gates Phase 1.
"""

from __future__ import annotations
