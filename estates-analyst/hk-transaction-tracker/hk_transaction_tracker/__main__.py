"""``python -m hk_transaction_tracker`` -- the same entry point as ``hk-tx``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
