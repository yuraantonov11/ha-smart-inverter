// power-history-card.js v2.0 — multi-series support
// R06 audit (2026-10-09):
//   - escapes user-supplied text (title, labels, names) to prevent
//     HTML injection from attribute values like "<x>" or
//     quotes that could break the surrounding markup;
//   - filters non-finite numbers (null, NaN, undefined,
//     strings) — they are SKIPPED rather than rendered
//     as "NaN" or "undefined";
//   - 0 is preserved as a real measurement (numeric
//     0 ≠ null, ≠ unknown, ≠ unavailable);
//   - gap detection: when a label is missing or
//     out-of-order, the line/bar path starts a new
//     sub-path so missing intervals are not
//     connected as if they were measured;
//   - ResizeObserver: the card re-renders on width
//     change and disconnects cleanly on removal;
//   - no addEventListener on the document or window
//     — only on the card itself, all cleaned up on
//     disconnect.
class PowerHistoryCard extends HTMLElement {
  connectedCallback() {
    if (typeof ResizeObserver === 'undefined') return;
    this._resizeObserver = new ResizeObserver(entries => {
      const width = entries[0]?.contentRect.width;
      if (width && width !== this._lastWidth) {
        this._lastWidth = width;
        this._render();
      }
    });
    this._resizeObserver.observe(this);
  }
  disconnectedCallback() {
    if (this._resizeObserver) {
      this._resizeObserver.disconnect();
      this._resizeObserver = null;
    }
  }
  setConfig(config) {
    if (!config.entity && !config.series) throw new Error('entity or series is required');
    this._config = config;
  }
  set hass(hass) {
    this._hass = hass;
    this._render();
  }
  getCardSize() { return 4; }

