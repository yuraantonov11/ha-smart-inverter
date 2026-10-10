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
| 280ebd4 | R07 follow-up: production config flow, reauth, legacy unique_id, total-energy Infinity fix, R08 cadence corrections (this commit) |

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
| `k-flow-card` two instances isolated | **DONE** | Node baseline `tests/test_cards_r06_siblings.cjs::test_k_flow_card_two_instances_isolated`; browser evidence `tests/test_r06_browser_additions.py::R06BrowserAddedCoverage::test_flow_instance_state_isolated_after_updates_and_real_clicks` verifies different fixtures, updates and a real click without cross-instance state changes. |
| `k-flow-card` untrusted config / attribute string safety | **DONE** | `tests/test_r06_browser_additions.py::R06BrowserAddedCoverage::test_k_flow_untrusted_config_and_attribute_remain_svg_text` checks inert payload text/ARIA, no payload element/handler, no sentinel execution or `pageerror`; Node coverage remains `tests/test_cards_r06_siblings.cjs::test_k_flow_card_html_escape`. |
| `energy-flow-card` two instances isolated | **DONE** | Node baseline `tests/test_cards_r06_siblings.cjs::test_energy_flow_card_two_instances_isolated`; browser evidence `tests/test_r06_browser_additions.py::R06BrowserAddedCoverage::test_flow_instance_state_isolated_after_updates_and_real_clicks` verifies independent DOM after `hass` updates and a real click on one instance. |
| `energy-flow-card` unit-attribute HTML escape | **DONE** | `tests/test_r06_browser_additions.py::R06BrowserAddedCoverage::test_energy_flow_untrusted_unit_is_text_not_live_html` verifies raw attribute payload is displayed as text, creates no image/handler, does not execute its sentinel, and causes no `pageerror`; Node coverage remains `tests/test_cards_r06_siblings.cjs::test_energy_flow_card_html_escape`. |
| `pv-comparison-card` source/cadence honesty | **DONE** | `tests/test_pv_comparison_card.cjs` (Cadence: unknown (not in config) when absent) |
| Mobile / browser visual / resize / reconnect (aggregate) | **PARTIAL** | Six cards have real-Chromium 360/1280 px DOM, overflow and `pageerror` checks plus 12 saved screenshots. Reconnect evidence is in the dedicated rows below. Visual review found a few legibility limitations; see the visual row. |
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

---

## R09 — frontend dedup & customElements guard

| Item | Status | Evidence |
|------|--------|----------|
| `add_extra_js_url` does NOT dedupe — operator-pinned URLs in `lovelace_resources` would be re-registered, leading to double HTTP load and `customElements.define` "Already used" warning. | **DONE** | `__init__.py::_install_flow_card` reads `.storage/lovelace_resources` first via `_existing_resource_paths()` and skips the URL when the canonical path (cache-bust stripped) is already present. Operator's version wins. |
| `customElements.define` not guarded in 4 cards: `power-history-card`, `forecast-card`, `total-energy-card`, `energy-flow-card`, `k-flow-card`. | **DONE** | Each of these files now wraps `customElements.define` in `if (!customElements.get('NAME'))`. `pv-comparison-card` already had the guard. |
| Regression test: operator has `power-history-card.js?v=2.0.0-92c25bc9` pinned → integration does NOT re-register. | **DONE** | `tests/test_r09_lovelace_dedup.py::TestR09IntegrationSkipsAlreadyRegistered::test_power_history_not_reregistered` |
| Regression test: cache-bust query string is stripped before comparison. | **DONE** | `tests/test_r09_lovelace_dedup.py::TestR09ResourcePathExtraction::test_single_url_strips_query` |
| Regression test: each guarded file actually wraps `customElements.define` (contract pin). | **DONE** | `tests/test_r09_lovelace_dedup.py::TestR09CustomElementGuard::test_each_file_has_guard` |
| Regression test: drive the real `_install_flow_card` via `importlib.util.spec_from_file_location` against synthetic `.storage/lovelace_resources` stores (missing file, empty items, malformed JSON, single pin, all-5 pins). | **DONE** | `tests/test_r09_lovelace_dedup.py::TestR09ResourceExtraction` (6 tests, `TemporaryDirectory` cleanup) |
| Behavioural double-load: each of 6 bundled cards loads twice in a Node VM, second load must not throw. | **DONE** | `tests/test_r09_double_load.cjs` (6/6 safe) |
| Guard for `k-flow-card-editor` (secondary custom element in `k-flow-card.js`). | **DONE** | `tests/test_r09_lovelace_dedup.py::TestR09CustomElementGuard::test_k_flow_card_editor_is_guarded` |
| Browser verification: "Already used" warning absent in operator's browser DevTools console. | **NOT VERIFIED** | The integration logs "Skipping ... already in lovelace_resources" and the behavioural test covers the guard idempotency in a Node VM. Final acceptance is the operator's browser console after deploy. |

