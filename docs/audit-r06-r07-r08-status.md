# R06 + R07 + R08 — audit status (2026-10-09, updated 2026-10-09 evening)

> Status updates and evidence for the
> R06–R08 block. Each item is marked
> **DONE / PARTIAL / DEFERRED / NOT
> VERIFIED** with concrete evidence
> (commit SHA, test name, file path).
> Items are not "shipped" via commit
> hash alone — each acceptance criterion
> must be backed by a real test name
> AND a real file path.

## Commit map (R06–R08)

| Commit  | Scope                                                       |
|---------|-------------------------------------------------------------|
| 2121416 | R06 base: card correctness (escape, gap, observer cleanup)  |
| b6dd024 | R07: auth, device selection, log safety                      |
| 0ec10cb | R08: demand model docs, EWMA cadence                         |
| 3a7355f | R07 follow-up: device identity preservation                  |
| 20a126f | R06 follow-up: line-chart x by rawIndex, total-energy escape |
| 789a1b2 | R07 follow-up: production config flow, reauth, legacy unique_id, total-energy Infinity fix, R08 cadence corrections (this commit) |

---

## R06 — frontend correctness & component coverage

| Item | Status | Evidence |
|------|--------|----------|
| `power-history-card` zero / null / NaN / unknown | **DONE** | `tests/test_power_history_card_r06.cjs::test_zero_handling`, `::test_null_handling` |
| `power-history-card` empty / non-numeric arrays | **DONE** | `tests/test_power_history_card_r06.cjs::test_empty_array`, `::test_non_numeric_array` |
| `power-history-card` gaps start new path (M-count) | **DONE** | `tests/test_power_history_card_r06_followup.cjs::test_yura_30_point_fixture` (30 raw + gaps 15, 20, 22 → 27 valid, 4 M, 23 L, last tooltip preserved) |
| `power-history-card` x-position by rawIndex (NOT cleaned index) | **DONE** | `tests/test_power_history_card_r06_followup.cjs::test_x_uses_rawIndex_not_cleaned_index` |
| `power-history-card` shared axis = longest series' raw length | **DONE** | `tests/test_power_history_card_r06_followup.cjs::test_shared_axis_uses_longest_series` (rawIndex 14 → x ≈ 259.8) |
| `power-history-card` tooltip for every valid point (incl. isolated after gap) | **DONE** | `tests/test_power_history_card_r06_followup.cjs::test_tooltip_after_gap_includes_isolated_point` |
| `power-history-card` two-series tail gap, last x at right edge | **DONE** | `tests/test_power_history_card_r06_followup.cjs::test_yura_30_point_fixture_with_tail_null` (last x = 490) |
| `power-history-card` HTML escape (title, labels) | **DONE** | `tests/test_power_history_card_r06_followup.cjs::test_html_injection_escaped` |
| `power-history-card` ResizeObserver cleanup | **DONE** | `tests/test_power_history_card_r06.cjs::test_resize_observer_cleanup` |
| `power-history-card` Cadence honesty (no fabricated "30 min") | **DONE** | `tests/test_power_history_card_r06_followup.cjs::test_cadence_unknown_not_defaulted`, `::test_cadence_from_config_is_used` |
| `total-energy-card` HTML escape | **DONE** | `tests/test_cards_r06_siblings.cjs::test_total_energy_html_escape` |
| `total-energy-card` zero / unknown / MWh | **DONE** | `tests/test_cards_r06_siblings.cjs::test_total_energy_zero_unknown_unavailable` |
| `total-energy-card` real zero preserved as "0.00 kWh" | **DONE** | `tests/test_cards_r06_siblings.cjs::test_total_energy_real_zero_preserved` |
| `total-energy-card` Infinity state / attribute → "—" | **DONE** | `tests/test_cards_r06_siblings.cjs::test_total_energy_infinity_state_does_not_render_infinity_mwh`, `::test_total_energy_infinity_attribute_does_not_render_infinity_mwh` |
| `total-energy-card` two instances isolated | **DONE** | `tests/test_cards_r06_siblings.cjs::test_total_energy_two_instances` |
| `total-energy-card` lifecycle safe | **DONE** | `tests/test_cards_r06_siblings.cjs::test_total_energy_lifecycle` |
| `forecast-card` zero vs unknown | **DONE** | `tests/test_cards_r06_siblings.cjs::test_forecast_card_zero_vs_unknown` |
| `forecast-card` two instances isolated | **DONE** | `tests/test_cards_r06_siblings.cjs::test_forecast_card_two_instances_isolated` |
| `forecast-card` HTML escape | **DONE** | `tests/test_cards_r06_siblings.cjs::test_forecast_card_html_escape` |
| `forecast-card` lifecycle (no throw) | **DONE** | `tests/test_cards_r06_siblings.cjs::test_forecast_card_loads_with_empty_hass` |
| `forecast-card` known entity renders content | **DONE** | `tests/test_cards_r06_siblings.cjs::test_forecast_card_renders_with_known_entity` |
| `k-flow-card` two instances isolated | **DONE** | `tests/test_cards_r06_siblings.cjs::test_k_flow_card_two_instances_isolated` |
| `k-flow-card` HTML escape | **PARTIAL** | `tests/test_cards_r06_siblings.cjs::test_k_flow_card_html_escape` (Node harness only; full assertion in browser) |
| `energy-flow-card` two instances isolated | **DONE** | `tests/test_cards_r06_siblings.cjs::test_energy_flow_card_two_instances_isolated` |
| `energy-flow-card` HTML escape | **PARTIAL** | `tests/test_cards_r06_siblings.cjs::test_energy_flow_card_html_escape` (Node harness only; full assertion in browser) |
| `pv-comparison-card` source/cadence honesty | **DONE** | `tests/test_pv_comparison_card.cjs` (Cadence: unknown (not in config) when absent) |
| Mobile / browser visual / resize / reconnect | **NOT VERIFIED** | Out of scope for Node harness. Browser-based verification deferred. |
| Unique DOM IDs per instance | **PARTIAL** | The cards currently rely on card-instance `this` for state isolation. No global DOM-id registry. Deferred. |

