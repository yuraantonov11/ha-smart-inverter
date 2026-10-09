# R06 + R07 + R08 — audit status (2026-10-09)

> Status updates and evidence for the
> R06–R08 block, layered on top of the
> R04+R05 deliverable at `96b2337`. Each
> item is marked **DONE / PARTIAL /
> DEFERRED / NOT VERIFIED** with concrete
> evidence (commit SHA, test name, file
> path).

## Commit map (R04–R08)

| Commit  | Scope                                              |
|---------|----------------------------------------------------|
| 96b2337 | R04+R05 production-path tariff (prior block)        |
| 4e02491 | R04+R05 setup follow-up: cold-start, real listener  |
| 2121416 | R06: frontend card correctness                      |
| b6dd024 | R07: auth, device selection, log safety             |
| (R08)   | R08: demand model, EWMA docs, copy hygiene          |

---

## R06 — frontend correctness & component coverage

| Item | Status | Evidence |
|------|--------|----------|
| Inventory of cards in production | DONE | See "Card inventory" below |
| Numeric 0 distinct from null/NaN/unavailable | DONE | `tests/test_power_history_card_r06.cjs::test_zero_is_a_value` |
| Empty / non-numeric arrays handled | DONE | `test_empty_arrays` |
| Gaps in history do NOT bridge missing intervals | DONE | `test_gaps_start_new_path` (line chart with 30 values, 2 gaps, M-count = 3) |
| Actual not drawn in future (forecast vs actual) | DONE | `frontend/pv-comparison-card.js` uses `t <= now` guard; `test_pv_comparison_card.cjs` |
| Two instances/entries isolated | DONE | `power-history-card.js` builds unique DOM id from `setConfig`; no shared listeners |
| Resize/reconnect listeners cleaned up | DONE | `power-history-card.js` `disconnectedCallback` removes `ResizeObserver`; `test_observer_cleanup` |
| Dynamic text with `< > " &` escaped | DONE | `power-history-card.js::_escape`; `test_html_injection_escaped` |
| Tooltip/legend: source / cadence / smoothing | PARTIAL | `pv-comparison-card.js` shows "v2 forecast (radiation_contract_version=2)"; `power-history-card.js` shows series legend with units. **Hardware PV limit UNKNOWN — no "source" string for PV cap** |
| Comparison with Siseli: same date/timezone/units/aggregation | DONE | `pv-comparison-card.js` aligns by local-time hour and uses `forecast_diagnostic.sample_row` only when model tag matches (`hourly_response_v2`) |

### Card inventory

| Card | Production file | Registration | Data source | Units | Cadence | Existing tests |
|------|-----------------|--------------|-------------|-------|---------|----------------|
| `power-history-card` | `www/power-history-card.js` (also `frontend/`) | `window.customCards.push(...)` | `sensor.*.hourly_power_kw` | kW | per `set hass` (state-change) | `test_pv_comparison_card.cjs` (sibling); `test_power_history_card_r06.cjs` (new) |
| `pv-comparison-card` | `frontend/pv-comparison-card.js` | `window.customCards.push(...)` | `forecast_diagnostic` + `*_hour_kw` | kW | per `set hass` | `test_pv_comparison_card.cjs` |
| `energy-flow-card` | `frontend/energy-flow-card.js` | `window.customCards.push(...)` | `sensor.*` realtime | W | per `set hass` | covered by sibling test |
| `k-flow-card` | `frontend/k-flow-card.js` | `window.customCards.push(...)` | `sensor.*` realtime | kW | per `set hass` | covered by sibling test |
| `forecast-card` | `frontend/forecast-card.js` | `window.customCards.push(...)` | `forecast_diagnostic` | kW | per `set hass` | `test_pv_comparison_card.cjs` covers date/timezone/aggregation |
| `total-energy-card` | `frontend/total-energy-card.js` | `window.customCards.push(...)` | `sensor.*_energy_total` | kWh | per `set hass` | NOT covered — **DEFERRED to R09** |

### Defects fixed in R06

- **HTML injection in `power-history-card.js`**:
  `${title}`, `${lbls[i]}`, `${vals[i]}`
  were inserted into `innerHTML` without
  escaping. Fixed by adding `_escape` and
  routing every text node through it.
  Test: `test_html_injection_escaped` (PASS).
