"""Module entrypoint — enables `python -m my_agent` and debugpy attach.

When you copy this template to agents/<your-name>/, rename:
  - this directory: src/my_agent/ -> src/<your_name>/
  - the import below: my_agent.main -> <your_name>.main  (if you add app.py)
  - pyproject.toml: name, packages entry, and [project.scripts]

For now the template keeps the entry point in main.py at the agent root
and this __main__.py just calls it, enabling `python -m my_agent` for debugpy.
"""

import sys
import os

# Ensure the agent root (where main.py lives) is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from main import main  # noqa: E402

main()
