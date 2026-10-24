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

# CI sets WALKMAN_TEST_HANG_SECONDS so a hung test prints every thread's stack
# and ends pytest, instead of holding the Windows runner until the job times out.
_HANG_SECONDS = float(os.environ.get("WALKMAN_TEST_HANG_SECONDS") or 0)
if _HANG_SECONDS > 0:
    import faulthandler

    import pytest

    _hang_report = []

    def pytest_configure(config):
        # A duplicate of the real stderr, taken while capture is suspended:
        # pytest replaces fd 2 while it imports this file and runs each test.
        _hang_report.append(os.fdopen(os.dup(2), "w"))

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(item, nextitem):
        faulthandler.dump_traceback_later(_HANG_SECONDS, exit=True, file=_hang_report[0])
        try:
            yield
        finally:
            faulthandler.cancel_dump_traceback_later()

    def pytest_sessionfinish(session, exitstatus):
        # Also covers a hang after the last test, such as a thread that blocks exit.
        faulthandler.dump_traceback_later(_HANG_SECONDS, exit=True, file=_hang_report[0])