- **Gap bridging in `power-history-card.js`**:
  the line-chart path emitted a single
  continuous `L`-only polyline even when
  labels were missing. Fixed by detecting
  gaps in the raw array and starting a
  new `M` sub-path. Test:
  `test_gaps_start_new_path` (PASS).
- **First iteration started with `L`**:
  the first segment of a line was emitted
  with `L` instead of `M` because the
  prev-raw sentinel was set to `-1` (which
  is adjacent to the first valid index 0).
  Fixed with a `firstInRun` flag. Test:
  `test_gaps_start_new_path` (PASS).
- **`ResizeObserver` not removed on
  `disconnectedCallback`**: would leak
  listeners. Fixed by tracking the
  observer and disconnecting in the
  lifecycle hook. Test:
  `test_observer_cleanup` (PASS).

---

## R07 — authentication & device selection

| Item | Status | Evidence |
|------|--------|----------|
| 32-character plain (non-hex) password NOT auto-treated as MD5 | DONE | `api.py::_is_pre_hashed_password`; `test_plain_32_char_password_is_NOT_pre_hashed` |
| Lowercase 32 hex IS pre-hashed | DONE | `test_lowercase_32_hex_is_pre_hashed` |
| Mixed-case 32 hex IS pre-hashed (legacy input) | DONE | `test_mixed_case_32_hex_is_pre_hashed` — contract documented as "lowercase 32 hex chars" with mixed-case legacy support |
| Mock endpoint: plain / prehashed / mixed-case / invalid | DONE | tests above (4 cases) + `test_short_string_not_pre_hashed` + `test_long_string_not_pre_hashed` |
| Tests do NOT use real credentials | DONE | All passwords are synthetic strings (`a1b2c3d4...`, `z * 32`, etc.) |
| Password/token never logged | DONE | `test_password_not_logged_on_construction` + `test_token_not_logged_on_setter` |
| Device identity: config entry → API → coordinator → entities | DONE | `__init__.py:252` uses `api.device_sn` for `(DOMAIN, device_sn)` identifier; live SN = `448411180556320769` |
| `device_sn` / station binding / unique IDs / entity IDs preserved | DONE | No changes to `async_set_unique_id(api.device_sn)`; no entity ID renames |
| Multiple devices: explicit selection required | DONE | `_fetch_device_list(None)` raises when `len(devices) > 1`; `test_no_preference_multi_device_raises` |
| Missing preferred device ≠ devices[0] | DONE | `_fetch_device_list("Z")` with Z not in list raises; `test_preferred_not_in_list_raises` |
| Reordered device list still finds preferred | DONE | `test_reordered_list_still_finds_preferred` |
| Two entries scenario | DONE | `test_unique_id_per_device` + `test_unique_id_same_device_raises` |

### Ambiguous 32-character hex — documented contract

> When the password is 32 hex characters
> (lowercase OR mixed-case legacy form), it is
> treated as a pre-hashed MD5. This is the
> chosen compatibility contract to support
> legacy config entries that stored the MD5
> in plaintext. Plain 32-character passwords
> that happen to be 32 chars (e.g.
> `a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6`) are
> NOT hex and are therefore re-hashed
> correctly via `hashlib.md5`.

### Defects fixed in R07

- **32-char non-hex password was treated as
  pre-hashed**: the previous `if
  len(self._password) == 32:` branch sent
  the password unchanged. A 32-character
  plain password would therefore bypass the
  MD5 step. Fixed by requiring every char
  to be a hex digit
  (`_is_pre_hashed_password`). Test:
  `test_plain_32_char_password_is_NOT_pre_hashed`
  (PASS).
- **`devices[0]` always selected**:
  `_fetch_device_list` ignored any
  preference and silently bound to the
  first device. Fixed by accepting
  `preferred_device_sn` and either
  matching it or raising. Test:
  `test_preferred_not_in_list_raises`
  (PASS) + `test_no_preference_multi_device_raises`
  (PASS).
- **`fetch_realtime_data` re-fetch used
  no preference**: would also fall back to
  `devices[0]`. Fixed by passing the
  previously-bound `device_sn`. Test
  coverage via
  `test_reordered_list_still_finds_preferred`
  (PASS).

---

## R08 — demand model & documentation

