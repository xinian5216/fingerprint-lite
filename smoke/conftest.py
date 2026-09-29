"""Make the repository root importable so smoke tests can reuse tests.browser.support."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from tests.browser.support import serve_local_sites  # noqa: E402


@pytest.fixture(scope="session")
def local_sites():
    """Two loopback origins the browser counts as different sites (same as upstream)."""
    with serve_local_sites() as sites:
        yield sites
