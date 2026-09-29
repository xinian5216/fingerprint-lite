# Releasing

## Phase 4A — Alpha preparation

`v0.1.0-alpha.1` is prepared as a Windows 10/11 x64 portable ZIP. This phase
does not create a tag, GitHub Release, or PyPI publication. The release branch
must receive explicit final authorization before any of those actions.

`pyproject.toml` is the project version source. `web/package.json` and its lock
file mirror that version for the bundled UI; the API, desktop display, and
portable package name must be checked against it. The Camoufox browser version
is an independent pin and must not be changed when bumping Fingerprint Lite.

## Future tagged releases

The inherited `release.yml` workflow runs on `v*` tags. Pushing a tag builds a
wheel and source distribution, creates/updates a GitHub Release, and uploads the
artifacts. If `PUBLISH_TO_PYPI=true`, it also attempts PyPI publishing. Do not
push a tag until the owner separately authorizes that release; CI success alone
does not grant release authorization.

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
