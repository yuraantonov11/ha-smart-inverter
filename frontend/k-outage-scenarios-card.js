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

// ---------------------------------------------------------------------
// Module-level helper: read the offset (``GMT+02:00`` /
// ``GMT-05:00`` / ``GMT``) of ``tz`` at the given UTC instant
// via ``Intl.DateTimeFormat``. Returns the offset in MINUTES
// east of UTC, or ``null`` if the value cannot be parsed (very
// rare -- only when the runtime does not support
// ``timeZoneName: 'longOffset'``).
// ---------------------------------------------------------------------
function readOffsetMinutes(date, tz) {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      timeZoneName: "longOffset",
    }).formatToParts(date);
    const tzn = parts.find((p) => p.type === "timeZoneName");
    if (!tzn || !tzn.value) return null;
    // ``tzn.value`` is ``"GMT"`` (UTC), ``"GMT+02:00"``,
    // ``"GMT-05:00"`` or (older ICU) ``"UTC+02:00"``.
    let raw = tzn.value;
    if (raw.startsWith("UTC")) raw = "GMT" + raw.slice(3);
    if (raw === "GMT") return 0;
    const m = raw.match(/^GMT([+-])(\d{1,2}):?(\d{2})?$/);
    if (!m) return null;
    const sign = m[1] === "-" ? -1 : 1;
    const hours = parseInt(m[2], 10);
    const mins = parseInt(m[3] || "0", 10);
    return sign * (hours * 60 + mins);
  } catch (e) {
    return null;
  }
}
//
// Version: 1.2.0
//
// 1.2.0 changes (manual-scenario UI v2 -- six defect fixes):
//   * DEFECT 1 (callService arg order): every
//     ``hass.callService(domain, service, data, null, true)``
//     becomes ``hass.callService(domain, service, data, null,
//     false, true)`` -- the 5th argument is
//     ``notifyOnError``, the 6th is ``returnResponse``
//     per https://raw.githubusercontent.com/home-assistant/
//     frontend/dev/src/types.ts. The handler return value
//     lives at ``res.response.response`` (the
//     ``ServiceCallResponse<T> = { context, response? }``
//     shape), NOT at ``res.response``. The harness was updated
//     the same way.
//   * DEFECT 3 (notes null vs empty string): the card now
//     sends the ``<input>`` value verbatim -- ``""`` for an
//     empty field, the typed string otherwise. The previous
//     code coalesced empty input to ``null`` which the schema
//     rejected. The service contract distinguishes
//     ``""`` (clear the note) from a missing field.
//   * DEFECT 4 (DST and midnight rollover):
//     ``_localInputToHaIso`` now reads the actual HA offset
//     at the typed wall-clock via
//     ``Intl.DateTimeFormat({timeZone: haTz, timeZoneName:
//     'longOffset'})`` and handles non-existent (spring
//     forward -- nudged +1h) and ambiguous (autumn fallback
//     -- prefer standard time) wall-clocks. ``_nowPlusHoursLocal``
//     now rolls the day forward when the +2h/+4h window
//     crosses midnight in the HA zone, and the default form
//     is computed on the first ``set hass`` so a missing
//     ``hass.config.time_zone`` at construction time no
//     longer produces stale defaults.
//   * DEFECT 5 (input persistence on set hass): the form
//     DOM is rendered ONCE (in the constructor) and is NOT
//     touched on subsequent ``set hass`` calls. The events
//     list section is the only DOM that re-renders on
//     telemetry updates. ``_refresh()`` is debounced to
//     250 ms so a burst of telemetry updates does not
//     trigger a redundant ``list_outage_events`` per tick.
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
    // DEFECT 5: the form DOM is rendered ONCE in the
    // constructor. Subsequent ``set hass`` calls do
    // NOT rebuild the form, so operator-typed values
    // (notes text, focus, datetime-local state)
    // survive every telemetry tick. The events list
    // is the only DOM that re-renders on ``set hass``;
    // ``_renderListOnly`` patches that section in
    // place. ``_scheduleRefresh`` debounces the
    // service call to 250 ms so bursts of telemetry
    // updates coalesce into one
    // ``list_outage_events`` call.
    this._render();
    this._formRendered = true;
    // Inline notice surfaced under the form when
    // ``_localInputToHaIso`` returns a non-empty
    // ``reason`` (non-existent / ambiguous DST). The
    // operator sees a non-blocking message instead of
    // a silent coercion. Stored as a string; cleared
    // by the next successful save.
    this._timeAdjustedNotice = null;
    // Tracks the most recent _refresh() promise so a
    // burst of telemetry updates coalesce into a
    // single in-flight ``list_outage_events`` call.
    this._refreshTimer = null;
    this._refreshInflight = null;
    // DEFECT 4: the HA TZ must be known before we can
    // compute sensible form defaults. We defer the
    // default ``start``/``end`` (``now + 2h`` /
    // ``now + 4h`` in HA TZ) until the FIRST
    // ``set hass`` that provides a valid
    // ``hass.config.time_zone``. The flag prevents the
    // constructor from seeding stale UTC defaults.
    this._defaultsSeeded = false;
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
      const tzChanged = hass.config.time_zone !== this._haTz;
      this._haTz = hass.config.time_zone;
      // The "Час — у часовому поясі HA: <b>...</b>"
      // label was rendered in the constructor
      // with the safe default ("UTC"). When the
      // first ``set hass`` lands with the real
      // ``hass.config.time_zone``, the label
      // MUST reflect it; otherwise the operator
      // sees a stale UTC string. We patch the
      // label in place -- never call
      // ``_render()`` here (it would clobber
      // the in-progress form edits and lose
      // focus).
      if (tzChanged) {
        this._updateTzLabel();
      }
      // DEFECT 4: seed the form defaults the first time
      // we know the HA TZ so a missing hass at
      // construction time does not produce stale
      // defaults. Re-seed only when the operator
      // changes the HA TZ AND the form fields are
      // still empty (the operator has not typed
      // anything); re-seeding a populated form would
      // clobber in-progress edits (DEFECT 5).
      if (!this._defaultsSeeded) {
        this._form = {
          start: this._defaultStartLocal(this._haTz),
          end: this._defaultEndLocal(this._haTz),
          notes: "",
        };
        this._defaultsSeeded = true;
        // Patch the form inputs in place -- do NOT
        // call ``_render()`` (which would clobber the
        // DOM and lose focus if the operator happens
        // to be typing when the first ``set hass`` arrives).
        this._patchFormInputs();
      } else if (
        tzChanged &&
        (!this._form.start ||
          this._form.start === this._defaultStartLocal("UTC"))
      ) {
        this._form = {
          start: this._defaultStartLocal(this._haTz),
          end: this._defaultEndLocal(this._haTz),
          notes: "",
        };
        this._patchFormInputs();
      }
    }
    if (hass) this._scheduleRefresh();
  }

  getCardSize() {
    return 4;
  }

  connectedCallback() {
    if (this._hass) this._scheduleRefresh();
  }

  // ----- Data flow ---------------------------------------------------

  // DEFECT 5: debounced wrapper around ``_refresh``.
  // The coordinator pushes ``set hass`` every few
  // seconds; without coalescing we would fire
  // ``list_outage_events`` on every tick. The 250 ms
  // debounce gives the burst time to settle while
  // keeping the events list responsive to operator
  // actions (Save / Cancel / Edit / "Оновити план"),
  // which call ``_refreshImmediate`` directly.
  _scheduleRefresh() {
    if (this._refreshTimer != null) {
      clearTimeout(this._refreshTimer);
    }
    this._refreshTimer = setTimeout(() => {
      this._refreshTimer = null;
      this._refreshImmediate();
    }, 250);
  }

  async _refreshImmediate() {
    if (!this._hass) return;
    if (this._refreshInflight) {
      // Coalesce: a refresh is already in flight; the
      // current list payload will be returned by the
      // same call (HA is single-writer). We do not
      // stack a second call here -- the in-flight
      // promise resolves with the same data the new
      // call would have received.
      return this._refreshInflight;
    }
    const p = this._refresh();
    this._refreshInflight = p;
    try {
      await p;
    } finally {
      this._refreshInflight = null;
    }
  }

  async _refresh() {
    if (!this._hass) return;
    try {
      // DEFECT 1: the 5th positional argument to
      // ``hass.callService`` is ``notifyOnError``
      // (NOT ``returnResponse``); the 6th is
      // ``returnResponse``. The card needs the
      // handler return value, so we pass
      // ``notifyOnError=false`` (we render the
      // error inline) and ``returnResponse=true``.
      // See
      // https://raw.githubusercontent.com/home-assistant/frontend/dev/src/types.ts
      // -- ``callService<T = any>(domain, service,
      // serviceData?, target?, notifyOnError?,
      // returnResponse?): Promise<ServiceCallResponse<T>>``
      // where ``ServiceCallResponse<T> = { context:
      // Context; response?: T }``. The handler
      // return value lives at ``res.response.response``,
      // NOT ``res.response``.
      const res = await this._hass.callService(
        "powmr_inverter",
        "list_outage_events",
        { entry_id: this._config.entry_id || null },
        null,
        false,
        true
      );
      // Defensive triple-path: prefer the documented
      // ``res.response.response`` shape; fall back to
      // the bare-payload WS binding that some older
      // frontends surface (``res = payload``); fall
      // back again to ``res.response`` (the OLD,
      // wrong path the previous card used) only if
      // neither shape contains ``events``. The list
      // payload MUST contain an ``events`` array;
      // any other shape means the call failed.
      let payload = null;
      if (res && res.response && Array.isArray(res.response.events)) {
        payload = res.response;
      } else if (res && Array.isArray(res.events)) {
        payload = res;
      } else if (res && res.response && typeof res.response === "object") {
        payload = res.response;
      } else if (res && typeof res === "object") {
        payload = res;
      }
      this._events = payload && Array.isArray(payload.events)
        ? payload.events
        : [];
      this._error = null;
    } catch (e) {
      this._error = String(e && e.message ? e.message : e);
      this._events = [];
    }
    // DEFECT 5: do NOT call ``_render()`` -- the
    // form DOM is preserved untouched. Patch only the
    // events list section.
    this._renderListOnly();
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
  //
  // DEFECT 4 (DST and midnight rollover): the previous
  // probe-and-roundtrip technique built a UTC ``Date``
  // from the typed numbers and read back the wall-clock
  // in HA TZ to compute the offset. That worked for
  // unambiguous wall-clocks (Kyiv, etc.) but produced
  // the wrong ISO for DST zones when the typed clock
  // was non-existent (spring forward) or ambiguous
  // (autumn fallback). The fix below reads the
  // explicit long-offset (``GMT+02:00``) at the typed
  // wall-clock via
  // ``Intl.DateTimeFormat({timeZoneName: 'longOffset'})``
  // and handles the three cases explicitly. The Python
  // mirror lives in ``services/outage.py::_resolve_local_to_utc``.
  _localInputToHaIso(localValue, haTz) {
    if (!localValue) return { iso: "", reason: null };
    const m = String(localValue).match(
      /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/
    );
    if (!m) return { iso: localValue, reason: null };
    const [, y, mo, d, hh, mm, ssRaw] = m;
    const ss = ssRaw || "00";
    const tz = haTz || this._haTz || "UTC";
    // Build a probe ``Date`` from the typed numbers
    // interpreted AS IF they were UTC. Reading the
    // wall-clock of that probe in ``tz`` tells us the
    // offset ``tz`` has at this instant. The probe
    // itself is only used to read the offset -- we do
    // not assume it represents a valid wall-clock in
    // ``tz``.
    const probe = new Date(
      Date.UTC(
        Number(y),
        Number(mo) - 1,
        Number(d),
        Number(hh),
        Number(mm),
        Number(ss)
      )
    );
    if (isNaN(probe.getTime())) {
      return { iso: localValue, reason: null };
    }
    // Read the offset at the probe instant via
    // ``Intl.DateTimeFormat`` with
    // ``timeZoneName: 'longOffset'``. The output is
    // a string like ``GMT+02:00`` / ``GMT-05:00`` /
    // ``GMT`` (UTC). We parse it to a signed minute
    // count. Using the probe UTC instant (not the
    // typed UTC numbers) ensures we read the offset
    // for the timezone the instant is in -- a
    // UTC-12 zone is still ``GMT-12:00`` even if the
    // typed numbers were outside its wall-clock.
    const offsetMin = readOffsetMinutes(probe, tz);
    if (offsetMin == null) {
      return { iso: localValue, reason: null };
    }
    // The instant whose wall-clock in ``tz`` equals
    // the typed numbers is:
    //   targetMs = typedWallClockAsUtc - offsetMin * 60000
    // ``typedWallClockAsUtc`` is the typed numbers
    // interpreted AS IF they were UTC
    // (``Date.UTC(...)``).
    const typedWallClockAsUtc = Date.UTC(
      Number(y),
      Number(mo) - 1,
      Number(d),
      Number(hh),
      Number(mm),
      Number(ss)
    );
    // DST handling (DEFECT 4 round 4): build
    // candidate UTC instants for every
    // plausible offset in 15-minute steps
    // (``-12:00`` to ``+14:00``), then check
    // which one round-trips to the typed
    // wall-clock in ``tz``. The set of matches
    // tells us:
    //   * 0 matches → the typed wall-clock is
    //     non-existent (DST spring forward).
    //     The previous round silently nudged
    //     to the next hour, which the operator
    //     rejected. We now return the typed
    //     wall-clock verbatim and a
    //     ``non_existent`` reason so the form
    //     surfaces the error.
    //   * 1 match → unambiguous; use that
    //     offset.
    //   * 2+ matches → ambiguous (DST autumn
    //     fallback). Pick the LATER offset
    //     (standard time, the conservative
    //     choice for event scheduling) and
    //     surface ``ambiguous``.
    const offsets = [];
    for (let hh = -12; hh <= 14; hh++) {
      for (const mm of [0, 15, 30, 45]) {
        offsets.push(hh * 60 + mm);
      }
    }
    const sliceLocal = localValue.slice(0, 16);
    const matches = [];
    for (const off of offsets) {
      const ms = typedWallClockAsUtc - off * 60000;
      const cand = new Date(ms);
      if (isNaN(cand.getTime())) continue;
      const back = this._isoToLocalInput(
        cand.toISOString(),
        tz
      );
      if (back === sliceLocal) {
        matches.push({ off, cand });
      }
    }
    if (matches.length === 0) {
      // Non-existent. Per operator requirement:
      // do NOT silently save a different hour.
      // The form's interval validator surfaces
      // the error.
      return { iso: localValue, reason: "non_existent" };
    }
    if (matches.length === 1) {
      // Unambiguous. Use the single match.
      const cand = matches[0].cand;
      return {
        iso: cand.toISOString(),
        reason: null,
      };
    }
    // 2+ matches: ambiguous. Pick the candidate
    // that corresponds to the LATER UTC
    // instant. For autumn DST, this is the
    // SMALLER offset (standard time, e.g. CET
    // for Berlin) because the post-transition
    // wall-clock happens LATER in UTC than the
    // pre-transition one. For spring, ambiguous
    // does not apply (the wall-clock is
    // non-existent there).
    matches.sort((a, b) => b.cand.getTime() - a.cand.getTime());
    const chosen = matches[0];
    return {
      iso: chosen.cand.toISOString(),
      reason: "ambiguous",
      preferred_offset_min: chosen.off,
    };
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
    // ``Intl.DateTimeFormat`` returns the wall-clock
    // of the current instant in ``tz`` -- not the
    // browser clock. Using the browser clock here
    // would mis-align the default window whenever the
    // operator is in a different timezone from HA
    // (the common case).
    const nowMs = Date.now();
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: tz,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).formatToParts(new Date(nowMs));
    const get = (t) =>
      (parts.find((p) => p.type === t) || {}).value || "";
    let y = Number(get("year"));
    let mo = Number(get("month"));
    let da = Number(get("day"));
    let hh = Number(get("hour"));
    const mm = Number(get("minute"));
    // DEFECT 4 (midnight rollover): when the HA clock
    // is 23:30 and we add 2 hours, the result is
    // 01:30 NEXT DAY, not 25:30 / 01:30 same day.
    // Compute the +N hour target via ``Date`` in the
    // HA TZ and read it back: this automatically
    // rolls the day forward across midnight, across
    // DST transitions, and across month / year
    // boundaries.
    const targetMs = nowMs + hours * 60 * 60 * 1000;
    const targetParts = new Intl.DateTimeFormat("en-CA", {
      timeZone: tz,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).formatToParts(new Date(targetMs));
    const tget = (t) =>
      (targetParts.find((p) => p.type === t) || {}).value || "";
    const ty = Number(tget("year"));
    const tmo = Number(tget("month"));
    const tda = Number(tget("day"));
    const thh = Number(tget("hour"));
    const tmm = Number(tget("minute"));
    const norm = (n) =>
      n === 24 ? 0 : n; // ``hour: "2-digit"`` -> "24" for midnight in some locales
    const pad = (n) => String(n).padStart(2, "0");
    return (
      `${ty}-${pad(tmo)}-${pad(tda)}T${pad(norm(thh))}:${pad(norm(tmm))}`
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
    const startResolved = this._localInputToHaIso(
      this._form.start, this._haTz
    );
    const endResolved = this._localInputToHaIso(
      this._form.end, this._haTz
    );
    const startIso = startResolved.iso;
    const endIso = endResolved.iso;
    // DEFECT 4 (round 5): a wall-clock that
    // does not exist in the HA timezone (DST
    // spring forward) MUST be rejected before
    // the service is called. The previous
    // round surfaced a notice ("використано
    // <actual>") and continued with a
    // silently-nudged ISO. The operator
    // rejected that: we must NOT save a
    // different hour than the operator typed.
    // We surface a clear inline error and
    // return without calling the service.
    const nonExistent = (
      (startResolved.reason === "non_existent") ||
      (endResolved.reason === "non_existent")
    );
    if (nonExistent) {
      const which = (
        startResolved.reason === "non_existent"
      )
        ? "початку"
        : "кінця";
      this._error =
        `Вказаний час ${which} не існує в часовому поясі HA ` +
        `(${this._haTz}); оберіть інший час.`;
      this._timeAdjustedNotice = null;
      this._renderListOnly();
      this._updateBusyState();
      this._renderNotice();
      return;
    }
    // Ambiguous wall-clock: pick the LATER
    // (standard-time) offset automatically --
    // the operator doesn't need to act.
    if (startResolved.reason || endResolved.reason) {
      this._timeAdjustedNotice =
        `Час існує двічі через переведення годинника; ` +
        `збережено з пізнішим (зимовим) зміщенням.`;
    } else {
      this._timeAdjustedNotice = null;
    }
    const err = this._validateInterval(startIso, endIso);
    if (err) {
      this._error = err;
      this._renderListOnly();
      this._updateBusyState();
      this._renderNotice();
      return;
    }
    this._busy = true;
    this._error = null;
    this._updateBusyState();
    this._renderNotice();
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
      // correct ``EventStore``.
      //
      // DEFECT 3: ``notes`` is forwarded VERBATIM
      // from the DOM ``<input>``. An empty field
      // is ``""`` (NOT ``null``); the previous
      // behaviour coalesced empty input to
      // ``null`` which the schema rejected. The
      // service contract is
      //   * ``""`` -- "no note" / clear.
      //   * any string -- the typed value.
      await this._hass.callService(
        "powmr_inverter",
        "add_outage_event",
        {
          source: "manual:dashboard",
          start: startIso,
          end: endIso,
          notes: this._form.notes == null ? "" : this._form.notes,
          entry_id: this._config.entry_id || null,
        },
        null,
        false,
        true
      );
    } catch (e) {
      this._error = String(e && e.message ? e.message : e);
      this._busy = false;
      this._updateBusyState();
      this._renderNotice();
      return;
    }
    // Reset the form to the next "now + 2h"
    // window so the operator can quickly add a
    // second event.
    this._form = {
      start: this._defaultStartLocal(this._haTz),
      end: this._defaultEndLocal(this._haTz),
      notes: "",
    };
    this._timeAdjustedNotice = null;
    // Patch form inputs in place; do NOT call
    // ``_render()`` (DEFECT 5 -- would lose any
    // operator focus the moment the events list
    // re-renders).
    this._patchFormInputs();
    this._busy = false;
    this._updateBusyState();
    this._renderNotice();
    await this._refreshImmediate();
  }

  async _cancelEvent(eventId) {
    if (this._busy) return;
    this._busy = true;
    this._error = null;
    this._updateBusyState();
    try {
      // ``cancel_outage_event`` does not require
      // a response payload (the service contract is
      // "fire and forget"); we still pass
      // ``notifyOnError=false`` to keep the inline
      // error path consistent.
      await this._hass.callService(
        "powmr_inverter",
        "cancel_outage_event",
        {
          id: eventId,
          reason: "cancelled from dashboard",
          entry_id: this._config.entry_id || null,
        },
        null,
        false
      );
      await this._refreshImmediate();
    } catch (e) {
      this._error = String(e && e.message ? e.message : e);
    } finally {
      this._busy = false;
      this._updateBusyState();
      this._renderNotice();
    }
  }

  async _saveEdit(eventId, version) {
    if (this._busy) return;
    const startResolved = this._localInputToHaIso(
      this._editForm.start, this._haTz
    );
    const endResolved = this._localInputToHaIso(
      this._editForm.end, this._haTz
    );
    const startIso = startResolved.iso;
    const endIso = endResolved.iso;
    // DEFECT 4 (round 5): block the update
    // when the typed wall-clock is non-existent
    // in the HA timezone (DST spring forward).
    // The previous round surfaced a notice and
    // saved the silently-nudged ISO; the
    // operator rejected that.
    const nonExistent = (
      (startResolved.reason === "non_existent") ||
      (endResolved.reason === "non_existent")
    );
    if (nonExistent) {
      const which = (
        startResolved.reason === "non_existent"
      )
        ? "початку"
        : "кінця";
      this._error =
        `Вказаний час ${which} не існує в часовому поясі HA ` +
        `(${this._haTz}); оберіть інший час.`;
      this._timeAdjustedNotice = null;
      this._renderListOnly();
      this._renderNotice();
      return;
    }
    if (startResolved.reason || endResolved.reason) {
      this._timeAdjustedNotice =
        `Час існує двічі через переведення годинника; ` +
        `збережено з пізнішим (зимовим) зміщенням.`;
    } else {
      this._timeAdjustedNotice = null;
    }
    const err = this._validateInterval(startIso, endIso);
    if (err) {
      this._error = err;
      this._renderListOnly();
      this._renderNotice();
      return;
    }
    this._busy = true;
    this._error = null;
    this._updateBusyState();
    this._renderNotice();
    try {
      // DEFECT 3: ``notes`` is forwarded VERBATIM
      // from the DOM ``<input>``. ``""`` means "no
      // note" / clear; any other string is the new
      // note. ``null`` is reserved for "do not
      // change" (the operator did not provide the
      // field at all), which the inline edit form
      // never produces.
      const notesValue =
        this._editForm.notes == null ? "" : this._editForm.notes;
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
        },
        null,
        false,
        true
      );
      this._editing = null;
      await this._refreshImmediate();
    } catch (e) {
      this._error = String(e && e.message ? e.message : e);
    } finally {
      this._busy = false;
      this._updateBusyState();
      this._renderListOnly();
      this._renderNotice();
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
      _eventId: ev.id,
      _version: ev.version,
    };
    // Re-render the events list section so the editing
    // row includes the inline edit form. The form
    // section is NOT touched (DEFECT 5): only the
    // events slot under ``[data-section="events"]`` is
    // rebuilt.
    this._renderListOnly();
  }

  _cancelEdit() {
    this._editing = null;
    // Patch the inline-edit DOM in place (remove
    // the form); the form DOM is preserved
    // untouched (DEFECT 5).
    this._renderListOnly();
  }

  async _computePlan(eventId) {
    if (!this._hass || !this._config.entry_id) return null;
    try {
      const res = await this._hass.callService(
        "powmr_inverter",
        `${this._config.entry_id}_compute_outage_plan`,
        { event_id: eventId, force_recompute: true },
        null,
        false,
        // The runtime service registration uses
        // ``supports_response=True`` so HA returns a
        // payload; ``returnResponse=true`` wraps it
        // under ``res.response.response``. We still
        // accept the bare response and the legacy
        // ``res.response`` shape for older WS
        // bindings (DEFECT 1).
        true
      );
      let r = null;
      if (res && res.response && typeof res.response === "object") {
        r = res.response;
      } else if (res && typeof res === "object") {
        r = res;
      }
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
    // Two call signatures:
    //   * ``_renderEditForm(ev)`` -- backing-store
    //     helper used by the initial-render path.
    //     Returns the HTML for the inline edit form
    //     (caller places it inside the row).
    //   * ``_renderEditForm()`` -- in-place DOM
    //     patch used by ``_startEdit``. Updates the
    //     ``.form--inline-slot`` for the editing
    //     event row without rebuilding the form
    //     DOM (DEFECT 5).
    let editingEvent = ev;
    if (!editingEvent) {
      editingEvent =
        (this._events || []).find((e) => e.id === this._editing) ||
        null;
    }
    if (editingEvent) {
      this._editForm = {
        start: this._isoToLocalInput(editingEvent.start_iso, this._haTz),
        end: this._isoToLocalInput(editingEvent.end_iso, this._haTz),
        notes: editingEvent.notes == null ? "" : editingEvent.notes,
        _eventId: editingEvent.id,
        _version: editingEvent.version,
      };
    }
    const editHtml =
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
      `<button class="btn btn--primary" data-action="save-edit" data-id="${this._escape(this._editForm._eventId || (editingEvent && editingEvent.id) || "")}" data-version="${this._escape(this._editForm._version != null ? this._editForm._version : (editingEvent && editingEvent.version) || "")}">Зберегти</button>` +
      `</div>` +
      `</div>`;
    // In-place patch: rebuild only the inline form
    // inside the editing row. The form inputs and
    // the notes text are preserved untouched (the
    // operator typed into them).
    if (!ev && this._editing && this.shadowRoot) {
      const target = this.shadowRoot.querySelector(
        `[data-section="events"] .event--editing`
      );
      if (target) {
        let slot = target.querySelector(".form--inline-slot");
        if (!slot) {
          slot = document.createElement("div");
          slot.className = "form--inline-slot";
          target.appendChild(slot);
        }
        slot.innerHTML = editHtml;
        slot.querySelectorAll("button[data-action]").forEach((btn) => {
          if (btn.__wired) return;
          btn.__wired = true;
          btn.addEventListener("click", (e) => this._onAction(e));
        });
        slot.querySelectorAll("input[data-field]").forEach((inp) => {
          if (inp.__wired) return;
          inp.__wired = true;
          inp.addEventListener("change", (e) => {
            const field = e.target.getAttribute("data-field");
            this._editForm = {
              ...this._editForm,
              [field]: e.target.value,
            };
          });
        });
      }
      return "";
    }
    return editHtml;
  }

  _render() {
    // Initial / full render. Called ONCE in the
    // constructor. Subsequent telemetry updates do
    // NOT call ``_render()``; they call
    // ``_renderListOnly`` which patches only the
    // events list section (DEFECT 5).
    const listHtml = this._renderEventsHtml();
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
        .notice { color: var(--secondary-text-color); margin-top: 8px; font-size: 0.85rem; font-style: italic; }
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
        <div class="subtitle">Ручне внесення запланованих відключень. Час — у часовому поясі HA: <b data-tz-label>${this._escape(this._haTz)}</b>.</div>
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
        <div data-notice="error"></div>
        <div data-notice="info"></div>
        <h2 style="margin-top:18px">Майбутні відключення</h2>
        <div data-section="events">${listHtml}</div>
      </div>
    `;
    this._wire();
  }

  _renderEventsHtml() {
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
    return upcoming.length
      ? upcoming.map((ev) => this._renderEventRow(ev)).join("")
      : `<div class="empty">Немає запланованих відключень. Додайте перше за допомогою форми нижче.</div>`;
  }

  // Patch the events list section in place. Called
  // by ``_refresh`` and the various action handlers.
  // The form DOM (start / end / notes / add button)
  // is NOT touched (DEFECT 5).
  _renderListOnly() {
    if (!this.shadowRoot) return;
    const slot = this.shadowRoot.querySelector('[data-section="events"]');
    if (!slot) return;
    // DEFECT 5 round 4: when the operator has the
    // edit form open, the in-progress values and
    // the focus position live ONLY in the DOM
    // (the user is typing). The previous round
    // replaced ``slot.innerHTML`` unconditionally
    // which discarded the in-progress input. We
    // snapshot the open edit form (values +
    // focused field) before the replace, then
    // re-apply it after the replace. The list
    // section is still rebuilt in full so plan
    // results and status pills update.
    let editSnapshot = null;
    if (this._editing) {
      const editRoot = slot.querySelector(
        ".event--editing .form--inline"
      );
      if (editRoot) {
        const focused =
          editRoot.querySelector(":focus");
        editSnapshot = {
          values: {},
          focusedField: focused
            ? focused.getAttribute("data-field")
            : null,
          focusedSelectionStart: focused
            ? focused.selectionStart
            : null,
          focusedSelectionEnd: focused
            ? focused.selectionEnd
            : null,
        };
        editRoot
          .querySelectorAll("input[data-field]")
          .forEach((el) => {
            editSnapshot.values[
              el.getAttribute("data-field")
            ] = el.value;
          });
      }
    }
    slot.innerHTML = this._renderEventsHtml();
    this._wireListButtons();
    // Re-apply the snapshot: fill the inputs
    // with the values the operator had typed,
    // and re-focus the field that was focused
    // (with its caret position). ``_wireFormInputs``
    // wires the change handler on the freshly
    // created input nodes so further edits
    // continue to flow into ``_editForm``.
    this._wireFormInputs();
    if (editSnapshot) {
      const newEditRoot = slot.querySelector(
        ".event--editing .form--inline"
      );
      if (newEditRoot) {
        Object.entries(editSnapshot.values).forEach(
          ([field, value]) => {
            const inp = newEditRoot.querySelector(
              `input[data-field="${field}"]`
            );
            if (inp) inp.value = value;
          }
        );
        if (editSnapshot.focusedField) {
          const focused = newEditRoot.querySelector(
            `input[data-field="${editSnapshot.focusedField}"]`
          );
          if (focused) {
            focused.focus();
            if (
              editSnapshot.focusedSelectionStart !=
                null &&
              typeof focused.setSelectionRange ===
                "function"
            ) {
              try {
                focused.setSelectionRange(
                  editSnapshot.focusedSelectionStart,
                  editSnapshot.focusedSelectionEnd
                );
              } catch (_) {
                // Some input types (e.g. datetime-local)
                // do not support setSelectionRange;
                // ignore.
              }
            }
          }
        }
      }
    }
  }
  _wireFormInputs() {
    if (!this.shadowRoot) return;
    const seen = this._wiredInputs || new WeakSet();
    this._wiredInputs = seen;
    this.shadowRoot.querySelectorAll(
      ".form input[data-field]"
    ).forEach((el) => {
      if (seen.has(el)) return;
      seen.add(el);
      el.addEventListener("change", (e) => {
        const field = e.target.getAttribute("data-field");
        const isEdit =
          e.target.closest(".form--inline") != null;
        if (isEdit) {
          this._editForm = {
            ...this._editForm,
            [field]: e.target.value,
          };
        } else {
          this._form = {
            ...this._form,
            [field]: e.target.value,
          };
        }
      });
    });
  }

  // Patch the error / notice blocks without touching
  // the form DOM (DEFECT 5).
  _renderNotice() {
    if (!this.shadowRoot) return;
    const errBlock = this.shadowRoot.querySelector('[data-notice="error"]');
    const noticeBlock = this.shadowRoot.querySelector('[data-notice="info"]');
    if (errBlock) {
      errBlock.innerHTML = this._error
        ? `<div class="error">${this._escape(this._error)}</div>`
        : "";
    }
    if (noticeBlock) {
      noticeBlock.innerHTML = this._timeAdjustedNotice
        ? `<div class="notice">${this._escape(this._timeAdjustedNotice)}</div>`
        : "";
    }
  }

  // Update the form input values in place (called
  // when the defaults change -- first ``set hass``
  // with a known TZ, or after a successful save that
  // resets the form to "now + 2h"). Does NOT touch
  // the notes input if the operator has typed in it
  // (DEFECT 5).
  _patchFormInputs() {
    if (!this.shadowRoot) return;
    const setVal = (field, value) => {
      const inp = this.shadowRoot.querySelector(
        `.form input[data-field="${field}"]`
      );
      if (inp && document.activeElement !== inp) {
        inp.value = value == null ? "" : value;
      }
    };
    setVal("start", this._form.start);
    setVal("end", this._form.end);
    setVal("notes", this._form.notes);
  }

  // Update the "Час — у часовому поясі HA: <b>...</b>"
  // label. The label is a static part of the
  // initial ``_render()`` HTML, but the
  // ``this._haTz`` value comes from
  // ``hass.config.time_zone`` which the
  // constructor cannot read (no hass yet). On
  // the first ``set hass`` the value flips from
  // ``"UTC"`` (the safe constructor default) to
  // the real ``hass.config.time_zone``; the
  // label must reflect that change WITHOUT
  // rebuilding the form / losing focus.
  //
  // Defect: the previous round left the
  // label stuck on the constructor default
  // because the rendering function was the only
  // place that wrote the value, and rendering
  // only runs on the first mount. We now patch
  // the label in place whenever ``_haTz``
  // changes.
  _updateTzLabel() {
    if (!this.shadowRoot) return;
    const label = this.shadowRoot.querySelector(
      "[data-tz-label]"
    );
    if (!label) return;
    label.textContent = this._haTz || "UTC";
  }

  // Update the busy state of the primary action
  // button without rebuilding the form (DEFECT 5).
  _updateBusyState() {
    if (!this.shadowRoot) return;
    const btn = this.shadowRoot.querySelector(
      'button[data-action="add"]'
    );
    if (btn) {
      if (this._busy) btn.setAttribute("disabled", "");
      else btn.removeAttribute("disabled");
    }
  }

  _wire() {
    if (!this.shadowRoot) return;
    // Form inputs (DEFECT 5): the change handler is
    // wired via ``_wireFormInputs`` which is
    // idempotent (uses a WeakSet to skip nodes
    // that already have the listener). The main
    // form is wired in the constructor; the edit
    // form (an inline ``.form--inline`` rendered
    // inside the event row) is wired by
    // ``_renderListOnly`` when the operator opens
    // the edit form.
    this._wireFormInputs();
    // The form's primary action button ("Add") lives
    // outside the events list section -- wire it
    // here so the operator can save the form even
    // when no events are listed. Subsequent calls
    // to ``_wireListButtons`` (DEFECT 5) re-wire
    // only the events list buttons; the form's
    // button stays wired because ``el.__wired``
    // prevents duplicate listeners.
    this.shadowRoot
      .querySelectorAll(
        '.form button[data-action]:not([data-action="save-edit"])'
      )
      .forEach((el) => {
        if (el.__wired) return;
        el.__wired = true;
        el.addEventListener("click", (e) => this._onAction(e));
      });
    this._wireListButtons();
  }

  _wireListButtons() {
    if (!this.shadowRoot) return;
    this.shadowRoot
      .querySelectorAll('[data-section="events"] button[data-action]')
      .forEach((el) => {
        if (el.__wired) return;
        el.__wired = true;
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
      this._renderListOnly();
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
