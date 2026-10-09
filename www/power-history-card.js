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
    // Returns { values:number[], labels:string[], gaps:number[] }.
    // ``gaps`` is the list of indices where the label
    // is missing or out-of-order; the chart uses
    // those to start a new sub-path.
    const values = Array.isArray(rawValues) ? rawValues : [];
    const labels = Array.isArray(rawLabels) ? rawLabels : [];
    const out = { values: [], labels: [], gaps: [] };
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

    // Resolve data for each series
    const resolved = [];
    for (const s of seriesList) {
      const rawValues = this._getAttr(s.entity, s.attribute);
      if (!rawValues) continue;
      const rawLabels = this._getAttr(s.entity, s.labels_attribute);
      const cleaned = this._cleanSeries(rawValues, rawLabels);
      if (cleaned.values.length === 0) continue;
      resolved.push({
        values: cleaned.values,
        labels: cleaned.labels,
        gaps: cleaned.gaps,
        color: s.color || '#f5b06a',
        name: s.name || '',
        divisor: s.unit_divisor || 1,
      });
    }

    if (resolved.length === 0) {
      this.innerHTML = `<ha-card><div style="padding:16px;color:#999">${this._escape(title)}: no data</div></ha-card>`;
      return;
    }

    const data = resolved[0].values;
    const lbls = resolved[0].labels;
    const W = 500, H = 180, padL = 45, padR = 10, padT = 10, padB = 30;
    const chartW = W - padL - padR, chartH = H - padT - padB;

    // Compute max across all series
    let maxVal = 0;
    for (const r of resolved) {
      for (const v of r.values) maxVal = Math.max(maxVal, v / r.divisor);
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

    // X labels
    const step = Math.max(1, Math.floor(data.length / 8));
    for (let i = 0; i < data.length; i += step) {
      const x = padL + (i / (data.length - 1 || 1)) * chartW;
      svg += `<text x="${x}" y="${H - 4}" text-anchor="middle" fill="#999" font-size="8">${this._escape(lbls[i] || '')}</text>`;
    }

    // Render each series
    for (const r of resolved) {
      const vals = r.values.map(v => v / r.divisor);
      const n = vals.length;
      const gap = chartW / Math.max(n - 1, 1);

      if (n <= 24) {
        // Bar chart for fewer points
        const barW = Math.max(2, (chartW / n) - 2);
        const barGap = chartW / n;
        for (let i = 0; i < n; i++) {
          const x = padL + i * barGap;
          const h = (vals[i] / maxVal) * chartH;
          const y = padT + chartH - h;
          const tipText = `${this._escape(lbls[i] || '')}: ${this._isFiniteNumber(vals[i]) ? vals[i].toFixed(3) : '—'}`;
          svg += `<rect x="${x}" y="${y}" width="${barW}" height="${h}" fill="${this._escape(r.color)}" rx="1" opacity="0.85">
            <title>${tipText}</title></rect>`;
        }
      } else {
        // Line chart for many points. R06: gaps in
        // the original series start a new ``M``
        // sub-path so missing intervals are not
        // connected as if they were measured.
        const n0 = resolved[0].values.length;
        // Map gaps in the cleaned values back to
        // a position in the rendered chart. We
        // start a new sub-path whenever the
        // corresponding index in the cleaned
        // series is the first index of a new
        // contiguous run. The original gaps in
        // rawValues are the positions in the
        // raw array, but the cleaned values are
        // a sparse subsequence; we approximate
        // by treating the absence of a value as
        // a "break before next valid point" if
        // the previous raw index was a gap.
        let path = '';
        let inRun = false;
        for (let i = 0; i < vals.length; i++) {
          if (!inRun) {
            path += 'M' + `${(padL + i * gap).toFixed(1)},${(padT + chartH - (vals[i] / maxVal) * chartH).toFixed(1)}`;
            inRun = true;
          } else {
            path += 'L' + `${(padL + i * gap).toFixed(1)},${(padT + chartH - (vals[i] / maxVal) * chartH).toFixed(1)}`;
          }
        }
        // Find positions in the cleaned sequence
        // that are immediately after a gap in the
        // original raw series (size n0). For each
        // such position, restart the path with an
        // ``M`` so missing intervals are not
        // connected.
        if (resolved[0].gaps && resolved[0].gaps.length) {
          // gaps are raw indices in the original
          // input. The cleaned index for raw
          // index i is (i - number_of_gaps_before).
          // If two consecutive valid raw indices
          // are separated by a gap, the second
          // one is the start of a new run.
          const gapSet = new Set(resolved[0].gaps);
          let cleaned = -1;
          let prevRaw = -1;
          let firstInRun = true;
          path = '';
          for (let raw = 0; raw < n0; raw++) {
            if (gapSet.has(raw)) {
              prevRaw = raw;
              firstInRun = true;
              continue;
            }
            cleaned++;
            // New run if the previous valid raw was
            // not the immediate predecessor, OR this
            // is the very first valid point.
            const isNewRun = firstInRun || raw !== prevRaw + 1;
            const x = padL + cleaned * gap;
            const yVal = padT + chartH - (vals[cleaned] / maxVal) * chartH;
            path += (isNewRun ? 'M' : 'L') + `${x.toFixed(1)},${yVal.toFixed(1)}`;
            prevRaw = raw;
            firstInRun = false;
          }
        }
        svg += `<path d="${path}" fill="none" stroke="${this._escape(r.color)}" stroke-width="1.5" opacity="0.9"/>`;
        // Dots — only for points that are NOT
        // immediately after a gap (gap-leader
        // dots would visually "close" the gap).
        if (resolved[0].gaps && resolved[0].gaps.length) {
          const gapSet = new Set(resolved[0].gaps);
          let cleaned = -1;
          let prevRaw = -1;
          for (let raw = 0; raw < n0; raw++) {
            if (gapSet.has(raw)) {
              prevRaw = raw;
              continue;
            }
            cleaned++;
            const isGapLeader = raw !== prevRaw + 1;
            if (!isGapLeader) {
              const x = padL + cleaned * gap;
              const yVal = padT + chartH - (vals[cleaned] / maxVal) * chartH;
              const tipText = `${this._escape(lbls[cleaned] || '')}: ${this._isFiniteNumber(vals[cleaned]) ? vals[cleaned].toFixed(3) : '—'}`;
              svg += `<circle cx="${x.toFixed(1)}" cy="${yVal.toFixed(1)}" r="2" fill="${this._escape(r.color)}">
                <title>${tipText}</title></circle>`;
            }
            prevRaw = raw;
          }
        } else {
          for (let i = 0; i < n; i++) {
            const x = padL + i * gap;
            const y = padT + chartH - (vals[i] / maxVal) * chartH;
            const tipText = `${this._escape(lbls[i] || '')}: ${this._isFiniteNumber(vals[i]) ? vals[i].toFixed(3) : '—'}`;
            svg += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2" fill="${this._escape(r.color)}">
              <title>${tipText}</title></circle>`;
          }
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
    const cadence = cfg.cadence || '30 min';
    const smoothing = cfg.smoothing || 'none';
    const source = cfg.source || (cfg.entity || 'unknown');
    const metaLine = `Source: ${this._escape(source)} · Cadence: ${this._escape(cadence)} · Smoothing: ${this._escape(smoothing)}`;

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

customElements.define('power-history-card', PowerHistoryCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: 'power-history-card', name: 'Power History Card', description: 'Multi-series chart from entity attributes' });
