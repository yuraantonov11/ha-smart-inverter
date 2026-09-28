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

## [1.8.13-perf-fixes] - 2026-09-28

### Changed
- Performance fixes (local, not yet pushed to GitHub)

## [1.8.11] - 2026-06-26

### Changed
- Combined generation + forecast on single chart

[Unreleased]: https://github.com/yuraantonov11/ha-smart-inverter/compare/v1.8.11...HEAD
[1.8.13-perf-fixes]: https://github.com/yuraantonov11/ha-smart-inverter/releases/tag/v1.8.11
[1.8.11]: https://github.com/yuraantonov11/ha-smart-inverter/releases/tag/v1.8.11
