from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from gaswatch.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["gui"]))
