"""Make the flat backend modules importable when pytest runs from anywhere."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# JobStore reads WALKMAN_JOBS_DB at import. Keep pytest off the operator's db.
if "WALKMAN_JOBS_DB" not in os.environ:
    os.environ["WALKMAN_JOBS_DB"] = str(
        Path(tempfile.gettempdir()) / f"walkman-jobs-pytest-{os.getpid()}.sqlite"
    )

# pytest copies parametrized values into test ids. A long id breaks Windows runs: pytest
# stores it in PYTEST_CURRENT_TEST (Windows caps variables at 32,767 characters), and a
# 1 MiB id line stalled the GitHub Actions runner so the step never ended.
MAX_TEST_ID = 1024


def pytest_collection_modifyitems(config, items):
    long_ids = [item.nodeid[:120] + "..." for item in items if len(item.nodeid) > MAX_TEST_ID]
    if long_ids:
        raise pytest.UsageError(f"Test ids longer than {MAX_TEST_ID} characters; give these "
                                "parameters explicit ids:\n" + "\n".join(long_ids))


# CI sets WALKMAN_TEST_HANG_SECONDS so a hung test prints every thread's stack
# and ends pytest, instead of holding the Windows runner until the job times out.
_HANG_SECONDS = float(os.environ.get("WALKMAN_TEST_HANG_SECONDS") or 0)
if _HANG_SECONDS > 0:
    import faulthandler

    _hang_report = []

    def _arm():
        faulthandler.dump_traceback_later(_HANG_SECONDS, exit=True, file=_hang_report[0])

    def pytest_configure(config):
        # A duplicate of the real stderr, taken while capture is suspended:
        # pytest replaces fd 2 while it imports this file and runs each test.
        _hang_report.append(os.fdopen(os.dup(2), "w"))

    def pytest_sessionstart(session):
        _arm()  # covers collection

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(item, nextitem):
        _arm()
        try:
            yield
        finally:
            faulthandler.cancel_dump_traceback_later()

    @pytest.hookimpl(trylast=True)
    def pytest_exception_interact(node, call, report):
        # pytest's faulthandler plugin cancels the timer here; keep the failed test's teardown watched.
        _arm()

    def pytest_sessionfinish(session, exitstatus):
        # Also covers a hang after the last test, such as a thread that blocks exit.
        _arm()
