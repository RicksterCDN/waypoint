"""Module entrypoint for Waypoint Recorder."""

import sys
import os

# Ensure the agent root (where main.py lives) is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from main import main  # noqa: E402

main()
