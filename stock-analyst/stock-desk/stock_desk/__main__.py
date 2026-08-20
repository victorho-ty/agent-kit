"""``python -m stock_desk`` -- the fallback when the console script is missing."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