---

## R07 — authentication & device selection

| Item | Status | Evidence |
|------|--------|----------|
| MD5 contract: lowercase 32-char hex → pre-hashed | **DONE** | `tests/test_r07_authentication.py::TestPasswordHashing::test_lowercase_32_hex_is_pre_hashed` |
| MD5 contract: mixed-case 32-char hex → pre-hashed (lowered) | **DONE** | `tests/test_r07_authentication.py::TestPasswordHashing::test_mixed_case_32_hex_is_pre_hashed` |
| MD5 contract: 32-char non-hex → plain (NOT auto-MD5) | **DONE** | `tests/test_r07_authentication.py::TestPasswordHashing::test_plain_32_char_password_is_NOT_pre_hashed` |
| MD5 contract: 31/33-char → plain | **DONE** | `tests/test_r07_authentication.py::TestPasswordHashing::test_short_31_char_is_NOT_pre_hashed`, `::test_long_33_char_is_NOT_pre_hashed` |
| MD5 contract boundary: leading space / sign / underscore | **DONE** | `tests/test_r07_authentication.py::TestPasswordHashing::test_leading_space_not_pre_hashed`, `::test_leading_sign_not_pre_hashed`, `::test_underscore_in_value_not_pre_hashed` |
| MD5 contract: actual login payload sent | **DONE** | `tests/test_r07_authentication.py::TestLoginEndpointActualPayload::test_plain_short_password_hashed_with_md5`, `::test_lowercase_32_hex_sent_as_is`, `::test_mixed_case_32_hex_sent_lowercased`, `::test_leading_space/sign/underscore_32_chars_hashed_with_md5` (3 cases) |
| Log safety: 401 login error doesn't log password | **DONE** | `tests/test_r07_authentication.py::TestLoginEndpointActualPayload::test_login_error_does_not_log_password` |
| Log safety: reauth doesn't log password | **DONE** | `tests/test_r07_authentication.py::TestLoginEndpointActualPayload::test_reauth_does_not_log_password` |
| `authenticate(preferred_device_sn)` parameter | **DONE** | `api.py::authenticate(self, preferred_device_sn=None, *, skip_device_list=False)` |
| `_fetch_device_list(preferred_device_sn)` raises on missing preference | **DONE** | `api.py::_fetch_device_list`: explicit raise when preferred is not in list; no silent `devices[0]` fallback |
| `_list_devices()` parallel helper (no binding) | **DONE** | `api.py::_list_devices` |
| `_ensure_authenticated()` preserves `_selected_device_sn` | **DONE** | `api.py::_ensure_authenticated` calls `authenticate()` which uses `self._selected_device_sn` |
| Config flow `[A, B]` → picker → select B (production path) | **DONE** | `tests/test_r07_config_flow_production.py::TestConfigFlowProduction::test_multi_device_picker_executes_production_flow` |
| Config flow `[A]` auto-bind (no picker) | **DONE** | `tests/test_r07_config_flow_production.py::TestConfigFlowProduction::test_single_device_auto_binds` |
| Config flow reordered list [B, A] → select B | **DONE** | `tests/test_r07_config_flow_production.py::TestConfigFlowProduction::test_picker_with_reordered_list` |
| Config flow invalid selection → `invalid_device` error | **DONE** | `tests/test_r07_config_flow_production.py::TestConfigFlowProduction::test_picker_invalid_selection_shows_error` |
| Config flow numeric id normalised to string | **DONE** | `tests/test_r07_config_flow_production.py::TestConfigFlowProduction::test_picker_numeric_id_normalised_to_string` |
| Config flow pending client closed on auth failure | **DONE** | `tests/test_r07_config_flow_production.py::TestConfigFlowProduction::test_pending_client_closed_on_auth_failure` |
| Config flow picker does NOT re-fetch device list (uses cache) | **DONE** | `config_flow.py::async_step_select_device` (no `_fetch_device_list` call; uses `self._pending_devices`) |
| Reauth passes saved SN; missing device → `reauth_failed_device_missing` (no `entry.data` mutation) | **DONE** | `tests/test_r07_config_flow_production.py::TestConfigFlowProduction::test_reauth_preserves_binding_when_device_missing` |
| Reauth success: updates credentials AND preserves `selected_device_sn` | **DONE** | `tests/test_r07_config_flow_production.py::TestConfigFlowProduction::test_reauth_succeeds_when_device_present` |
| Reauth legacy entry without `selected_device_sn` uses `entry.unique_id` | **DONE** | `tests/test_r07_config_flow_production.py::TestConfigFlowProduction::test_legacy_entry_uses_unique_id_as_fallback` |
| Re-adding B after deletion: `async_set_unique_id` + `_abort_if_unique_id_configured` | **DONE** | `tests/test_r07_config_flow_production.py::TestConfigFlowProduction::test_duplicate_device_sn_raises_in_production_flow` |
| Translations (uk.json, en.json) for new step + errors | **DONE** | `translations/uk.json::config.step.select_device`, `translations/en.json::config.step.select_device` |
| `strings.json` keys for new step + errors + aborts | **DONE** | `strings.json::config.step.select_device`, `::config.step.reauth`, `::config.error.invalid_device`, `::config.error.reauth_failed_device_missing`, `::config.abort.reauth_successful` |

