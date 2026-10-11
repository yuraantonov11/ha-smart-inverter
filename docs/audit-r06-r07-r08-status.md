# R06 + R07 + R08 — audit status (2026-10-11 closeout)

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
| `power-history-card` zero vs invalid samples (null / NaN / string) | **DONE** | `tests/test_power_history_card_r06.cjs` is a standalone assertion script (no named test functions); it verifies 0 remains rendered, null/NaN/string samples are omitted, text is escaped and a gap starts a new path. |
| `power-history-card` empty arrays / unknown entity state / missing entity | **DONE** | `tests/test_r06_browser_playwright.py::TestR06BrowserPowerHistoryRender::test_empty_unknown_missing_entity_then_valid_data_recovers` loads the unmodified production script in Chromium, asserts the actual connected DOM for all three states, captures `pageerror`, then verifies the `hass` setter restores three visible data bars/tooltips. |
| `power-history-card` gaps start new path (M-count) | **DONE** | `tests/test_power_history_card_r06_followup.cjs::test_yura_30_point_fixture` (30 raw + gaps 15, 20, 22 → 27 valid, 4 M, 23 L, last tooltip preserved) |
| `power-history-card` x-position by rawIndex (NOT cleaned index) | **DONE** | `tests/test_power_history_card_r06_followup.cjs::test_x_uses_rawIndex_not_cleaned_index` |
| `power-history-card` shared axis = longest series' raw length | **DONE** | `tests/test_power_history_card_r06_followup.cjs::test_shared_axis_uses_longest_series` (rawIndex 14 → x ≈ 259.8) |
| `power-history-card` tooltip for every valid point (incl. isolated after gap) | **DONE** | `tests/test_power_history_card_r06_followup.cjs::test_tooltip_after_gap_includes_isolated_point` |
| `power-history-card` two-series tail gap, last x at right edge | **DONE** | `tests/test_power_history_card_r06_followup.cjs::test_yura_30_point_fixture_with_tail_null` (last x = 490) |
| `power-history-card` HTML escape (title, labels) | **DONE** | `tests/test_power_history_card_r06_followup.cjs::test_html_injection_escaped` |
| `power-history-card` ResizeObserver cleanup | **DONE** | `tests/test_r06_browser_playwright.py::TestR06BrowserResizeObserver::test_disconnect_reconnect_does_not_leak_observers` drives five native Chromium remove/append cycles and verifies active observer cleanup. |
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
| `k-flow-card` production PNG requests and SVG loads | **DONE** | `tests/test_r06_browser_additions.py::R06BrowserAddedCoverage::test_k_flow_icon_png_resources_are_served_and_loaded_by_svg` asserts exact `/local/community/powmr-inverter/{ev-charger,grid,home}-icon.png` paths, HTTP 200, `image/png`, PNG signature and three SVG `load` events (not merely absence of `pageerror`). |
| `smart-solar-energy-flow` title behavior | **DONE** | The card intentionally renders no title under the current contract. The viewport screenshots in `tests/test_r06_browser_additions.py::R06BrowserAddedCoverage::test_all_six_production_cards_at_360_and_1280_with_nonzero_data_and_screenshots` confirm this; no title feature is added. |
| `pv-comparison-card` source/cadence honesty | **DONE** | `tests/test_pv_comparison_card.cjs` (Cadence: unknown (not in config) when absent) |
| Mobile / browser visual / resize / reconnect (aggregate) | **DONE** | The six production cards were exercised in real Chromium at 360/1280 px; the 19-test `tests/test_r06_browser_*.py` suite covers browser rendering, viewport geometry, reconnects, and errors. All 12 current screenshots were reviewed; exact limits and evidence are recorded below. |
| Repeated IDs across separate ShadowRoots | **DONE** | IDs are scoped by each card's own ShadowRoot; identical IDs in different roots do not require global renaming. The browser isolation checks cover the targeted flow instances. This does not claim uniqueness for any future document-global IDs. |

---

## R07 — authentication & device selection