| Item | Status | Evidence |
|------|--------|----------|
| Documented EWMA cadence and effective horizon | DONE | `hems/demand_forecast.py::update_ewma` docstring: "1/α samples per hour, not per wall-clock time" |
| Verified same load at different sample frequencies (after convergence) | DONE | `test_same_load_steady_state_independent_of_cadence` (60 vs 16 samples/hour, within 2%) |
| Documented low-cadence limitation (1 sample/hour does NOT converge) | DONE | `test_low_cadence_does_not_converge` (2737.5 vs 1950 expected) |
| Multipliers (0.8 / 1.0 / 1.2 / 1.35) are NOT quantiles | DONE | `hems/demand_forecast.py` docstring rewritten; `test_same_multiplier_for_every_hour` |
| `p25/p50/p75/p90` field names preserved (sensor contract) | DONE | `test_field_names_match_published_contract` |
| README / WORKFLOW / manifest / version / config fields / translations cross-checked | DONE | `manifest.json` v1.9.0 unchanged (no semantic change). Translations `en.json` + `uk.json` already have `nominal_voltage_v` from `96b2337` |
| No release tag created | DONE | no `git tag` issued; no push to `main` |
| Package-import test excludes `.local`, venv, backups, runtime journals | DONE | `tests/test_r04_integration_load.py::_build_isolated_load_path` ignore_patterns extended |

### Defects fixed in R08

- **Docstring called multipliers
  "probabilistic" / "empirical quantiles"**:
  the field names `p25/p50/p75/p90` are
  fixed multipliers (0.8, 1.0, 1.2, 1.35)
  of the EWMA mean, not quantiles of a
  sample distribution. Fixed by rewriting
  the module docstring and the
  `update_ewma` docstring. Test:
  `test_module_docstring_does_not_claim_empirical_quantiles`
  (PASS) + `test_same_multiplier_for_every_hour`
  (PASS).
- **Clamp test expected exact clamp**:
  the EWMA clamps the SAMPLE, not the
  stored value, so the test
  `assertEqual(profile[12], _MIN_LOAD_W)`
  was wrong. Fixed by recomputing the
  one-step EWMA value
  `α × 100 + (1-α) × 500 = 400` and the
  100-step converged value
  `≈ 100`. Tests
  `test_min_load_clamp` and
  `test_max_load_clamp` (PASS).
- **Steady-state test used 1 sample per
  hour**: that is not enough to converge
  (only one EWMA step per hour). Fixed
  by using 16 samples per hour (4× the
  time constant) and verifying 2%
  tolerance. Test
  `test_same_load_steady_state_independent_of_cadence`
  (PASS) + new test
  `test_low_cadence_does_not_converge`
  documents the limitation (PASS).
- **Package-import test copied everything
  in the integration root**:
  `shutil.copytree` with
  `ignore=ignore_patterns(".test-venv",
  "__pycache__", ".git", "tests",
  "node_modules")` did not exclude
  `.local`, venv variants, backup
  archives, or runtime journals. Fixed
  by extending the ignore set. The
  dynamic integration load still passes.

---

## Final test counts (R04–R08)

| Suite | Count | Status |
|-------|-------|--------|
| `tests/test_r04_r05_acceptance.py` | 46 | PASS |
| `tests/test_r04_integration_load.py` | 3 | PASS |
| `tests/test_r07_authentication.py` | 16 | NEW (R07) |
| `tests/test_r08_demand_model.py` | 8 | NEW (R08) |
| JS: `tests/test_pv_comparison_card.cjs` | 1 | PASS |
| JS: `tests/test_power_history_card_r06.cjs` | 1 | NEW (R06) |
| All Python suites (runner) | 80 | PASS |
| Compileall | clean | |
| `git diff --check` | clean | |

## Items marked **NOT VERIFIED** or **DEFERRED**

- `first_v2_completed_pair` — NOT_YET_VERIFIED
  (needs ≥18-24h of Oct 9 data). The
  contract is defined in
  `hems/pv_learning.py::RealForecastPairs` and
  the probe validator
  (`scripts/probe_r01_live.py`) checks
  both journals. No defect suspected; just
  waiting on the data.
- `pv_max_w` (hardware PV limit) — UNKNOWN.
  The API does not expose it; the
  integration cannot infer it. **Not
  blocking** R04–R08; **R09 candidate** if
  a different signal can be sourced.
- `total-energy-card` JS test — DEFERRED to
  R09. The card exists and renders totals,
  but no behavioral test exists yet. R06
  does not require it.
- Hardware PV "source" string in tooltip —
  PARTIAL (no value, not blocking).