---

## R08 — demand model & documentation accuracy

| Item | Status | Evidence |
|------|--------|----------|
| EWMA cadence: `update_ewma` runs once per polling cycle (default 5s) | **DONE** | `hems/demand_forecast.py::update_ewma` docstring; coordinator polls at `DEFAULT_POLL_INTERVAL_SEC` (5s) |
| 1/α = 4 in SAMPLES (not "samples per hour" — that was a docstring bug) | **DONE** | `tests/test_r08_demand_model.py::test_horizon_in_docstring` (text "1/α = 4 samples" present) |
| Multipliers (0.8/1.0/1.2/1.35) are HEURISTIC, NOT empirical quantiles | **DONE** | `hems/demand_forecast.py::to_demand_forecast` docstring ("heuristic multiplicative spread"). `tests/test_r08_demand_model.py::test_to_demand_forecast_docstring_heuristic` |
| NO 1-sigma envelope provenance claim | **DONE** | Removed from `hems/demand_forecast.py::to_demand_forecast` and module docstring. Field names p25/p50/p75/p90 are retained for sensor-contract compatibility but documented as "placeholders for future sample-based calibration". |
| NO `demand_forecast_method` sensor-attribute claim | **DONE** | The previous `tests/test_r08_demand_model.py::test_to_demand_forecast_docstring_does_not_claim_gauss` was REPLACED with `::test_to_demand_forecast_docstring_heuristic` which asserts the new wording. |
| Same load at different sample rates → same steady state | **DONE** | `tests/test_r08_demand_model.py::test_same_load_steady_state_independent_of_cadence` |
| Transient response: 1000W → 2000W step, count to 50% | **DONE** | `tests/test_r08_demand_model.py::test_transient_response_under_load_change` |
| Min/Max load clamp | **DONE** | `tests/test_r08_demand_model.py::test_min_load_clamp`, `::test_max_load_clamp` |
| p25/p50/p75/p90 field names retained for sensor contract | **DONE** | `tests/test_r08_demand_model.py::test_to_demand_forecast_field_names` |
| Version reconciliation (root `__version__` vs manifest) | **DEFERRED** | Per Юра's instruction: no release tag. |
| Doc / translation sync (README, WORKFLOW, manifest, config fields) | **PARTIAL** | Translation keys for new step + errors added in this block. README/WORKFLOW reconciliation deferred. |