| Item | Status | Evidence |
|------|--------|----------|
| MD5 contract: lowercase 32-char hex → pre-hashed | **DONE** | `tests/test_r07_authentication.py::TestPasswordHashing::test_lowercase_32_hex_is_pre_hashed` |
| MD5 contract: mixed-case 32-char hex → pre-hashed (lowered) | **DONE** | `tests/test_r07_authentication.py::TestPasswordHashing::test_mixed_case_32_hex_is_pre_hashed` |
| MD5 contract: 32-char non-hex → plain (NOT auto-MD5) | **DONE** | `tests/test_r07_authentication.py::TestPasswordHashing::test_plain_32_char_password_is_NOT_pre_hashed` |
| MD5 contract: short / 31 / 33+ char → plain | **DONE** | `TestPasswordHashing::test_short_string_not_pre_hashed`, `::test_long_string_not_pre_hashed`; actual request payload tests include `TestLoginEndpointActualPayload::test_plain_31_char_password_hashed_with_md5` and `::test_33_char_hex_password_hashed_with_md5`. |
| MD5 contract boundary: leading space / sign / underscore | **DONE** | `tests/test_r07_authentication.py::TestPasswordHashing::test_leading_space_not_pre_hashed`, `::test_leading_sign_not_pre_hashed`, `::test_underscore_in_value_not_pre_hashed` |
| MD5 contract: actual login payload sent | **DONE** | `tests/test_r07_authentication.py::TestLoginEndpointActualPayload::test_plain_short_password_hashed_with_md5`, `::test_plain_31_char_password_hashed_with_md5`, `::test_lowercase_32_hex_sent_as_is`, `::test_mixed_case_32_hex_sent_lowercased`, `::test_32_char_non_hex_password_hashed_with_md5`, `::test_33_char_hex_password_hashed_with_md5`, and the three leading-space/sign/underscore payload tests. |
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
| 1/α = 4 in SAMPLES (not "samples per hour" — that was a docstring bug) | **DONE** | `tests/test_r08_demand_model.py::TestEwmaCadence::test_effective_horizon_is_samples_per_hour` |
| Multipliers (0.8/1.0/1.2/1.35) are HEURISTIC, NOT empirical quantiles | **DONE** | `hems/demand_forecast.py::to_demand_forecast` docstring ("heuristic multiplicative spread"). `tests/test_r08_demand_model.py::TestDocumentationStrings::test_to_demand_forecast_docstring_heuristic` |
| NO 1-sigma envelope provenance claim | **DONE** | Removed from `hems/demand_forecast.py::to_demand_forecast` and module docstring. Field names p25/p50/p75/p90 are retained for sensor-contract compatibility but documented as "placeholders for future sample-based calibration". |
| NO `demand_forecast_method` sensor-attribute claim | **DONE** | `tests/test_r08_demand_model.py::TestDocumentationStrings::test_to_demand_forecast_docstring_heuristic` asserts heuristic wording and absence of the unsupported method claim. |
| Same load at different sample rates → same steady state | **DONE** | `tests/test_r08_demand_model.py::TestEwmaCadence::test_same_load_steady_state_independent_of_cadence` |
| Transient response: 1000W → 2000W step, count to 50% | **DONE** | `tests/test_r08_demand_model.py::TestDocumentationStrings::test_transient_response_under_load_change` |
| Min/Max load clamp | **DONE** | `tests/test_r08_demand_model.py::TestEwmaCadence::test_min_load_clamp`, `::test_max_load_clamp` |
| p25/p50/p75/p90 field names retained for sensor contract | **DONE** | `tests/test_r08_demand_model.py::TestMultipliersAreNotQuantiles::test_field_names_match_published_contract` |
| Version reconciliation (root `__version__` vs manifest) | **DONE** | Root `__init__.py::__version__` and `manifest.json:version` are both `1.9.0`; no release tag is required or created for this documentation/test closeout. |
| Doc / translation sync (README, WORKFLOW, manifest, config fields) | **DONE** | `README.md` and `WORKFLOW.md` now describe root layout, version `1.9.0`, manifest runtime dependencies, no asserted HA minimum, and manual release policy; translations remain in sync for the new config-flow step/errors. |

---

---

## R09 — frontend dedup & customElements guard