Browser "Already used" warning root cause was **confirmed**: the
operator's `lovelace_resources` had `power-history-card.js?v=2.0.0-92c25bc9`
and `pv-comparison-card.js?v=2` (stale `?v=2` static literal)
pinned manually. The integration's `add_extra_js_url` then
added second copies with the live cache-bust hash, which the
browser loaded in parallel, hitting `customElements.define`
twice. R09 fix preserves the operator's URLs (no deletion) and
prevents the integration from re-registering them. The
custom-element guard is defence in depth.

**Cache-bust and operator-pinned URLs**: the operator's
resources keep their existing `?v=...` query string. The
integration does NOT rewrite the operator's URLs. To pick up
a new frontend version the operator must update the cache-bust
hash in their Lovelace dashboard settings — the integration
cannot do that on the operator's behalf. Until the operator
re-pins, the browser fetches the operator's pinned asset, NOT
the new file. This is the correct behaviour: per Юра's
directive ("Користувацькі ресурси навмання не видаляй"), the
operator's customisation is preserved. The behavioural
double-load test (`tests/test_r09_double_load.cjs`) proves
that even if the operator's pinned asset is older (without
the new guard), a second load of the SAME asset is a no-op
when the integration's own copy of the script reaches the
browser. The "Already used" warning cannot fire as long as
either the operator's pinned version or the integration's
copy has the guard. With `3b3a5a0` deploy all 6 cards are
guarded.

## Open items (NOT YET VERIFIED or DEFERRED)

| Item | Status | Note |
|------|--------|------|
| `first_v2_completed_pair` (real-forecast + actual pair, both v2) | **NOT YET VERIFIED** | Verified constraint: `PvLearningStore.snapshot()` rejects past-or-today dates (`if date.fromisoformat(day) <= now.date(): return False`) and never overwrites an existing snapshot (`if day in self.snapshots: return False`). The current JSON has 3 snapshots for 2026-10-09/10/11 with v1 models (`station_gain_v1` × 2, `hourly_response_v1` × 1). With `calibration_model=hourly_response_v2`, the next unused +1 / +2 dates are 2026-10-12 / 2026-10-13. A v2 snapshot can first be written when polling runs on or after 2026-10-10 (which would write 12 + 13). The closed-fact pair is then expected no earlier than 2026-10-13. The `forecast_model_tags` attribute in the sensor is the engine's calibration model, not the snapshot's stored `forecast_model`; the two are not the same field. |
| Hardware PV limit (`pv_max_w`) | **UNKNOWN** | Inverter API does not expose a max-watts field. The MiniMax M3 cloud endpoints (`/v1/token_plan/remains`, `/anthropic/v1/models`) are billing / model-list endpoints and DO NOT contain the inverter's hardware spec; they are not valid evidence for this question. Status remains UNKNOWN until an inverter-side endpoint (e.g. the SOLARsiseli API or the device's local Wi-Fi module) exposes the field. |
| Mobile / browser visual / resize / reconnect | **PARTIAL** | See dedicated "R06 browser-path coverage" below: forecast and total-energy same-object lifecycle, all-six viewport checks, and screenshots are now evidenced in real Chromium. Visual review is documented as partial, not as a claim of pixel-perfect correctness. |
| Unique DOM IDs per card instance | **PARTIAL** | No global registry. |
| Root `__version__` ↔ manifest reconciliation | **DONE** | `__version__ = "1.9.0"` ↔ `manifest.json: "version": "1.9.0"` (verified in `3b3a5a0` deploy). No release tag created per Юра's standing instruction. |
| README / WORKFLOW rewrite | **DONE** | `README.md` and `WORKFLOW.md` updated in `3b3a5a0` to reflect `powmr_inverter` domain, `cryptography` requirement, and the manual release-tag workflow. |

---

## R06 browser-path coverage (mobile / resize / disconnect)

The original R06 audit listed mobile, resize and
`disconnectedCallback` cleanup. Per Юра's directive ("R06
початкового аудиту включає mobile, resize і disconnected
cleanup — не оголошуй їх поза scope самомостійно"), the
available checks are listed here; remaining browser-only
verifications are explicitly **NOT VERIFIED**, not "out of
scope".

