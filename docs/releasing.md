# Releasing

## Alpha releases

`v0.1.0-alpha.2` packages the merged bilingual UI, download progress, browser
search and Unicode Windows GeoIP repairs as a Windows x64 portable ZIP.
Keep Alpha releases marked as pre-releases and retain previous releases for
rollback. Release publication requires the owner's explicit authorization.

`pyproject.toml` is the project version source. `web/package.json` and its lock
file mirror that version for the bundled UI; the API, desktop display, and
portable package name must be checked against it. The Camoufox browser version
is an independent pin and must not be changed when bumping Fingerprint Lite.

## Tagged releases (workflow behavior)

The `release.yml` workflow runs on `v*` tags. Pushing a tag builds a wheel and
source distribution and creates/updates a GitHub Release with those artifacts.

- **Pre-release gating.** Tags containing `-alpha`, `-beta`, or `-rc` (e.g.
  `v0.1.0-alpha.1`) are published with `prerelease: true` and `make_latest:
  false`, so they can never become the Stable/latest Release. Only plain
  version tags (e.g. `v0.2.0`) produce a normal Release.
- **PyPI stays opt-in.** The `publish-pypi` job runs only when the repository
  variable `PUBLISH_TO_PYPI` equals `true` (it is currently unset) *and* a
  Trusted Publisher is configured on PyPI. Otherwise no PyPI upload can happen.
- **Frozen platform binaries are attached by hand, never rebuilt by CI.** The
  workflow does not build the Windows portable ZIP. A frozen, machine-accepted
  ZIP (with its `SHA256SUMS.txt` and `RELEASE-MANIFEST.txt`) is uploaded to the
  Release with `gh release upload` after the tag run succeeds, so the download
  users get is byte-identical to the accepted artifact.

Do not push a tag until the owner separately authorizes that release; CI
success alone does not grant release authorization.

Before a future tagged release:

1. Move the finished notes from `Unreleased` into the exact version in
   `CHANGELOG.md` and update the project version in `pyproject.toml`, then mirror
   it in `web/package.json` and `web/package-lock.json`.
2. Run static checks and the required platform tests. Install any built wheel in
   a clean environment and verify `/health`, `/system/config`, and OpenAPI all
   report the same version as the package metadata.
3. Build from a clean output directory. Record the source commit, build time,
   PyInstaller version, Camoufox pin, filename, size, and SHA256 in the manifest.
4. Audit licenses and bundled notices, and review signing/SmartScreen status.

The PyPI job is inherited configuration and has not been prepared or authorized
for Fingerprint Lite. Keep it disabled unless PyPI ownership and publishing are
separately reviewed and approved.
