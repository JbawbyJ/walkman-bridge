"""Make the flat backend modules importable when pytest runs from anywhere."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# JobStore reads WALKMAN_JOBS_DB at import. Keep pytest off the operator's db.
if "WALKMAN_JOBS_DB" not in os.environ:
    os.environ["WALKMAN_JOBS_DB"] = str(
        Path(tempfile.gettempdir()) / f"walkman-jobs-pytest-{os.getpid()}.sqlite"
    )