| Item | Status | Evidence |
|------|--------|----------|
| `add_extra_js_url` does NOT dedupe — operator-pinned URLs in `lovelace_resources` would be re-registered, leading to double HTTP load and `customElements.define` "Already used" warning. | **DONE** | `__init__.py::_install_flow_card` reads `.storage/lovelace_resources` first via `_existing_resource_paths()` and skips the URL when the canonical path (cache-bust stripped) is already present. Operator's version wins. |
| `customElements.define` not guarded in 4 cards: `power-history-card`, `forecast-card`, `total-energy-card`, `energy-flow-card`, `k-flow-card`. | **DONE** | Each of these files now wraps `customElements.define` in `if (!customElements.get('NAME'))`. `pv-comparison-card` already had the guard. |
| Regression test: operator has `power-history-card.js?v=2.0.0-92c25bc9` pinned → integration does NOT re-register. | **DONE** | `tests/test_r09_lovelace_dedup.py::TestR09ResourceExtraction::test_pinned_power_history_not_reregistered` exercises the production `_install_flow_card` path with the query string. |
| Regression test: cache-bust query string is ignored when matching pinned paths. | **DONE** | `tests/test_r09_lovelace_dedup.py::TestR09ResourceExtraction::test_pinned_power_history_not_reregistered` proves the versioned pinned URL prevents a second registration; no separate URL-parser test exists. |
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

## Independent evidence states

The following items remain explicitly separate; completion of local tests or
HTTP byte checks does not upgrade these external observations.

