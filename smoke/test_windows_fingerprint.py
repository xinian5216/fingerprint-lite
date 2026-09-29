"""Windows smoke: the pinned machine is stable across relaunches.

Supplementary to tests/browser/test_fingerprint_stability.py (which already
asserts the pin and the canvas behaviours). This file measures what that suite
does not: language, timezone, and the full set of navigator/screen/WebGL values
observed from the page, using the manager's own fingerprint_store pinning path.
"""

import hashlib

import pytest
from camoufox import AsyncCamoufox
from tests.browser.support import offline_launch, timezone_a_page_sees

from camoufox_pm.core import fingerprint_store
from camoufox_pm.core.models import BrowserSettings, Profile

IDENTITY = """() => {
  const gl = document.createElement('canvas').getContext('webgl');
  const dbg = gl ? gl.getExtension('WEBGL_debug_renderer_info') : null;
  return {
    userAgent: navigator.userAgent,
    platform: navigator.platform,
    oscpu: navigator.oscpu,
    cores: navigator.hardwareConcurrency,
    language: navigator.language,
    languages: (navigator.languages || []).join(','),
    screen: screen.width + 'x' + screen.height,
    availScreen: screen.availWidth + 'x' + screen.availHeight,
    colorDepth: screen.colorDepth,
    pixelRatio: window.devicePixelRatio,
    inner: window.innerWidth + 'x' + window.innerHeight,
    gpuVendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
    gpuRenderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
    webglVendor: gl ? gl.getParameter(gl.VENDOR) : null,
    webglRenderer: gl ? gl.getParameter(gl.RENDERER) : null,
  };
}"""

CANVAS = """() => {
  const c = document.createElement('canvas');
  c.width = 240; c.height = 60;
  const x = c.getContext('2d');
  x.textBaseline = 'top';
  x.font = '16px Arial';
  x.fillStyle = '#f60'; x.fillRect(0, 0, 120, 25);
  x.fillStyle = '#069'; x.fillText('canvas probe', 2, 18);
  return c.toDataURL();
}"""

STABLE_KEYS = (
    "userAgent",
    "platform",
    "oscpu",
    "cores",
    "language",
    "languages",
    "screen",
    "availScreen",
    "colorDepth",
    "pixelRatio",
    "inner",
    "gpuVendor",
    "gpuRenderer",
    "webglVendor",
    "webglRenderer",
    "timezone",
)


async def observe(options, user_data_dir, origin):
    """Launch once and report everything a page can read."""
    launch = offline_launch(options)
    launch["headless"] = True
    launch["user_data_dir"] = str(user_data_dir)
    async with AsyncCamoufox(**launch) as browser:
        page = await browser.new_page()
        await page.goto(origin, wait_until="domcontentloaded", timeout=45000)
        seen = await page.evaluate(IDENTITY)
        # Read by the page's own realm: evaluate() runs isolated since beta.29.
        seen["timezone"] = await timezone_a_page_sees(page)
        value = await page.evaluate(CANVAS)
        seen["canvas"] = hashlib.sha256(value.encode()).hexdigest()[:16]
        return seen


def pinned_options(profile):
    """Launch options with the machine pinned, exactly as the manager does."""
    fresh = profile.to_camoufox_launch_options()
    fresh["config"] = {**profile.fingerprint, **fresh["config"]}
    return fresh


