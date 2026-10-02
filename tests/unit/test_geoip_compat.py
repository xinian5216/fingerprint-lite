"""Real MMDB reads through Camoufox from Windows Unicode directories."""

import inspect
import io
import shutil
from pathlib import Path

import maxminddb
import pytest
from camoufox import geolocation

from camoufox_pm import browser_env, geoip_compat

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/maxmind/GeoIP2-City-Test.mmdb"


@pytest.fixture
def windows_reader(monkeypatch):
    monkeypatch.setattr(geoip_compat, "_is_windows", lambda: True)
    # Session startup may already have installed the adapter on Windows.
    original = inspect.unwrap(maxminddb.open_database)
    monkeypatch.setattr(maxminddb, "open_database", original)
    return original


def test_real_camoufox_lookup_in_a_chinese_path(tmp_path, monkeypatch, windows_reader):
    folder = tmp_path / "下载" / "首次启动" / "Browser/cache/geoip/mmdb"
    folder.mkdir(parents=True)
    database = folder / "test-city-combined.mmdb"
    shutil.copyfile(FIXTURE, database)
    config = {
        "name": "test-city",
        "urls": {"combined": ["not-used"]},
        "paths": {
            "iso_code": "country.iso_code",
            "longitude": "location.longitude",
            "latitude": "location.latitude",
            "timezone": "location.time_zone",
        },
    }
    monkeypatch.setattr(geolocation, "MMDB_DIR", folder)
    monkeypatch.setattr(geolocation, "load_geoip_config", lambda: config)
    monkeypatch.setattr(geolocation, "download_mmdb", lambda: pytest.fail("no network needed"))
    # Report the unadapted reader's outcome on Windows, without treating it as
    # proof about the user's machine or relying on an upstream bug forever.
    try:
        with windows_reader(str(database)) as reader:
            assert reader.get("81.2.69.142")["country"]["iso_code"] == "GB"
        print("Unadapted AUTO Unicode read: PASS")
    except OSError as exc:
        print(f"Unadapted AUTO Unicode read: {type(exc).__name__}: {exc}")

    # Exercise application startup and Camoufox's real lookup, not a mock
    # replacement of the lookup or a test that only inspects the chosen mode.
    browser_env.ensure_geoip()
    geo = geolocation.get_geolocation("81.2.69.142")
    assert geo.locale.region == "GB"
    assert geo.timezone == "Europe/London"
    assert geo.latitude == pytest.approx(51.5142)
    assert geo.longitude == pytest.approx(-0.0931)
    print("Adapted Camoufox Unicode lookup: PASS")


@pytest.mark.parametrize(
    "database, requested, expected",
    [
        ("下载/test.mmdb", maxminddb.MODE_AUTO, maxminddb.MODE_MMAP),
        (Path("下载/test.mmdb"), maxminddb.MODE_AUTO, maxminddb.MODE_MMAP),
        ("ascii/test.mmdb", maxminddb.MODE_AUTO, maxminddb.MODE_AUTO),
        ("下载/test.mmdb", maxminddb.MODE_MMAP_EXT, maxminddb.MODE_MMAP_EXT),
        ("下载/test.mmdb", maxminddb.MODE_FILE, maxminddb.MODE_FILE),
        (b"raw-path.mmdb", maxminddb.MODE_AUTO, maxminddb.MODE_AUTO),
        (io.BytesIO(b"file-descriptor"), maxminddb.MODE_FD, maxminddb.MODE_FD),
    ],
)
def test_only_automatic_unicode_path_reads_change_mode(
    monkeypatch, windows_reader, database, requested, expected
):
    calls = []
    sentinel = object()

    def record(database, mode):
        calls.append((database, mode))
        return sentinel

    monkeypatch.setattr(maxminddb, "open_database", record)
    geoip_compat.install_windows_geoip_reader()
    assert maxminddb.open_database(database, mode=requested) is sentinel
    assert calls == [(database, expected)]


def test_installation_is_idempotent(windows_reader):
    geoip_compat.install_windows_geoip_reader()
    first = maxminddb.open_database
    geoip_compat.install_windows_geoip_reader()
    assert maxminddb.open_database is first


def test_other_platforms_keep_their_reader(monkeypatch, windows_reader):
    monkeypatch.setattr(geoip_compat, "_is_windows", lambda: False)
    geoip_compat.install_windows_geoip_reader()
    assert maxminddb.open_database is windows_reader


def test_missing_database_still_raises(tmp_path, windows_reader):
    geoip_compat.install_windows_geoip_reader()
    with pytest.raises(FileNotFoundError):
        maxminddb.open_database(str(tmp_path / "下载" / "missing.mmdb"))


def test_invalid_database_still_raises(tmp_path, windows_reader):
    database = tmp_path / "损坏.mmdb"
    database.write_bytes(b"not a database")
    geoip_compat.install_windows_geoip_reader()
    with pytest.raises(maxminddb.InvalidDatabaseError):
        maxminddb.open_database(str(database))
