"""Preparation, verification, deterministic fitting, and hosting for photos.

The connected hero source is one image Carmen or Chase supplies in the owned
Slack thread. Its composition is preserved until the exact template frame is
known. Pillow handles every fit; very small sources use a contained foreground
over a blurred, darkened fill from the same upload. Synthetic generation,
generative enhancement, and automatic photo discovery are not connected.
"""

from __future__ import annotations