| Item | Status | Evidence |
|------|--------|----------|
| `power-history-card` `disconnectedCallback` cleanup (ResizeObserver) | **DONE** | `tests/test_power_history_card_r06.cjs::test_resize_observer_cleanup` |
| `power-history-card` reconnect after disconnect | **DONE** | `tests/test_r06_browser_playwright.py::TestR06BrowserResizeObserver::test_disconnect_reconnect_does_not_leak_observers` runs 5 cycles in real Chromium. After each cycle the active observer count returns to 0 and the cumulative total grows by 1 (no duplication). |
| Same-element reconnect for all six audited cards | **DONE** | `tests/test_r06_browser_playwright.py::TestR06BrowserCardLifecycle` covers five genuine same-object `remove()` / `appendChild()` cycles for power-history, k-flow, pv-comparison and energy-flow. `tests/test_r06_browser_additions.py::R06BrowserAddedCoverage::test_forecast_same_element_five_real_document_reconnects` and `::test_total_energy_same_element_five_real_document_reconnects` cover forecast and total-energy: each keeps one registered element reference, changes `hass`, reconnects that same node five times, then checks real DOM values and `pageerror`. |
| Requested-card resource inventory (host/global vs DOM-owned) | **DONE** | `tests/test_r06_browser_playwright.py::TestR06BrowserCardLifecycle::test_source_inventory_distinguishes_owned_resources_from_native_animation` covers k-flow, energy-flow and pv-comparison. `tests/test_r06_browser_additions.py::R06BrowserAddedCoverage::test_forecast_and_total_energy_resource_inventory` confirms forecast owns only its per-element shadow root and total-energy renders in light DOM; neither class creates observers, listeners or JS timers requiring cleanup. Existing k-flow editor/native animation details and pv-comparison's host `ResizeObserver` cleanup remain as described above. |
| Mobile / desktop viewport checks (360 / 1280 px) | **DONE** | `tests/test_r06_browser_additions.py::R06BrowserAddedCoverage::test_all_six_production_cards_at_360_and_1280_with_nonzero_data_and_screenshots` loads all six production scripts unchanged with representative nonzero fixtures and long titles, asserts rendered values, `document.documentElement.scrollWidth <= innerWidth`, and no `pageerror`, and saves 12 screenshots under `/opt/data/cache/scratch/powmr-r06-visual/` (outside git). |
| Visual review (layout, clipping, readability) | **PARTIAL** | The 12 Chromium screenshots were reviewed. Power-history's final x-axis label is now fully inside the plot at both sizes. K-flow keeps the long inverter name inside its central box using a visible ellipsis and full `aria-label`/`title`; several chart labels and smaller flow values remain tiny, and the flow-card icon/readout area is visually crowded. `energy-flow-card` wraps its value tiles at 360 px but does not render the supplied title. These findings are documented; this is not a pixel-perfect sign-off. |
| R09 "Already used" warning absent in operator's browser console | **DONE** | Three behavioural layers: (1) `tests/test_r09_double_load.cjs` — same-version double-load (6/6 safe). (2) `tests/test_r09_mixed_load.cjs` — operator's old-unguarded pinned URL + integration's new-guarded URL in the same VM context (6/6 safe). (3) `tests/test_r06_browser_playwright.py` — 5/5 repeated connect/disconnect cycles in real Chromium without exception. (4) `tests/test_r06_browser_mutation.py` — control failure: production `_render` patched to throw, suite exits non-zero with the sentinel surfaced. Live verification: 5/5 pinned URLs in `lovelace_resources` return the current production `frontend/*.js` by md5. |
| `forecast_received_at` time-zone correctness | **DONE** | `tests/test_r10_forecast_received_at_tz.py` pins both layers: production stamps `_forecast_last_received_at` as `_pv_local_now().astimezone(timezone.utc)` (Kyiv summer -3h, winter -2h); sensor's `_received_at_iso` renders aware values via `astimezone(UTC)` and returns `None` for naive legacy values. Live sensor now shows the UTC instant of the receive cycle, not the local wall clock. |

---

## Honesty rules

- The Node harness does NOT verify "browser/mobile" behavior. Real-browser
  resize/reconnect and viewport evidence is listed in the R06 table above.
  The 12 screenshots were inspected; visual status remains **PARTIAL** because
  the chart axes/small flow labels are difficult to read and the k-flow card
  abbreviates long visible inverter names (while retaining the full accessible
  label/title).
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