| Item | Status | Concrete evidence / exact remaining work |
|------|--------|------------------------------------------|
| First genuine completed v2 forecast/actual pair | **NOT YET VERIFIED** | No genuine completed v2 pair is available in the checked evidence. Do not create a synthetic/fake pair. Remaining: wait for real v2 forecast plus completed measured actual data, then verify the stored pair and model tags. |
| Inverter hardware PV limit (`pv_max_w`) | **UNKNOWN** | Verified inverter API surface exposes no max-watts field. Remaining: obtain a value from a documented inverter-side endpoint/specification; do not infer it from unrelated cloud/model endpoints or send hardware commands. |
| Live browser cache / browser-side pinned content | **NOT VERIFIED** | Prior resource HTTP-byte/readback evidence proves served bytes only, not what an operator browser cached or loaded. Remaining: inspect browser-side pinned resource/cache after a separately approved deploy; no dashboard/resource edits in this closeout. |
| UPS | **NOT VERIFIED** | No confirmed UPS presence, telemetry, or operation evidence is recorded. Remaining: provide independently verifiable UPS evidence; do not assume one exists. |
| Independent receiver / disaster-recovery copy | **NOT VERIFIED** | Same-host/NAS storage is not an independent receiver/DR copy. Remaining: verify a separate off-host receiver and perform a readback/restore check. |
| T27 actual Windows worktree execution | **NOT VERIFIED / PARTIAL** | `tests/test_t27_runner_contract.py` exercises Windows-specific wrapper/path contracts, and the current run is Linux; neither is Windows worktree execution. Remaining: run the canonical runner on an actual Windows worktree and retain its result. |
| Live Lovelace activation of `energy-flow-card.js` | **PARTIAL** | HA `.storage/lovelace_resources` had no entry for this card; installed file and direct HTTP response were checked, but the card was not active as a Lovelace resource. Remaining: separate operator-approved resource activation and readback; no dashboard/resource edits here. |
| R09 "Already used" warning in operator browser console | **NOT VERIFIED** | Node double-load and isolated Chromium checks do not inspect the live operator DevTools console. Remaining: inspect that console after an approved live rollout. |

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
| `power-history-card` empty arrays / `unknown` / missing entity and valid-data recovery | **DONE** | `tests/test_r06_browser_playwright.py::TestR06BrowserPowerHistoryRender::test_empty_unknown_missing_entity_then_valid_data_recovers` asserts connected-DOM placeholders, no chart, no `pageerror`, then visible three-bar/tooltips recovery through production `hass`; `TestR06ControlFailure::test_power_history_render_failure_is_detected` independently mutates this renderer and confirms both error and DOM assertions fail. |
| `power-history-card` `disconnectedCallback` cleanup (ResizeObserver) | **DONE** | `tests/test_r06_browser_playwright.py::TestR06BrowserResizeObserver::test_disconnect_reconnect_does_not_leak_observers` is the authoritative real-browser cleanup evidence; the legacy Node script also asserts the observer reference is cleared after calling the callback directly. |
| `power-history-card` reconnect after disconnect | **DONE** | `tests/test_r06_browser_playwright.py::TestR06BrowserResizeObserver::test_disconnect_reconnect_does_not_leak_observers` runs 5 cycles in real Chromium. After each cycle the active observer count returns to 0 and the cumulative total grows by 1 (no duplication). |
| Same-element reconnect for all six audited cards | **DONE** | `tests/test_r06_browser_playwright.py::TestR06BrowserResizeObserver::test_disconnect_reconnect_does_not_leak_observers` covers power-history; `tests/test_r06_browser_playwright.py::TestR06BrowserCardLifecycle` covers k-flow, pv-comparison and energy-flow. `tests/test_r06_browser_additions.py::R06BrowserAddedCoverage::test_forecast_same_element_five_real_document_reconnects` and `::test_total_energy_same_element_five_real_document_reconnects` cover forecast and total-energy: each keeps one registered element reference, changes `hass`, reconnects that same node five times, then checks real DOM values and `pageerror`. |
| Requested-card resource inventory (host/global vs DOM-owned) | **DONE** | `tests/test_r06_browser_playwright.py::TestR06BrowserCardLifecycle::test_source_inventory_distinguishes_owned_resources_from_native_animation` covers k-flow, energy-flow and pv-comparison. `tests/test_r06_browser_additions.py::R06BrowserAddedCoverage::test_forecast_and_total_energy_resource_inventory` confirms forecast owns only its per-element shadow root and total-energy renders in light DOM; neither class creates observers, listeners or JS timers requiring cleanup. Existing k-flow editor/native animation details and pv-comparison's host `ResizeObserver` cleanup remain as described above. |
| Mobile / desktop viewport checks (360 / 1280 px) | **DONE** | `tests/test_r06_browser_additions.py::R06BrowserAddedCoverage::test_all_six_production_cards_at_360_and_1280_with_nonzero_data_and_screenshots` loads all six production scripts with representative nonzero fixtures and long titles, checks values/data/ticks, text `getBBox()` against SVG viewBox, screen-font size (≥10 CSS px for chart text), text overlaps, legend bounds, page overflow and `pageerror`; it saves 12 PNGs under `/opt/data/cache/scratch/powmr-r06-visual/` and `/opt/data/powmr-ai-work/r06-final-visual-2026-10-10.zip` (outside git). `test_k_flow_icon_png_resources_are_served_and_loaded_by_svg` separately proves all three real PNG responses and SVG load events. |
| Live Lovelace activation of deployed `energy-flow-card.js` | **PARTIAL** | On HA, `/config/.storage/lovelace_resources` has no `energy-flow-card.js` entry. Its installed file and direct HTTP URL are verified, but it is not an active Lovelace resource; no dashboard/resource configuration was changed. Existing k-flow and power-history resource URLs return the deployed file hashes when requested, but cached browser content was not verified. |
| Visual review (layout, clipping, readability) | **DONE** | All 12 fresh Chromium screenshots were inspected at 360/1280 px and archived at `/opt/data/powmr-ai-work/r06-final-visual-2026-10-10.zip`. Forecast `0:00` is fully visible. At 360px, power-history ticks are `06,08,10,12,14,17`; the full interval and all 12 underlying bars/tooltips remain intact, while 1280px retains hourly labels. At 1280px `kW` is visible between 11:00/12:00; it looks close to 12:00 but text boxes do not overlap. PV-comparison shows the expected full-day span, ticks and W unit. SVG bounds, ≥10 CSS px chart text and non-overlap assertions pass. K-flow renders the real grid/home/EV production PNGs plus its battery illustration and nonzero EV data; no icon/readout clipping or obscuring overlap is visible. Its long inverter name is intentionally ellipsized with the full `aria-label`; some secondary flow labels remain small. Energy-flow has no heading by contract. This is scoped screenshot acceptance, not a claim of pixel-perfect design or live-dashboard activation. |
| R09 "Already used" warning absent in operator's browser console | **NOT VERIFIED** | `tests/test_r09_double_load.cjs`, `tests/test_r09_mixed_load.cjs`, and real-Chromium reconnect tests verify safe repeated script/lifecycle behavior. They do not inspect the operator's live DevTools console; the prior `lovelace_resources` URL/hash checks establish served bytes, not console silence. |
| `forecast_received_at` time-zone correctness | **DONE** | `tests/test_r10_forecast_received_at_tz.py` pins both layers: production stamps `_forecast_last_received_at` as `_pv_local_now().astimezone(timezone.utc)` (Kyiv summer -3h, winter -2h); sensor's `_received_at_iso` renders aware values via `astimezone(UTC)` and returns `None` for naive legacy values. Live sensor now shows the UTC instant of the receive cycle, not the local wall clock. |