  _escape(v) {
    return String(v ?? '').replace(/[&<>"']/g,
      c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  _isFiniteNumber(v) {
    return typeof v === 'number' && Number.isFinite(v);
  }
  _cleanSeries(rawValues, rawLabels) {
    // Returns { values:number[], labels:string[],
    // gaps:number[], rawIndices:number[],
    // rawLength:number }. ``gaps`` is the
    // list of indices where the value or
    // label is missing or out-of-order; the
    // chart uses those to start a new
    // sub-path. ``rawIndices`` maps each
    // valid value back to its original
    // (time) index in the input array — the
    // chart uses it for x-axis positioning
    // so removing invalid points does NOT
    // compress the timeline.
    // ``rawLength`` is the ORIGINAL input
    // length (including the gaps). The
    // chart's x-axis uses it as the
    // canonical time span so that tail
    // gaps (where the last point is null)
    // are NOT silently dropped.
    const values = Array.isArray(rawValues) ? rawValues : [];
    const labels = Array.isArray(rawLabels) ? rawLabels : [];
    const out = { values: [], labels: [], gaps: [], rawIndices: [], rawLength: values.length };
    let prev = -Infinity;
    for (let i = 0; i < values.length; i++) {
      if (!this._isFiniteNumber(values[i])) {
        // R06: null / NaN / undefined / string are
        // skipped, NOT rendered as "0" or "NaN".
        out.gaps.push(i);
        continue;
      }
      const lbl = labels[i];
      if (lbl === undefined || lbl === null || lbl === '') {
        out.gaps.push(i);
        continue;
      }
      // Detect out-of-order labels (string compare on
      // hour-like strings "00:00".. "23:00" works for
      // the standard format used by the integration).
      const t = Date.parse(`1970-01-01T${String(lbl)}:00`);
      if (Number.isFinite(t) && t < prev) {
        out.gaps.push(i);
        continue;
      }
      if (Number.isFinite(t)) prev = t;
      out.values.push(values[i]);
      out.labels.push(String(lbl));
      out.rawIndices.push(i);
    }
    return out;
  }

  _getAttr(eid, key) {
    if (!this._hass) return null;
    const s = this._hass.states[eid];
    if (!s) return null;
    const v = s.attributes[key];
    return (Array.isArray(v) && v.length > 0) ? v : null;
  }

  _render() {
    if (!this._hass || !this._config) return;
    const cfg = this._config;
    const title = cfg.title || '';

    // Build series list
    let seriesList = [];
    if (cfg.series && Array.isArray(cfg.series)) {
      seriesList = cfg.series;
    } else {
      // Legacy single-entity mode
      seriesList = [{
        entity: cfg.entity,
        attribute: cfg.attribute || 'hourly_power_kw',
        labels_attribute: cfg.labels_attribute || 'hourly_labels',
        color: cfg.bar_color || '#f5b06a',
        name: cfg.name || '',
        unit_divisor: cfg.unit_divisor || 1,
      }];
    }

    // Resolve data for each series.
    // R06 follow-up (2026-10-09): each
    // series is resolved independently. The
    // raw INDEX is preserved alongside
    // each valid value so the x-axis
    // reflects the ORIGINAL time, not the
    // cleaned index. Two series with
    // different gaps therefore align on
    // the same time axis.
    const resolved = [];
    // The shared time axis is derived
    // from the LARGEST raw length across
    // all series (R07 follow-up: the
    // first-series-only approach could
    // miss tail gaps when the first
    // series had shorter data than the
    // others). We collect every series'
    // raw length and union them.
    let canonicalRawLength = 0;
    let firstSeriesSeen = false;
    for (const s of seriesList) {
      const rawValues = this._getAttr(s.entity, s.attribute);
      if (!rawValues) continue;
      const rawLabels = this._getAttr(s.entity, s.labels_attribute);
      const cleaned = this._cleanSeries(rawValues, rawLabels);
      if (cleaned.values.length === 0) continue;
      // ``cleaned.rawIndices[i]`` is the
      // raw (time) index of the i-th
      // valid point. ``cleaned.gaps`` are
      // raw indices in the same axis.
      const points = [];
      for (let i = 0; i < cleaned.values.length; i++) {
        points.push({
          rawIndex: cleaned.rawIndices[i],
          value: cleaned.values[i],
          label: cleaned.labels[i],
        });
      }
      const rawIndices = cleaned.rawIndices;
      if (!firstSeriesSeen) firstSeriesSeen = true;
      // ``cleaned.rawLength`` is the
      // original input length (the
      // number of raw samples BEFORE
      // invalid points were removed).
      // The shared time axis MUST
      // include gaps at the tail of the
      // longest series, even if no
      // series has a valid point at
      // that raw index.
      if (cleaned.rawLength > canonicalRawLength) {
        canonicalRawLength = cleaned.rawLength;
      }
      resolved.push({
        points: points,
        gaps: cleaned.gaps,
        color: s.color || '#f5b06a',
        name: s.name || '',
        divisor: s.unit_divisor || 1,
        rawIndices: rawIndices,
        rawLength: cleaned.rawLength,
      });
    }

    if (resolved.length === 0) {
      this.innerHTML = `<ha-card><div style="padding:16px;color:#999">${this._escape(title)}: no data</div></ha-card>`;
      return;
    }

    // The X axis spans the canonical raw
    // length across all series. The
    // longest series' raw length is the
    // canonical axis — this preserves
    // tail gaps in any single series.
    // R07 follow-up: the previous
    // implementation used only the
    // valid-subset of the first series,
    // which truncated the time axis
    // when the first series had fewer
    // samples than the others.
    const canonicalN = canonicalRawLength;
    const data = resolved[0].points.map(
      p => p.value,
    );
    const lbls = resolved[0].points.map(
      p => p.label || '',
    );
    const W = 500, H = 180, padL = 45, padR = 10, padT = 10, padB = 30;
    const chartW = W - padL - padR, chartH = H - padT - padB;

    // Compute max across all series
    let maxVal = 0;
    for (const r of resolved) {
      for (const p of r.points) {
        maxVal = Math.max(
          maxVal, p.value / r.divisor,
        );
      }
    }
    maxVal = Math.max(maxVal * 1.1, 0.01);

    let svg = '';

    // Grid + Y labels
    for (let i = 0; i <= 4; i++) {
      const y = padT + chartH - (chartH * i / 4);
      const val = maxVal * i / 4;
      svg += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="#333" stroke-width="0.5"/>`;
      const yLabel = this._isFiniteNumber(val)
        ? (val < 1 ? val.toFixed(2) : val.toFixed(1))
        : '0';
      svg += `<text x="${padL - 4}" y="${y + 3}" text-anchor="end" fill="#999" font-size="9">${yLabel}</text>`;
    }

    // X labels: only for points on the
    // canonical time axis. The first
    // series' labels are the labels of the
    // time axis. If the first series has a
    // gap, we still want a label at the
    // gap position (use the gap's expected
    // label from the raw input).
    const xLabelStep = Math.max(
      1, Math.floor(canonicalN / 8),
    );
    for (let i = 0; i < canonicalN; i += xLabelStep) {
      const x = padL
        + (i / Math.max(canonicalN - 1, 1))
        * chartW;
      // Find the label for raw index ``i``
      // — the first series' label at
      // rawIndex === i, or the raw label
      // at index ``i`` from the first
      // series' labels_attribute.
      let lblAt = '';
      const firstSeries = resolved[0];
      const idx = firstSeries.rawIndices.indexOf(i);
      if (idx >= 0) {
        lblAt = firstSeries.points[idx].label || '';
      }
      svg += `<text x="${x}" y="${H - 4}" text-anchor="middle" fill="#999" font-size="8">${this._escape(lblAt)}</text>`;
    }

    // Render each series. Each series
    // has its OWN gaps and its OWN
    // rawIndices.
    for (const r of resolved) {
      const n = r.points.length;
      // Use the canonical time axis to
      // derive x-positions: x for rawIndex
      // ``i`` is ``padL + (i / (canonicalN-1)) * chartW``.
      const xForRaw = (rawIndex) =>
        padL
        + (rawIndex / Math.max(canonicalN - 1, 1))
        * chartW;

      if (n <= 24) {
        // Bar chart: use raw index for x so
        // the bar reflects the original
        // time, not the cleaned position.
        const barW = Math.max(
          2, (chartW / Math.max(canonicalN, 1)) - 2,
        );
        const barGap = chartW / Math.max(canonicalN, 1);
        for (let i = 0; i < n; i++) {
          const p = r.points[i];
          const x = padL + p.rawIndex * barGap;
          const v = p.value / r.divisor;
          const h = (v / maxVal) * chartH;
          const y = padT + chartH - h;
          const tipText = `${this._escape(p.label || '')}: ${this._isFiniteNumber(v) ? v.toFixed(3) : '—'}`;
          svg += `<rect x="${x}" y="${y}" width="${barW}" height="${h}" fill="${this._escape(r.color)}" rx="1" opacity="0.85">
            <title>${tipText}</title></rect>`;
        }
      } else {
        // Line chart. Each series has its
        // OWN gaps. Use rawIndex for x.
        const gapSet = new Set(r.gaps);
        let path = '';
        let firstInRun = true;
        for (let i = 0; i < n; i++) {
          const p = r.points[i];
          // New run if the previous valid
          // raw was not the immediate
          // predecessor, OR this is the
          // first valid point.
          const prev = i > 0 ? r.points[i - 1].rawIndex : -1;
          const isNewRun =
            firstInRun || p.rawIndex !== prev + 1;
          const x = xForRaw(p.rawIndex);
          const v = p.value / r.divisor;
          const yVal = padT + chartH - (v / maxVal) * chartH;
          path += (isNewRun ? 'M' : 'L') + `${x.toFixed(1)},${yVal.toFixed(1)}`;
          firstInRun = false;
        }
        svg += `<path d="${path}" fill="none" stroke="${this._escape(r.color)}" stroke-width="1.5" opacity="0.9"/>`;
        // Dots: emit one circle per valid
        // point INCLUDING gap-leaders
        // (R07 follow-up: a gap-leader is
        // a valid point that immediately
        // follows a missing sample. The
        // previous implementation skipped
        // it to keep the visual gap, but
        // that also dropped the tooltip.
        // The line ALREADY breaks at the
        // gap (see the path 'M' start
        // above); the dot here only marks
        // the data point, NOT closes the
        // gap). Every valid point gets a
        // tooltip.
        for (let i = 0; i < n; i++) {
          const p = r.points[i];
          const x = xForRaw(p.rawIndex);
          const v = p.value / r.divisor;
          const yVal = padT + chartH - (v / maxVal) * chartH;
          const tipText = `${this._escape(p.label || '')}: ${this._isFiniteNumber(v) ? v.toFixed(3) : '—'}`;
          svg += `<circle cx="${x.toFixed(1)}" cy="${yVal.toFixed(1)}" r="2" fill="${this._escape(r.color)}">
            <title>${tipText}</title></circle>`;
        }
      }
    }

    // Legend
    let legend = '';
    if (resolved.length > 1) {
      let lx = padL;
      for (const r of resolved) {
        legend += `<rect x="${lx}" y="2" width="10" height="6" fill="${this._escape(r.color)}"/>`;
        legend += `<text x="${lx + 13}" y="8" fill="#ccc" font-size="8">${this._escape(r.name)}</text>`;
        lx += r.name.length * 5.5 + 25;
      }
    }

    // Tooltip / legend with actual source, cadence
    // and smoothing. R06 audit requires the card
    // to surface what it is plotting so the
    // operator can interpret it correctly.
    // R06 follow-up: do NOT default
    // ``cfg.cadence`` to "30 min" — that
    // was a fabricated default that did
    // not match the actual data. When the
    // cadence is unknown, surface that
    // explicitly so the operator is not
    // misled.
    const smoothing = cfg.smoothing || 'none';
    const source = cfg.source || (cfg.entity || 'unknown');
    const cadenceText = cfg.cadence
      ? `Cadence: ${this._escape(cfg.cadence)}`
      : 'Cadence: unknown (not in config)';
    const metaLine = `Source: ${this._escape(source)} · ${cadenceText} · Smoothing: ${this._escape(smoothing)}`;

    const unit = cfg.unit || '';
    this.innerHTML = `<ha-card>
      <div style="padding:12px 16px 4px;font-size:14px;font-weight:500">${this._escape(title)}</div>
      <div style="padding:0 16px 4px;font-size:10px;color:#666">${metaLine}</div>
      <div style="padding:0 8px 8px">
        <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">
          ${svg}${legend}
          <text x="${padL + chartW / 2}" y="${H}" text-anchor="middle" fill="#666" font-size="8">${this._escape(unit)}</text>
        </svg>
      </div>
    </ha-card>`;
  }
}

if (!customElements.get('power-history-card')) {
  customElements.define('power-history-card', PowerHistoryCard);
}
window.customCards = window.customCards || [];
window.customCards.push({ type: 'power-history-card', name: 'Power History Card', description: 'Multi-series chart from entity attributes' });
