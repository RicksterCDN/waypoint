"""Module entrypoint for AssuranceOrchestrator."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from main import main  # noqa: E402

main()