---

## Honesty rules

- The Node harness does NOT verify "browser/mobile" behavior. Real-browser
  lifecycle, escaping, isolation, PNG-load and viewport evidence is listed in the
  R06 table above. The requested isolated 360/1280 screenshot criteria are
  **DONE**; the long k-flow name remains intentionally ellipsized, some secondary
  labels are small, and energy-flow has no title by contract. This is not live
  Lovelace activation or pixel-perfect sign-off.
- The new browser harness loads production scripts in a local fixture, not an
  authenticated live Lovelace dashboard. In particular, `energy-flow-card.js`
  is installed and HTTP-served but is absent from live `lovelace_resources`; the
  direct browser tests prove its rendering contract, not live dashboard activation.
- `pv-comparison-card` has a dedicated suite
  (`tests/test_pv_comparison_card.cjs`) with real data fixtures.
  The siblings share the generic
  `tests/test_cards_r06_siblings.cjs`; browser-only evidence for forecast,
  total-energy and flow cards is named individually in the R06 table above.
- The "multi-device flow" is **DONE** for the production
  `async_step_user → async_step_select_device` path, evidenced
  by the 10 tests in
  `tests/test_r07_config_flow_production.py`. It is NOT
  verified by a live HA instance end-to-end (HA's
  `_abort_if_unique_id_configured` is stubbed; the production
  path goes through it but the test only asserts the wiring).

## Remaining items from the original audit

Resolved R06/R07/R08 closeout criteria are recorded above and in the final
T01–T28/R01–R08 matrix below. Distinct external and platform evidence gaps are
listed under **Independent evidence states**; none is silently promoted by a
local test pass.

No HA deploy/restart, live settings/dashboard/resource changes, inverter
commands, hardware changes, or release tag were made. Shadow stays enabled and
Assist remains off.

## Final audit matrix — T01–T28 and R01–R08

Statuses describe the scoped criterion only. All tests cited here are local;
they do not imply live hardware/API/browser state unless a live observation is
explicitly named. Historical commit references are abbreviated real Git SHAs.