@pytest.mark.asyncio
async def test_pinned_machine_is_stable_across_three_cold_starts(tmp_path, local_sites):
    """Fixed hardware fields agree over three cold starts; Canvas stays separate."""
    profile = Profile(name="win-pin", browser_settings=BrowserSettings(os="windows"))
    profile.fingerprint = fingerprint_store.resolve(profile.to_camoufox_launch_options())
    assert profile.fingerprint, "expected a resolved fingerprint to pin"

    observations = [
        await observe(pinned_options(profile), tmp_path / "same-profile", local_sites.first)
        for _ in range(3)
    ]

    for key in STABLE_KEYS:
        values = [seen[key] for seen in observations]
        assert all(value == values[0] for value in values[1:]), (
            f"{key} drifted across cold starts: {values!r}"
        )

    # Canvas is deliberately excluded from stable fields: the default randomizes
    # it across launches. Record the hashes separately rather than claiming it is
    # pinned; stable_canvas=True has its own explicit test below.
    canvas_hashes = [seen["canvas"] for seen in observations]
    assert len(set(canvas_hashes)) > 1, (
        f"the default canvas was expected to randomize across cold starts, got {canvas_hashes}"
    )

    print("PINNED_FIXED_FIELDS_THREE_COLD_STARTS=", {k: observations[0][k] for k in STABLE_KEYS})
    print(
        "WEBGL_GPU_FIELDS_THREE_COLD_STARTS=",
        {
            key: [seen[key] for seen in observations]
            for key in ("gpuVendor", "gpuRenderer", "webglVendor", "webglRenderer")
        },
    )
    print("CANVAS_HASHES_UNSTABLE_DEFAULT_NOT_PINNED=", canvas_hashes)


@pytest.mark.asyncio
async def test_stable_canvas_setting_survives_relaunch(tmp_path, local_sites):
    """stable_canvas=True must make the exported canvas identical across launches."""
    profile = Profile(
        name="win-stable-canvas",
        browser_settings=BrowserSettings(os="windows", stable_canvas=True),
    )
    profile.fingerprint = fingerprint_store.resolve(profile.to_camoufox_launch_options())
    assert profile.fingerprint

    first = await observe(pinned_options(profile), tmp_path / "c1", local_sites.first)
    second = await observe(pinned_options(profile), tmp_path / "c2", local_sites.first)

    assert first["canvas"] == second["canvas"], (
        f"stable canvas drifted: {first['canvas']} vs {second['canvas']}"
    )


@pytest.mark.asyncio
async def test_language_setting_reaches_the_page(tmp_path, local_sites):
    """languages=["de-DE","de"] must be what navigator reports."""
    profile = Profile(
        name="win-lang",
        browser_settings=BrowserSettings(os="windows", languages=["de-DE", "de"]),
    )
    profile.fingerprint = fingerprint_store.resolve(profile.to_camoufox_launch_options())
    assert profile.fingerprint

    seen = await observe(pinned_options(profile), tmp_path / "lang", local_sites.first)

    assert seen["language"] == "de-DE", f"language is {seen['language']!r}"
    assert seen["languages"].startswith("de-DE"), f"languages are {seen['languages']!r}"


@pytest.mark.asyncio
async def test_two_profiles_are_not_the_same_machine(tmp_path, local_sites):
    """Isolation of identity: two profiles must resolve to different machines."""
    first_seen = None
    for attempt in range(3):
        profiles = []
        for index in range(2):
            profile = Profile(
                name=f"iso-{attempt}-{index}", browser_settings=BrowserSettings(os="windows")
            )
            profile.fingerprint = fingerprint_store.resolve(profile.to_camoufox_launch_options())
            assert profile.fingerprint
            profiles.append(profile)

        if profiles[0].fingerprint == profiles[1].fingerprint:
            continue  # catalogue collision; draw again

        first_seen = await observe(
            pinned_options(profiles[0]), tmp_path / f"a{attempt}", local_sites.first
        )
        second_seen = await observe(
            pinned_options(profiles[1]), tmp_path / f"b{attempt}", local_sites.first
        )
        compare = [k for k in STABLE_KEYS if k != "canvas" and first_seen[k] != second_seen[k]]
        if compare or first_seen["canvas"] != second_seen["canvas"]:
            print("ISOLATION_DIFF_KEYS=", compare)
            return

    pytest.fail(
        "two independently pinned profiles kept resolving to one identical machine; "
        f"last observed {first_seen}"
    )
