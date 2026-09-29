# Roadmap

This is a rough, non-binding plan. Priorities may change based on feedback.
Track progress in [GitHub Issues](https://github.com/xinian5216/fingerprint-lite/issues).

## Since 0.2.0

- The browser tests assert what a *page* sees, not what automation sees. Three
  timezone assertions read through `page.evaluate`, which since Camoufox
  152.0.4-beta.29 runs in an isolated world with a realm of its own — so they
  reported the host's zone while real pages were correctly spoofed. Verified
  green on beta.28, beta.29 and beta.30.
- Proxy health is visible in the profiles list: a check leaves its answer in the
  row — exit address, country, latency, and a green, amber or red dot — and a
  selection can be checked in one go. On demand only; nothing polls.
- The browser suite no longer touches the internet: pages come from a loopback
  server, and the exit address a launch would look up is named instead. A run
  can no longer fail on someone else's rate limit.
- The web UI runs on Next 16 and ESLint 10, with linting moved from the removed
  `next lint` to the ESLint CLI and the `FlatCompat` shim dropped for the native
  flat config.

## Shipped in 0.2.0

- Profiles keep one pinned machine across launches, instead of resolving a new
  fingerprint every start.
- Profiles can be created from Camoufox's real device presets.
- A whole profile — settings, pinned fingerprint and browser data — exports to a
  single archive and imports elsewhere.
- A profile's pin can be moved onto a newer browser without changing its
  hardware, so a long-lived profile does not keep advertising the version it was
  created with.
- A proxy can be checked from the UI: where it comes out, how fast, and whether
  the profile's timezone and coordinates agree with it.
- A profile that contradicts itself says so and can be fixed: an OS setting that
  disagrees with the pinned machine is reported with both ways out, and a profile
  carrying geography from before it followed the proxy can have it cleared, one
  profile or a whole selection at a time.
- The web UI covers profiles and a settings screen reporting how the instance is
  configured. Group records and profile assignments remain available through
  the REST API; group-management controls are intentionally hidden in the web UI.
- The REST API is stabilised for `1.0`: `/api/v1` paths (the unversioned ones
  stay as aliases), one error shape everywhere, typed responses on every route,
  and a written stability contract in [api.md](api.md) saying what freezes and
  what deliberately does not.
- The parts that delete profile directories and run browsers are under test, and
  the bugs that turned up in doing it are fixed.
  What is deliberately left uncovered, and why, is written down in
  [CONTRIBUTING.md](../CONTRIBUTING.md#what-is-deliberately-not-covered).
- Authentication beyond the API key: user accounts (`camoufox-pm user add`),
  argon2id password hashes, HttpOnly session cookies with a real server-side
  logout, and a login screen in the web UI. The three states stay coherent —
  nothing configured is as open as before (loopback), the API key keeps
  machine clients working unchanged, and the moment a user exists humans must
  log in. See [SECURITY.md](../SECURITY.md#authentication).
- Task scheduling, in-process and stored in the database: open a profile's
  browser on a schedule (with an optional self-closing session length), and
  move its pinned fingerprint onto the installed browser version on a schedule.
  Runs missed while the app was closed are recorded and skipped, not replayed.
  This item was originally written as "task scheduling and automated
  fingerprint rotation"; the second half was **deliberately not built**. The
  wording predated pinned fingerprints, when the hardware changed every launch
  anyway — now rotating it on a timer would hand a warmed-up account a new
  machine overnight, which is exactly what the pin exists to prevent. Hardware
  regeneration stays a manual action; the scheduled browser-version refresh is
  the rotation that imitates what real machines do. See
  [scheduling.md](scheduling.md).

## Next

Everything planned for `0.2.0` shipped. These are the known follow-ups, in rough
order of how much they would hurt if left alone:

- Reconcile the deprecations before `1.0`: the flattened `browser_*` update
  fields, `browser_session_id`, and the top-level `detail` in errors all go away
  in `1.0`, and the unversioned `/api` prefix in `2.0`. See the deprecation table
  in [api.md](api.md#stability-contract).
- **TypeScript 7 waits on the tools, not on us.** The UI is on TypeScript 6.0.3,
  the last TypeScript line with a JavaScript compiler API. TypeScript 7 is the Go
  rewrite: its package ships a `tsc` binary and no API — `require("typescript")`
  has no `createProgram` — and both `typescript-eslint` and Next's build-time
  type check need that API. TypeScript documents a side-by-side install that
  keeps the 6.0 API for tooling, but here it would buy only a faster standalone
  `tsc` that CI never runs, for ~30 MB of platform binaries. Worth revisiting
  when `typescript-eslint` supports TS 7 (their issue #10940) and Next type-checks
  through it.

## Later / ideas

- PyPI package once the browser-binary bootstrap is smoothed out.
- Published Docker images.
- Internationalised (i18n) web UI.
- PostgreSQL storage for larger deployments.
- Encrypted profile archives, so an export is not as sensitive as the account.

## Not planned

- **Chromium-based profiles.** This is a Camoufox manager, and Camoufox is
  Firefox. A Chrome fingerprint is a different product.
- **Mobile profiles.** Camoufox is desktop Firefox; Android and iOS profiles are
  out of reach.
- Bundling or redistributing the Camoufox browser binary — it is fetched from
  upstream.
- Anything that facilitates violating a site's terms of service.

## Canvas stability

By default a site sees a different canvas hash each time a profile is launched.
Within one session it is stable per site; the problem is only across sessions,
where a real browser would be unchanging.

**Done: the `stable_canvas` per-profile setting.** Turning it on launches with
`privacy.baselineFingerprintingProtection = false`, which together with the
pinned `fonts:spacing_seed` makes the canvas reproducible across launches. Off by
default, because the trade is that the canvas is then the same across sites — what
a real machine looks like, and what the randomisation existed to prevent. Covered
by browser tests that assert the stability, the drift when it is off, and the
cross-site linkability it costs.

**Reported upstream, still open:
[daijro/camoufox#721](https://github.com/daijro/camoufox/issues/721).** `canvas:seed`
is advertised in Camoufox's property manifest and emitted by its Python layer, but
its C++ config reader never reads it and no patch implements it;
`window.setCanvasSeed()` is documented but absent from the shipped build. A working
seed would be better than the pref, because it would give each profile its own
canvas value instead of one shared true render.

Checked again on 2026-08-20: no answer and no change — `MaskConfig.hpp` at
upstream `HEAD` still does not mention canvas at all. Not a stalled project, just
a busy one; there are 57 commits between `beta.28` and `beta.29`. Nothing to do
but keep the pref and re-check on each release. A fork is not the answer — it
would mean building and hosting Firefox for every platform and rebasing on every
Camoufox release, for one seed value.

See [profile-settings.md](profile-settings.md#known-limitations) for the measured
behaviour and the trade-off table.
