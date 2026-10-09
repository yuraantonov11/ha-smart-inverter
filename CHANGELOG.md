# Changelog

All notable changes to **Smart Solar Inverter** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Internal: reorganized to git workflow with branches and hooks
- Added `.gitignore` to exclude `*.bak`, `__pycache__`, secrets
- Added Git hooks: `pre-commit` (syntax/secret check), `pre-push` (tests/force-push protection), `post-commit` (HA reload)

### Added
- `bump_version.sh` helper script
- `WORKFLOW.md` developer guide

## [1.9.0] - 2026-10-09

### Changed
- Config flow split into 3 phases: credentials → device list (cached) → entry
- `add_extra_js_url` now skips URLs already present in the operator's
  `lovelace_resources` (custom cache-bust hash wins; integration no
  longer double-registers the same path)
- All 5 bundled frontend cards guard `customElements.define` with
  `if (!customElements.get(...))` to make double-load safe
- Root `__version__` aligned to `manifest.json` (1.9.0)
- Card escape fixes; total-energy Infinity handling; line chart shared axis;
  R08 EWMA docstring corrections

### Fixed
- Config flow no longer fails with `auth_failed` when the account has
  multiple devices — the picker is reached on the new `select_device` step
- Reauth preserves `selected_device_sn`; missing device aborts without
  mutating `entry.data`
- `_is_finite()` rejects `+Inf` / `-Inf` (was `True` for both — NaN-only
  check via `f == f`)

### Verified
- 82/82 Python suites + 4/4 JS suites PASS
- R07 follow-up #3+#4+#5 closed; R09 entry added (this release)

## [1.8.13-perf-fixes] - 2026-09-28



### Changed
- Performance fixes (local, not yet pushed to GitHub)

## [1.8.11] - 2026-06-26

### Changed
- Combined generation + forecast on single chart

[Unreleased]: https://github.com/yuraantonov11/ha-smart-inverter/compare/v1.8.11...HEAD
[1.9.0]: https://github.com/yuraantonov11/ha-smart-inverter/releases/tag/v1.9.0
[1.8.13-perf-fixes]: https://github.com/yuraantonov11/ha-smart-inverter/releases/tag/v1.8.11
[1.8.11]: https://github.com/yuraantonov11/ha-smart-inverter/releases/tag/v1.8.11
