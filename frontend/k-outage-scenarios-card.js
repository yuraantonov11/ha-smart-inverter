// k-outage-scenarios-card.js
//
// Manual outage scenario card. Lets the operator add, confirm,
// edit, and cancel planned-outage events through the dashboard
// instead of calling the service from developer tools.
//
// Service contract (all already registered in
// services/outage.py):
//   * powmr_inverter.add_outage_event
//   * powmr_inverter.update_outage_event
//   * powmr_inverter.confirm_outage_event
//   * powmr_inverter.cancel_outage_event
//   * powmr_inverter.list_outage_events
//   * powmr_inverter.compute_outage_plan (per-entry suffixed)
//
// Configuration:
//   type: custom:k-outage-scenarios-card
//   entry_id: <string>  (the Inverter config-entry id; the
//                          card uses it to address the suffixed
//                          compute_outage_plan service)
//
// Version: 1.1.0
//
// 1.1.0 changes (manual-scenario bug fix round):
//   * `_refresh()` and `_computePlan()` handle both
//     ``res.response`` (the regular HA
//     ``return_response=true`` path) and the bare ``res``
//     fallback so a misbehaving / older WebSocket binding
//     never silently drops the response.
//   * `_addEvent()` and `_saveEdit()` now pass
//     ``entry_id`` alongside the payload so the
//     `add_outage_event` service handler always has the
//     target entry context (the
//     `update_outage_event` already passed it).
//   * All datetime input / output uses the HA
//     timezone (``hass.config.time_zone``) instead of
//     the browser timezone -- the home server is the
//     clock authority. The new
//     `_localInputToHaIso(localValue, haTz)` helper
//     converts a wall-clock input string
//     (``YYYY-MM-DDTHH:MM``) to offset-aware ISO 8601
//     using the HA TZ offset, not the browser offset,
//     and the new `_formatInHaTz(iso, haTz)` helper
//     formats display strings in the same zone.
//   * `_formatPlan()` reads `plan.blocked`,
//     `plan.reason_if_blocked`, and
//     `plan.reason_if_incomplete` from the planner's
//     top-level field set (not the legacy
//     ``plan.reason`` only used by the early-return
//     branches of the runtime handler). Field-by-field
//     values still come from `plan.plan.*` (the nested
//     dict).
//   * `_saveEdit()` sends
//     ``notes: this._editForm.notes ?? null`` so an
//     undefined form value is converted to ``null``
//     (don't change) while an explicitly typed empty
//     string is preserved as the "clear notes" marker.

class KOutageScenariosCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._haTz = "UTC"; // cached from hass.config.time_zone; "UTC" is a safe default
    this._events = [];
    this._error = null;
    this._busy = false;
    // Form state. Times are stored as ISO local strings
    // (no offset) -- the service requires offset-aware
    // ISO 8601, so the card joins the local wall-clock
    // with the HA timezone offset (NOT the browser
    // offset) before calling the service.
    this._form = {
      start: "",
      end: "",
      notes: "",
    };
    this._editing = null; // event id being edited, or null
    this._editForm = {
      start: "",
      end: "",
      notes: "",
    };
    this._planCache = {}; // event_id -> { plan, ts }
    this._render();
  }

  static getStubConfig() {
    return { type: "custom:k-outage-scenarios-card", entry_id: "" };
  }

  setConfig(config) {
    if (!config || config.type !== "custom:k-outage-scenarios-card") {
      throw new Error("Invalid configuration");
    }
    this._config = { entry_id: config.entry_id || "" };
  }

  set hass(hass) {
    this._hass = hass;
    // Cache the HA timezone exactly once per ``set hass``
    // call so we never read it during a render call and
    // we always have a stable value to feed into the
    // datetime helpers. The frontend object is the
    // canonical source of truth for the home clock.
    if (hass && hass.config && typeof hass.config.time_zone === "string" && hass.config.time_zone) {
      this._haTz = hass.config.time_zone;
    }
    if (hass) this._refresh();
  }

  getCardSize() {
    return 4;
  }

  connectedCallback() {
    if (this._hass) this._refresh();
  }

  // ----- Data flow ---------------------------------------------------

  async _refresh() {
    if (!this._hass) return;
    try {
      // The list service returns ``{ events: [...] }``
      // when called with ``return_response=True``;
      // HA wraps it under ``res.response``.
      const res = await this._hass.callService(
        "powmr_inverter",
        "list_outage_events",
        { entry_id: this._config.entry_id || null },
        null,
        true
      );
      // Defensive dual-path: prefer ``res.response`` (the
      // standard ``return_response=true`` wrap) but also
      // accept the bare ``res`` for callers / older WS
      // bindings that surface the payload directly.
      const response =
        (res && res.response) || res || {};
      this._events = Array.isArray(response.events)
        ? response.events
        : [];
      this._error = null;
    } catch (e) {
      this._error = String(e && e.message ? e.message : e);
      this._events = [];
    }
    this._render();
  }

  // ----- Time helpers ------------------------------------------------

  // Build a probe ``Date`` whose UTC fields store the
  // typed-in wall-clock numbers, then format that probe
  // in the HA timezone to read back the wall-clock those
  // numbers actually represent.  The difference between
  // the input and the output tells us the HA offset at
  // that instant; with that offset we can produce a
  // correct offset-aware ISO string that, when fed back
  // through ``new Date(iso)`` in any browser, renders
  // back to the user's wall clock in the HA zone.
  //
  // ``localValue`` is ``YYYY-MM-DDTHH:MM`` from the
  // ``datetime-local`` input -- it has NO timezone
  // semantics on its own. We interpret the numbers
  // as a wall-clock time in ``haTz``.
  _localInputToHaIso(localValue, haTz) {
    if (!localValue) return "";
    const m = String(localValue).match(
      /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/
    );
    if (!m) return localValue;
    const [, y, mo, d, hh, mm, ssRaw] = m;
    const ss = ssRaw || "00";
    // Build a probe UTC timestamp using the typed
    // numbers. ``new Date("2026-10-20T09:00:00.000Z")``
    // gives a known UTC instant we can probe against.
    const probeIso =
      `${y}-${mo}-${d}T${hh}:${mm}:${ss}.000Z`;
    const probe = new Date(probeIso);
    if (isNaN(probe.getTime())) return localValue;
    // Format the probe in HA TZ to read back its
    // wall-clock. The number-padded output is locale-
    // independent (``formatToParts``), so an ``en-CA``
    // locale with ``hour12: false`` gives us ``YYYY-MM-
    // DD HH:MM:SS`` of the probe's wall-clock in HA TZ.
    const fmt = new Intl.DateTimeFormat("en-CA", {
      timeZone: haTz || this._haTz || "UTC",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    const parts = fmt.formatToParts(probe);
    const get = (t) =>
      (parts.find((p) => p.type === t) || {}).value || "";
    const yh = get("year");
    const moh = get("month");
    const dh = get("day");
    const hh24 = get("hour");
    const mmh = get("minute");
    const ssh = get("second");
    // ``typedWallClockAsUtc`` is the user's typed
    // wall-clock expressed AS IF those numbers were UTC
    // (Date.UTC(year, month-1, day, hour, minute,
    // second)).  ``probeInHaWallClockAsUtc`` is the same
    // thing but using the formatted-back wall-clock.
    //
    // The probe's UTC instant can be expressed as:
    //   probeMs == typedWallClockAsUtc
    //     (because the probe was built from typed values
    //      directly as UTC).
    //
    // The probe's UTC instant, reformatted in HA TZ,
    // should equal:
    //   probeMs == probeInHaWallClockAsUtc - HA_offset
    //     (because Date.UTC interprets wall-clock as UTC,
    //      and the HA TZ wall-clock of the probe is
    //      ``HA_offset`` ahead/behind that).
    //
    // Concretely, ``HA_offset = probeInHaWallClockAsUtc
    // - probeMs`` tells us how many milliseconds the HA
    // wall-clock lags (``-offset`` if HA is east of UTC,
    // ``+offset`` if HA is west). We want the instant
    // whose HA wall-clock equals the typed values:
    //
    //   targetMs = typedWallClockAsUtc - HA_offset
    //
    // Substituting:
    //
    //   targetMs = typedWallClockAsUtc
    //             - (probeInHaWallClockAsUtc - probeMs)
    //
    // Which simplifies to the formula below.
    const typedWallClockAsUtc = Date.UTC(
      Number(y),
      Number(mo) - 1,
      Number(d),
      Number(hh),
      Number(mm),
      Number(ss),
    );
    const probeInHaWallClockAsUtc = Date.UTC(
      Number(yh),
      Number(moh) - 1,
      Number(dh),
      Number(hh24 === "24" ? 0 : hh24),
      Number(mmh),
      Number(ssh),
    );
    const targetMs =
      typedWallClockAsUtc +
      probe.getTime() -
      probeInHaWallClockAsUtc;
    const corrected = new Date(targetMs);
    if (isNaN(corrected.getTime())) return localValue;
    return corrected.toISOString();
  }

  // Format the given ISO string as a wall-clock string
  // in the HA timezone (the user's set Home Assistant
  // time zone) rather than the browser's local zone.
  // Used for both the event-time range in each row and
  // the planner's ``prep_start_iso`` field.
  _formatInHaTz(iso, haTz, opts) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    const tz = haTz || this._haTz || "UTC";
    const fmt = new Intl.DateTimeFormat("uk-UA", {
      timeZone: tz,
      ...(opts || {}),
    });
    try {
      return fmt.format(d);
    } catch (e) {
      // Fall back to the browser's en-US format if the
      // requested TZ is not recognised by ``Intl``
      // (shouldn't happen in modern Chromium / Firefox
      // / Safari but defensive against runtime fall-
      // through).
      return d.toLocaleString();
    }
  }

  // Convert an ISO string to ``YYYY-MM-DDTHH:MM`` using
  // the HA timezone so the form's ``datetime-local``
  // defaults come back in the same wall clock the user
  // originally typed.
  _isoToLocalInput(iso, haTz) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    const tz = haTz || this._haTz || "UTC";
    const fmt = new Intl.DateTimeFormat("en-CA", {
      timeZone: tz,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    const parts = fmt.formatToParts(d);
    const get = (t) =>
      (parts.find((p) => p.type === t) || {}).value || "";
    const y = get("year");
    const mo = get("month");
    const da = get("day");
    const hh = get("hour");
    const mi = get("minute");
    // ``hour: "2-digit"`` can produce ``"24"`` for
    // midnight in some locales; roll that to ``"00"``.
    const hhNorm = hh === "24" ? "00" : hh;
    return `${y}-${mo}-${da}T${hhNorm}:${mi}`;
  }

  // Same as ``_localInputToHaIso`` but uses an explicit
  // ``haTz`` (so callers don't depend on the card
  // instance state, useful for ``setConfig`` defaults
  // before ``set hass`` runs).
  _defaultStartLocal(haTz) {
    return this._nowPlusHoursLocal(2, haTz);
  }

  _defaultEndLocal(haTz) {
    return this._nowPlusHoursLocal(4, haTz);
  }

  _nowPlusHoursLocal(hours, haTz) {
    const tz = haTz || this._haTz || "UTC";
    // Build "now" in the HA TZ (so the +2h / +4h
    // defaults sit cleanly inside the operator's day).
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: tz,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).formatToParts(new Date());
    const get = (t) =>
      (parts.find((p) => p.type === t) || {}).value || "";
    const y = Number(get("year"));
    const mo = Number(get("month"));
    const da = Number(get("day"));
    let hh = Number(get("hour"));
    const mi = Number(get("minute"));
    hh = (hh + hours) % 24;
    const pad = (n) => String(n).padStart(2, "0");
    return (
      `${y}-${pad(mo)}-${pad(da)}T${pad(hh)}:${pad(mi)}`
    );
  }

  _validateInterval(startIso, endIso) {
    if (!startIso || !endIso) {
      return "Вкажіть дату й час початку та завершення.";
    }
    const s = new Date(startIso);
    const e = new Date(endIso);
    if (isNaN(s.getTime()) || isNaN(e.getTime())) {
      return "Некоректний формат дати.";
    }
    if (e <= s) {
      return "Час завершення має бути пізніше за час початку.";
    }
    const diffMs = e - s;
    const minutes = Math.round(diffMs / 60000);
    if (minutes < 1) {
      return "Інтервал занадто короткий (< 1 хв).";
    }
    if (minutes > 7 * 24 * 60) {
      return "Інтервал занадто довгий (> 7 днів).";
    }
    return null;
  }

  // ----- Service calls ---------------------------------------------

  async _addEvent() {
    if (this._busy) return;
    // Convert the wall-clock inputs from the form to
    // offset-aware ISO 8601 using the HA timezone (NOT
    // the browser zone). This keeps the stored event
    // aligned with HA's clock regardless of where the
    // operator happens to be sitting when they type.
    const startIso = this._localInputToHaIso(
      this._form.start, this._haTz
    );
    const endIso = this._localInputToHaIso(
      this._form.end, this._haTz
    );
    const err = this._validateInterval(startIso, endIso);
    if (err) {
      this._error = err;
      this._render();
      return;
    }
    this._busy = true;
    this._error = null;
    this._render();
    try {
      // ``add_outage_event`` with structured
      // ``start`` + ``end`` (offset-aware ISO)
      // is unambiguous and the service
      // auto-confirms the event -- the planner
      // only plans ``confirmed`` events, so
      // there is no second ``confirm`` call
      // here. The structured insert is also
      // the same path the HA ``Frontend``
      // call-services UI uses.
      //
      // ``entry_id`` is passed so the service
      // handler can route the event to the
      // correct ``EventStore``. ``notes``
      // follows the contract: ``null`` means
      // "no notes" (stored as ``None``),
      // ``""`` is what the service contract
      // would normalise to ``None`` anyway,
      // so we are consistent.
      await this._hass.callService(
        "powmr_inverter",
        "add_outage_event",
        {
          source: "manual:dashboard",
          start: startIso,
          end: endIso,
          // Empty ``<input>`` value is ``""`` in the
          // DOM and a structured null on the service
          // contract; send ``null`` explicitly so we
          // don't store a literal empty string.
          notes: this._form.notes ? this._form.notes : null,
          entry_id: this._config.entry_id || null,
        },
        null,
        true
      );
      // Reset the form to the next "now + 2h"
      // window so the operator can quickly add a
      // second event.
      this._form = {
        start: this._defaultStartLocal(this._haTz),
        end: this._defaultEndLocal(this._haTz),
        notes: "",
      };
      await this._refresh();
    } catch (e) {
      this._error = String(e && e.message ? e.message : e);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _cancelEvent(eventId) {
    if (this._busy) return;
    this._busy = true;
    this._error = null;
    this._render();
    try {
      await this._hass.callService(
        "powmr_inverter",
        "cancel_outage_event",
        {
          id: eventId,
          reason: "cancelled from dashboard",
          entry_id: this._config.entry_id || null,
        }
      );
      await this._refresh();
    } catch (e) {
      this._error = String(e && e.message ? e.message : e);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _saveEdit(eventId, version) {
    if (this._busy) return;
    const startIso = this._localInputToHaIso(
      this._editForm.start, this._haTz
    );
    const endIso = this._localInputToHaIso(
      this._editForm.end, this._haTz
    );
    const err = this._validateInterval(startIso, endIso);
    if (err) {
      this._error = err;
      this._render();
      return;
    }
    this._busy = true;
    this._error = null;
    this._render();
    try {
      // ``notes`` contract for ``update_outage_event``:
      //   * ``null`` -- do not change.
      //   * ``""`` -- clear the note (explicit empty).
      //   * any other string -- replace.
      // The DOM field stores ``""`` when the user
      // clears the input. We forward ``""`` directly
      // (no coalescing with ``??``) because the user
      // explicitly opted in to clearing the note. The
      // ``??`` only catches a missing field (which the
      // operator can never produce -- the input is
      // always present, even if blank).
      const notesRaw = this._editForm.notes;
      const notesValue = notesRaw == null ? null : notesRaw;
      await this._hass.callService(
        "powmr_inverter",
        "update_outage_event",
        {
          id: eventId,
          version,
          start: startIso,
          end: endIso,
          notes: notesValue,
          entry_id: this._config.entry_id || null,
        }
      );
      this._editing = null;
      await this._refresh();
    } catch (e) {
      this._error = String(e && e.message ? e.message : e);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  _startEdit(ev) {
    this._editing = ev.id;
    // Pre-fill from the existing event; format in HA TZ
    // so the datetime-local defaults round-trip back
    // exactly to the same wall clock the operator
    // originally typed.
    this._editForm = {
      start: this._isoToLocalInput(ev.start_iso, this._haTz),
      end: this._isoToLocalInput(ev.end_iso, this._haTz),
      notes: ev.notes == null ? "" : ev.notes,
    };
    this._render();
  }

  _cancelEdit() {
    this._editing = null;
    this._render();
  }

  async _computePlan(eventId) {
    if (!this._hass || !this._config.entry_id) return null;
    try {
      const res = await this._hass.callService(
        "powmr_inverter",
        `${this._config.entry_id}_compute_outage_plan`,
        { event_id: eventId, force_recompute: true },
        null,
        // The runtime service registration uses
        // ``supports_response=True`` so HA returns a
        // payload; ``return_response=true`` wraps it
        // under ``res.response``. We still accept the
        // bare response for older WS bindings.
        true
      );
      const r = (res && res.response) || res;
      if (!r) return null;
      // Reshape the early-return shape so the rest of
      // the renderer can use a single code path:
      //   { blocked: true, reason_if_blocked: "..." }
      // becomes   { planned: false,
      //              blocked: true,
      //              reason_if_blocked: "...",
      //              plan: {} }
      // (the runtime early-return branch uses
      //  ``reason``, which the full planner uses
      //  ``reason_if_blocked`` -- both end up under
      //  the same key here.)
      if (r.blocked === undefined && r.reason && r.planned === false) {
        return {
          planned: false,
          blocked: true,
          reason_if_blocked: r.reason,
          reason_if_incomplete: null,
          plan: {},
        };
      }
      return r;
    } catch (e) {
      return {
        planned: false,
        blocked: true,
        reason_if_blocked:
          "planner недоступний: " +
          String(e && e.message ? e.message : e),
        reason_if_incomplete: null,
        plan: {},
      };
    }
  }

  // ----- Rendering -------------------------------------------------

  _escape(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  _formatRange(ev) {
    const s = ev.start_iso ? new Date(ev.start_iso) : null;
    const e = ev.end_iso ? new Date(ev.end_iso) : null;
    if (!s || !e || isNaN(s) || isNaN(e)) return "(невідомо)";
    // Format in the HA timezone -- the dashboard is
    // operator-facing and the operator lives in HA's
    // configured zone regardless of browser locale.
    const fmt = (d) =>
      this._formatInHaTz(d.toISOString(), this._haTz, {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    return `${fmt(s)} → ${fmt(e)}`;
  }

  _formatPlan(plan) {
    if (!plan) {
      return `<div class="plan plan--unknown">План не розраховано.</div>`;
    }
    // The runtime handler can return either:
    //   * a PlannerPlan dict (full shape with
    //     ``blocked``, ``reason_if_blocked``,
    //     ``reason_if_incomplete``, ``plan``)
    //   * an early-return dict with
    //     ``{ planned: false, reason: "..." }``
    // The card normalises both forms before this
    // point (see ``_computePlan``).
    if (plan.blocked === true || plan.planned === false) {
      const reasonText =
        plan.reason_if_blocked ||
        plan.reason ||
        "невідомо";
      return (
        `<div class="plan plan--blocked">` +
        `План відсутній: ${this._escape(reasonText)}</div>`
      );
    }
    const inner = plan.plan || {};
    const targetSoc = inner.attainable_target_soc_pct;
    const needed = inner.energy_needed_kwh;
    const available = inner.energy_available_kwh;
    const reserve = inner.energy_reserve_kwh;
    const gridKwh = inner.needs_charging_from_grid_kwh;
    const prep = inner.prep_start_iso;
    const chargeRate =
      inner.grid_charge_hours_per_hour != null
        ? inner.grid_charge_hours_per_hour
        : plan.grid_charge_hours_per_hour;
    const lines = [];
    lines.push(this._planLine(
      "Цільовий SOC на початок",
      targetSoc == null
        ? {
            kind: "unknown",
            text: "невідомо (потрібні дані про батарею)",
          }
        : { kind: "value", text: `${targetSoc.toFixed(0)} %` }
    ));
    lines.push(this._planLine(
      "Коли починати підготовку",
      !prep
        ? {
            kind: "unknown",
            text: "невідомо (немає даних про потужність зарядки)",
          }
        : { kind: "value", text: this._formatPlanTime(prep) }
    ));
    if (needed == null || available == null) {
      lines.push(this._planLine(
        "Доступна енергія vs потреба",
        {
          kind: "unknown",
          text:
            "невідомо (потрібні дані про споживання та PV-прогноз)",
        }
      ));
    } else {
      const margin = available - (needed + (reserve || 0));
      const status =
        margin >= 0
          ? "вистачає"
          : `не вистачає ${Math.abs(margin).toFixed(2)} кВт·год`;
      const detail =
        needed != null
          ? `потрібно ${needed.toFixed(2)} кВт·год, ` +
            `доступно ${available.toFixed(2)} кВт·год`
          : "";
      lines.push(this._planLine(
        "Доступна енергія vs потреба",
        {
          kind: status === "вистачає" ? "ok" : "warn",
          text: `${status}${detail ? " — " + detail : ""}`,
        }
      ));
    }
    if (gridKwh != null && gridKwh > 0) {
      const rateText = chargeRate
        ? ` (${chargeRate.toFixed(2)} год/год)`
        : "";
      lines.push(this._planLine(
        "Зарядка від мережі перед подією",
        {
          kind: "warn",
          text: `потрібно дозарядити ${gridKwh.toFixed(2)} кВт·год${rateText}`,
        }
      ));
    }
    if (plan.reason_if_incomplete) {
      lines.push(
        `<div class="plan__warn">${this._escape(plan.reason_if_incomplete)}</div>`
      );
    }
    return `<div class="plan">${lines.join("")}</div>`;
  }

  _formatPlanTime(iso) {
    return this._formatInHaTz(iso, this._haTz, {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  _planLine(label, value) {
    const cls = `plan__row plan__row--${value.kind}`;
    return (
      `<div class="plan__row">` +
      `<span class="plan__label">${this._escape(label)}</span>` +
      `<span class="${cls}">${this._escape(value.text)}</span>` +
      `</div>`
    );
  }

  _renderEventRow(ev) {
    const isEditing = this._editing === ev.id;
    const status = ev.status || "pending";
    const statusLabel = this._statusLabel(status);
    const plan = this._planCache[ev.id] && this._planCache[ev.id].plan;
    return (
      `<div class="event ${isEditing ? "event--editing" : ""}">` +
      `<div class="event__header">` +
      `<div class="event__time">${this._escape(this._formatRange(ev))}</div>` +
      `<div class="event__status event__status--${this._escape(status)}">` +
      `${this._escape(statusLabel)}` +
      `</div>` +
      `</div>` +
      (ev.notes
        ? `<div class="event__notes">${this._escape(ev.notes)}</div>`
        : "") +
      (isEditing
        ? this._renderEditForm(ev)
        : this._formatPlan(plan)) +
      `<div class="event__actions">` +
      (isEditing
        ? `<button class="btn btn--secondary" data-action="cancel-edit" data-id="${this._escape(ev.id)}">Скасувати</button>`
        : `<button class="btn btn--secondary" data-action="edit" data-id="${this._escape(ev.id)}" data-version="${this._escape(ev.version)}">Редагувати</button>`) +
      (status !== "cancelled"
        ? `<button class="btn btn--danger" data-action="cancel" data-id="${this._escape(ev.id)}">Скасувати подію</button>`
        : "") +
      (isEditing ? "" : `<button class="btn btn--secondary" data-action="plan" data-id="${this._escape(ev.id)}">Оновити план</button>`) +
      `</div>` +
      `</div>`
    );
  }

  _statusLabel(status) {
    switch (status) {
      case "confirmed":
        return "Підтверджено";
      case "pending":
        return "Очікує підтвердження";
      case "cancelled":
        return "Скасовано";
      case "completed":
        return "Завершено";
      default:
        return status;
    }
  }

  _renderEditForm(ev) {
    return (
      `<div class="form form--inline">` +
      `<label>Початок ` +
      `<input type="datetime-local" data-field="start" value="${this._escape(this._editForm.start)}" />` +
      `</label>` +
      `<label>Кінець ` +
      `<input type="datetime-local" data-field="end" value="${this._escape(this._editForm.end)}" />` +
      `</label>` +
      `<label>Примітка ` +
      `<input type="text" data-field="notes" value="${this._escape(this._editForm.notes)}" />` +
      `</label>` +
      `<div class="form__actions">` +
      `<button class="btn btn--primary" data-action="save-edit" data-id="${this._escape(ev.id)}" data-version="${this._escape(ev.version)}">Зберегти</button>` +
      `</div>` +
      `</div>`
    );
  }

  _render() {
    if (!this._form.start) {
      this._form.start = this._defaultStartLocal(this._haTz);
      this._form.end = this._defaultEndLocal(this._haTz);
    }
    const nowIso = new Date().toISOString();
    const upcoming = (this._events || [])
      .filter(
        (ev) =>
          ev.status !== "cancelled" &&
          ev.status !== "completed" &&
          ev.end_iso &&
          new Date(ev.end_iso).toISOString() >= nowIso
      )
      .sort(
        (a, b) =>
          new Date(a.start_iso).getTime() -
          new Date(b.start_iso).getTime()
      );
    const listHtml = upcoming.length
      ? upcoming.map((ev) => this._renderEventRow(ev)).join("")
      : `<div class="empty">Немає запланованих відключень. Додайте перше за допомогою форми нижче.</div>`;
    const errorBlock = this._error
      ? `<div class="error">${this._escape(this._error)}</div>`
      : "";
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          font-family: var(--paper-font-body1_-_font-family, system-ui);
          color: var(--primary-text-color);
        }
        .card {
          padding: 12px 16px 16px;
          background: var(--card-background-color, #1c1c1c);
          border-radius: 8px;
        }
        h2 { margin: 0 0 4px; font-size: 1.15rem; }
        .subtitle { color: var(--secondary-text-color); font-size: 0.85rem; margin-bottom: 12px; }
        .form { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; }
        .form--inline { grid-template-columns: 1fr 1fr 1fr; }
        .form label { display: flex; flex-direction: column; font-size: 0.8rem; color: var(--secondary-text-color); }
        .form input { font-size: 0.9rem; padding: 4px 6px; }
        .form__actions { grid-column: 1 / -1; display: flex; gap: 8px; }
        .btn {
          font: inherit;
          padding: 6px 12px;
          border-radius: 4px;
          border: 1px solid var(--divider-color, #444);
          background: var(--secondary-background-color, #2a2a2a);
          color: var(--primary-text-color);
          cursor: pointer;
        }
        .btn--primary { background: var(--primary-color, #03a9f4); color: #fff; border-color: transparent; }
        .btn--danger { background: #b00020; color: #fff; border-color: transparent; }
        .btn[disabled] { opacity: 0.5; cursor: not-allowed; }
        .error { color: #ff5252; margin-top: 8px; font-size: 0.85rem; }
        .event { border-top: 1px solid var(--divider-color, #333); padding: 10px 0; }
        .event:first-child { border-top: 0; }
        .event--editing { background: rgba(3,169,244,0.08); border-radius: 4px; padding: 8px; }
        .event__header { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
        .event__time { font-weight: 500; }
        .event__status { font-size: 0.75rem; padding: 2px 8px; border-radius: 12px; background: rgba(255,255,255,0.08); }
        .event__status--confirmed { background: rgba(46, 204, 113, 0.25); }
        .event__status--cancelled { background: rgba(255, 82, 82, 0.2); }
        .event__status--pending { background: rgba(255, 193, 7, 0.25); }
        .event__notes { font-size: 0.85rem; color: var(--secondary-text-color); margin-top: 4px; }
        .event__actions { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
        .plan { margin-top: 6px; font-size: 0.85rem; }
        .plan--blocked, .plan--unknown { color: var(--secondary-text-color); font-style: italic; }
        .plan__row { display: flex; gap: 8px; align-items: baseline; padding: 1px 0; }
        .plan__label { color: var(--secondary-text-color); min-width: 200px; }
        .plan__row--ok { color: #2ecc71; }
        .plan__row--warn { color: #f1c40f; }
        .plan__row--unknown { color: var(--secondary-text-color); font-style: italic; }
        .plan__warn { color: #f1c40f; margin-top: 4px; font-size: 0.8rem; }
        .empty { color: var(--secondary-text-color); font-size: 0.9rem; padding: 12px 0; }
      </style>
      <div class="card">
        <h2>Сценарії відключень</h2>
        <div class="subtitle">Ручне внесення запланованих відключень. Час — у часовому поясі HA: <b>${this._escape(this._haTz)}</b>.</div>
        <div class="form">
          <label>Початок
            <input type="datetime-local" data-field="start" value="${this._escape(this._form.start)}" />
          </label>
          <label>Кінець
            <input type="datetime-local" data-field="end" value="${this._escape(this._form.end)}" />
          </label>
          <label style="grid-column: 1 / -1;">Примітка (необов'язково)
            <input type="text" data-field="notes" value="${this._escape(this._form.notes)}" placeholder="Напр., ремонт ТП-12" />
          </label>
          <div class="form__actions">
            <button class="btn btn--primary" data-action="add" ${this._busy ? "disabled" : ""}>Зберегти й підтвердити</button>
          </div>
        </div>
        ${errorBlock}
        <h2 style="margin-top:18px">Майбутні відключення</h2>
        ${listHtml}
      </div>
    `;
    this._wire();
  }

  _wire() {
    if (!this.shadowRoot) return;
    // Form inputs
    this.shadowRoot.querySelectorAll("input[data-field]").forEach((el) => {
      el.addEventListener("change", (e) => {
        const field = e.target.getAttribute("data-field");
        const isEdit = e.target.closest(".form--inline") != null;
        if (isEdit) {
          this._editForm = {
            ...this._editForm,
            [field]: e.target.value,
          };
        } else {
          this._form = { ...this._form, [field]: e.target.value };
        }
      });
    });
    // Buttons
    this.shadowRoot.querySelectorAll("button[data-action]").forEach((el) => {
      el.addEventListener("click", (e) => this._onAction(e));
    });
  }

  async _onAction(e) {
    const el = e.currentTarget;
    const action = el.getAttribute("data-action");
    const id = el.getAttribute("data-id");
    const version = el.getAttribute("data-version");
    if (action === "add") {
      await this._addEvent();
      return;
    }
    if (action === "edit" && id) {
      const ev = (this._events || []).find((e) => e.id === id);
      if (ev) this._startEdit(ev);
      return;
    }
    if (action === "cancel-edit") {
      this._cancelEdit();
      return;
    }
    if (action === "save-edit" && id) {
      await this._saveEdit(id, version ? Number(version) : 0);
      return;
    }
    if (action === "cancel" && id) {
      await this._cancelEvent(id);
      return;
    }
    if (action === "plan" && id) {
      const plan = await this._computePlan(id);
      this._planCache = {
        ...this._planCache,
        [id]: { plan, ts: Date.now() },
      };
      this._render();
      return;
    }
  }
}

if (!customElements.get("k-outage-scenarios-card")) {
  customElements.define(
    "k-outage-scenarios-card",
    KOutageScenariosCard
  );
}