---

## Open items (NOT YET VERIFIED or DEFERRED)

| Item | Status | Note |
|------|--------|------|
| `first_v2_completed_pair` (real-forecast + actual pair, both v2) | **NOT YET VERIFIED** | ≥18–24h of production data required. Not blocking R06–R08. |
| Hardware PV limit (`pv_max_w`) | **UNKNOWN** | API does not expose it. |
| Mobile / browser visual / resize / reconnect | **NOT VERIFIED** | Node harness only. Browser path deferred. |
| Unique DOM IDs per card instance | **PARTIAL** | No global registry. |
| Root `__version__` ↔ manifest reconciliation | **DEFERRED** | Per Юра's instruction: no release tag. |
| README / WORKFLOW rewrite | **DEFERRED** | Per scope. |

---

## Honesty rules

- "Browser/mobile" is **NOT VERIFIED** by the Node harness. The harness
  tests what it can (state isolation, escape, two-instance
  non-contamination, lifecycle). Visual / resize / reconnect are
  explicitly out of scope.
- "Sibling component" tests do NOT cover all sibling components
  equally. The forecast/energy-flow/k-flow cards use shadow DOM
  with `getElementById` / `querySelectorAll`; the Node harness
  stubs the entry points but cannot exercise the full render
  path. Their full verification requires a real browser.
- `pv-comparison-card` has a dedicated suite
  (`tests/test_pv_comparison_card.cjs`) with real data fixtures.
  The siblings share the generic
  `tests/test_cards_r06_siblings.cjs`. Coverage of forecast
  / energy-flow / k-flow is more limited than the
  pv-comparison coverage — that is HONESTY, not a bug.
- The "multi-device flow" is **DONE** for the production
  `async_step_user → async_step_select_device` path, evidenced
  by the 10 tests in
  `tests/test_r07_config_flow_production.py`. It is NOT
  verified by a live HA instance end-to-end (HA's
  `_abort_if_unique_id_configured` is stubbed; the production
  path goes through it but the test only asserts the wiring).