| ID | Criterion | Status | Concrete file / test / commit evidence | Live evidence and exact remaining work |
|----|-----------|--------|----------------------------------------|----------------------------------------|
| T01 | SOC `unknown` blocks control and numeric zero remains zero | **DONE** | `tests/test_t01_soc_unknown_real_path.py`, `tests/test_t01_soc_unknown_blocks_commands.py`, `tests/test_t01_soc_zero.py`; `69691ab`, `64c4bb6`, `26f284e`. | No live inverter check; none remaining for local contract. |
| T02 | HEMS user-off / selected mode persists across reload | **DONE** | `tests/test_t02_hems_persistence.py`; `69691ab`. | No live settings changed; none remaining for tested persistence contract. |
| T03 | NaN / ±Infinity inputs are rejected or sanitized at API boundaries | **DONE** | `tests/test_t03_nan_finite.py`; `69691ab`. | No live API command; none remaining for local validation contract. |
| T04 | Passwords/tokens are excluded from logs | **DONE** | `tests/test_t04_log_redaction.py`; `69691ab`. | Synthetic test values only; no real credentials used. |
| T05 | Energy counters use kWh consistently, not Wh | **DONE** | `tests/test_t05_energy_kwh.py`; `69691ab`. | No live energy read; none remaining for unit contract. |
| T06 | Elapsed energy accounting survives restart without wall-clock errors | **DONE** | `tests/test_t06_elapsed_restart.py`; `69691ab`. | No live runtime data changed; none remaining for tested restart contract. |
| T07 | Multi-hour source/load/battery/grid energy balance | **DONE** | `tests/test_t07_t08_energy_balance_and_reserve.py::test_t07_simulate_24h_multihour_discharge_balance`, `::test_t07_pv_surplus_charges_battery_usb_snu`; `93ead68`, `00c7263`. | Model simulation only; no hardware behavior claimed. |
| T08 | Configured reserve SOC is validated, wired, and respected | **DONE** | `tests/test_t07_t08_energy_balance_and_reserve.py::test_t08_production_wiring_reserve_soc_through_build_planner_inputs`, `::test_t08_simulate_24h_respects_custom_reserve_soc`; `93ead68`, `00c7263`. | No live settings changed; none remaining for tested planner contract. |
| T09 | Real weather inputs, storm thresholds, timezone/DST behavior | **DONE** | `tests/test_t09_storm_risk_forecast.py::test_t09_real_weather_reaches_evaluator`, `::test_t09_six_hours_across_midnight`, `::test_t09_dst_fold_keeps_future_forecast_hour`; `2d1a0d6`, `04f24e6`. | Fake HTTP response fixtures only; live forecast service not exercised here. |
| T10 | Weather and grid-outage storm causes remain independent | **DONE** | `tests/test_t10_auto_storm_causes.py::test_t10_outage_cause_survives_weather_clearing`, `::test_t10_weather_cause_survives_outage_clearing`, `::test_t10_storm_ends_only_when_both_causes_clear`; `2d1a0d6`, `8cc4641`. | No live control-mode change; none remaining for local cause contract. |
| T11 | Service calls resolve the intended config entry | **DONE** | `tests/test_t11_service_entry_resolver.py`; `c056341`. | Local fake HA registry; live multi-entry service call not claimed. |
| T12 | Force-grid-charge hold honors safety guards and timer contract | **DONE** | `tests/test_t12_force_grid_charge_hold.py`; `832447c`, `93ead68`. | No inverter command sent; none remaining for tested guard contract. |
| T13 | Cloud command entry points validate acknowledgements | **DONE** | `tests/test_t13_command_ack.py`; `c056341`. | Fake responses only; no live command sent. |
| T14 | History cache preserves last-known-good data on partial failures and real update path | **DONE** | `tests/test_t14_history_lkg_real_path.py`, `tests/test_t14_history_partial_failure.py`; `26f284e`, `fb5c196`, `56fc6e0`. | Fake endpoint failures only; no live history request in this closeout. |
| T15 | Total-energy card is tracked, copied, and registered safely | **DONE** | `tests/test_t15_total_energy_card.py::T15TotalEnergyCardTests::test_15_01_total_energy_js_is_tracked_and_parseable`, `::test_15_02_copy_step_succeeds_on_fresh_www`; `b40aee0`. | Package-path test only; no HA deploy performed. |
| T16 | Runtime options apply, persist, and use a consistent reload classifier | **DONE** | `tests/test_t16_behavioral_options.py::T16BehaviouralOptionsTests::test_16_live_01_listener_executes`, `tests/test_t16_options_contract.py::T16OptionsContractTests::test_16_09_options_flow_uses_classifier`; `2a755c9`, `5ad68c0`, `a8ac5b0`. | Local fake config entries; no live options changed. |
| T17 | Setup/unload/reload cleans entry-owned API, coordinator, and forecast resources | **DONE** | `tests/test_t17_unload_reload_cleanup.py::T17CleanupTests::test_17_07b_unload_order_is_coordinator_first`, `T17ForecastShutdownTests::test_17_12_close_order_cancels_before_closing_session`; `4934db0`, `5ad68c0`. | No HA restart/reload performed. |
| T18 | Debug logging is bounded, thread-safe, per-entry, and drains on unload | **DONE** | `tests/test_t18_debug_logging_threadsafe.py::T18DebugLoggingTests::test_t18_02_event_loop_not_blocked`, `::test_t18_10_per_entry_isolation_runtime`, `tests/test_t18_async_setup_no_unbound_local.py`; `4d3c422`, `c96147b`, `dad977c`. | Local temporary paths only; no HA log file changed. |
| T19 | Dated history, weekday selection, and gap-filled rows preserve coverage semantics | **DONE** | `tests/test_t19_weekday_filter.py::test_missing_dates_do_not_shift_weekday_index`, `tests/test_t19_gap_filled_coverage.py::test_predictor_excludes_gap_filled_from_same_weekday`, `tests/test_t19_history_propagation.py::test_telemetry_engine_propagates_dated_history`; `d18cd85`, `005d96e`, `4965f20`. | Offline history fixtures; no live recorder data processed. |
| T20 | Daily-energy freshness, midnight reset, and selected-device refresh/cache | **DONE** | `tests/test_t20_daily_energy_sensor.py::test_midnight_reset_zeros_value_for_today`, `::test_real_sensor_path_uses_utc_normalization`, `tests/test_t20_energy_stats_refresh.py::test_picks_configured_device_not_devices_zero`, `tests/test_t19_t20_round3.py::test_real_ttl_preserves_cache_on_failure`; `9f2e04d`, `7579b73`, `607f612`, `4b6ca3e`. | Synthetic HTTP/coordinator fixtures; live telemetry freshness remains outside this test. |
| T21 | Savings are labeled as gross estimates with formula/limitations disclosed | **DONE** | `tests/test_t21_savings_transparency.py::test_daily_savings_docstring_describes_gross_estimate`, `::test_savings_sensor_exposes_formula_attribute`, `::test_dashboard_does_not_mislabel_savings`; `67cfe9e`, `9048ec3`. | No real bill/tariff validation claimed. |
| T22 | Predictive readiness / zero-pair state is exposed honestly | **DONE** | `tests/test_t22_t23_dashboard.py::test_r71_predictive_control_publishes_readiness_to_state_dict`, `::test_r72_zero_pairs_title_appears_in_view`; `b9a7a42`, `7d19c5f`. | Dashboard generation tests only; live dashboard was not edited. |
| T23 | Dashboard writes/migrations are per-entry, atomic, and rollback-safe | **DONE** | `tests/test_t22_t23_dashboard.py::test_r73_sidecar_dashboard_is_registered_in_metadata`, `::test_r101_rollback_preserves_existing_sidecar_with_main_present`, `::test_r106_two_entries_isolated_dashboards`; `b9a7a42`, `87279de`, `5c000d1`, `7d19c5f`. | No live dashboard/resource mutation performed. |
| T24 | Automations are advisory, use live select state, and do not bypass Python HEMS guards | **DONE** | `tests/test_t24_automations.py::TestT24AdvisoryAutomations::test_select_state_read_uses_state_not_option_attribute`, `tests/test_t24_command_paths.py::TestT24GridOutageIsNotificationOnly::test_no_select_option_in_grid_outage`; `4f3acee`, `207690b`. | YAML/HA stubs only; automations were not installed or run. |
| T25 | Per-entry persistence, round trips, rollback, flush-on-unload, single keepalive owner | **DONE** | `tests/test_t25_round_trip_persistence.py::TestPerEntryIsolation::test_demand_forecast_two_entries_isolated`, `tests/test_t25_persistence.py::TestScheduleServiceRollback::test_add_rule_rollback_when_persist_fails`, `tests/test_t25_keepalive_real_path.py::TestKeepaliveEligibility::test_eligibility_exactly_at_threshold`; `0507f9e`, `260da29`, `8661bf3`, `dad977c`. | Temporary test stores only; no production state touched. |
| T26 | Battery SoH input/date validation, aging, reserve floor, serialization | **DONE** | `tests/test_t26_battery_soh.py::TestCoerceSoc::test_nan_returns_none`, `TestEstimatedSoHPercentControlledNow::test_one_year_aging_reduces_soh_by_approximately_3_percent`, `TestRecommendedReserveNeverBelowBase::test_base_reserve_already_above_cap_returns_base`; `854ee9b`, `8882168`, `dad977c`. | No live battery measurements changed. |
| T27 | Unified Python/JS runner and Windows isolation contract | **PARTIAL — Windows NOT VERIFIED** | `tests/test_t27_runner_contract.py::T27RunnerContractTests::test_t27_12_wrapper_handles_windows_paths`, `::test_t27_11_runner_runs_full_suite_with_isolation`; `8a1aac9`, `c6d2014`, `312c6f1`, `44c460e`. | Current full run is Linux. Remaining: execute the canonical runner in an actual Windows worktree; local Linux success and wrapper simulation are not Windows evidence. |
| T28 | Runtime data/log/backup files are narrowly ignored and failures are diagnosed | **DONE** | `tests/test_t28_runtime_files.py::TestT28GitignoreRuntimePatterns::test_runtime_data_files_ignored`, `TestT28WriteErrorDiagnostics::test_persist_helpers_log_on_failure`, `TestT28SpecificRuntimePaths::test_real_forecast_pairs_per_entry_subdir_ignored`; `81fc002`, `e6e5c6c`. | Local temp/git-ignore checks; no runtime files changed. |
| R01 | Interval-start v2 radiation contract, DST boundaries, versioned model identity/migration | **DONE** | `tests/test_r01_production_contract.py`, `tests/test_r01_dst_intervals.py`, `tests/test_r01_reproduction_via_http.py`, `tests/test_r01_probe_validator.py`; `de808e7`, `c1cf880`, `d9d26ae`, `1aacb26`. | API/test fixtures; no live data run. |
| R02 | Cloud/realtime payload evidence and freshness semantics | **PARTIAL — measured freshness unavailable** | `tests/test_r02_realtime_telemetry.py`, `tests/test_r02_cloud_payloads.py`; investigation `d64f9d4`. No API `measured_at` timestamp or sequence identifier is available in the inspected payloads. | Do not claim measured freshness. Remaining: obtain and validate an authoritative server timestamp/sequence field if the API exposes one. |
| R03 | Real forecast/actual pair storage and immutable snapshot contract | **PARTIAL — first real v2 pair NOT YET VERIFIED** | `tests/test_r03_forecast_pairs.py`; `a49ea5f`, `2b4679a`. Tests establish local pair/snapshot invariants, not the existence of a real completed v2 pair. | Remaining: observe a genuine completed v2 forecast/actual pair; no synthetic/fake pair was created. |
| R04 | Battery and tariff fixes through production wiring; inverter PV ceiling | **PARTIAL — `pv_max_w` UNKNOWN** | `tests/test_r04_r05_acceptance.py`, `tests/test_r04_integration_load.py`; `49b43c1`, `c505a3d`, `96b2337`, `4e02491` cover validation, tariff path, capacity/recovery and production wiring. | Battery/tariff fixes are tested. Remaining: authoritative inverter-side `pv_max_w` value; API currently exposes none. |
| R05 | PV daylight/window gating and forecast/tariff acceptance fixes | **DONE** | `tests/test_r04_r05_acceptance.py` and `tests/test_r04_integration_load.py`; `49b43c1`, `96b2337`, `4e02491`. | Automated fixtures only; no live hardware behavior claimed. |
| R06 | Production-card real-browser rendering, lifecycle, empty/unknown/missing states, recovery | **DONE** | `tests/test_r06_browser_playwright.py::TestR06BrowserPowerHistoryRender::test_empty_unknown_missing_entity_then_valid_data_recovers`, `TestR06ControlFailure::test_power_history_render_failure_is_detected`, and existing ResizeObserver/reconnect tests; `2121416`, `13a1760`, `4e53f8d`. | Isolated Chromium only. Remaining: live browser cache/pinned content is separately **NOT VERIFIED**; no deploy/dashboard changes here. |
| R07 | Auth payload MD5 boundary, no secret logs, stable multi-device config flow | **DONE** | `tests/test_r07_authentication.py::TestLoginEndpointActualPayload::test_plain_31_char_password_hashed_with_md5`, `::test_32_char_non_hex_password_hashed_with_md5`, `::test_33_char_hex_password_hashed_with_md5`, plus 32-hex preserved/lowercase tests; `tests/test_r07_config_flow_production.py::TestConfigFlowProduction::test_multi_device_picker_executes_production_flow`; `b6dd024`, `3a7355f`, `280ebd4`, `79c426c`, `ddd61e2`. | Synthetic credentials and fake HTTP only; no real credential, live API, or full response payload used. |
| R08 | Demand-model wording/cadence and root-layout/version/runtime/release docs | **DONE** | `tests/test_r08_demand_model.py::TestEwmaCadence::test_same_load_steady_state_independent_of_cadence`, `TestDocumentationStrings::test_to_demand_forecast_docstring_heuristic`; `README.md`, `WORKFLOW.md`, `manifest.json`, root `__init__.py`; `0ec10cb`, `280ebd4`. Both version values are `1.9.0`; no tag created. | No live evidence required. Remaining: none for docs/version alignment; release tag remains maintainer-controlled and is not part of this closeout. |

This matrix does not close the separate items in **Independent evidence states**.
