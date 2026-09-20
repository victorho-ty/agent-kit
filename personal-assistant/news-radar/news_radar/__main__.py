"""``python -m news_radar`` -- the fallback when the console script is missing."""

from __future__ import annotations

import sys

from .cli import run

if __name__ == "__main__":
    sys.exit(run())
