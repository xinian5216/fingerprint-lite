"""Unicode-safe MaxMind database reads in this Windows application process.

Camoufox calls maxminddb.open_database with AUTO, which selects the optional
C reader. Its narrow Windows filename API can reject a UTF-8 filename even
when Python can see the file. Use the library's supported Python mmap reader
for Unicode Windows paths; retain normal behavior for every other call.
"""

from __future__ import annotations

import os
import sys
from functools import wraps
from typing import Any


def _is_windows() -> bool:
    return sys.platform == "win32"


def install_windows_geoip_reader() -> None:
    """Apply once, before Camoufox's own GeoIP lookup can run.

    The upstream lookup imports maxminddb inside its function, so adjusting
    this entry point covers both browser launches and proxy checks without
    copying Camoufox's IP/location/configuration logic or changing its files.
    """
    if not _is_windows():
        return
    import maxminddb

    original = maxminddb.open_database
    if getattr(original, "_cpm_windows_unicode", False):
        return

    @wraps(original)
    def open_database(database: Any, mode: int = maxminddb.MODE_AUTO) -> Any:
        if mode == maxminddb.MODE_AUTO and isinstance(database, (str, os.PathLike)):
            filename = os.fspath(database)
            if isinstance(filename, str) and not filename.isascii():
                mode = maxminddb.MODE_MMAP
        return original(database, mode)

    open_database._cpm_windows_unicode = True  # type: ignore[attr-defined]
    maxminddb.open_database = open_database
