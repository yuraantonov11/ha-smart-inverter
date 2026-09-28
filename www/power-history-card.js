// power-history-card.js v2.0 — multi-series support
class PowerHistoryCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity && !config.series) throw new Error('entity or series is required');
    this._config = config;
  }
  set hass(hass) {
    this._hass = hass;
    this._render();
  }
  getCardSize() { return 4; }

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
    let allLabels = null;
    for (const s of seriesList) {
      const values = this._getAttr(s.entity, s.attribute);
      const labels = this._getAttr(s.entity, s.labels_attribute);
      if (values) {
        resolved.push({ values, color: s.color || '#f5b06a', name: s.name || '', divisor: s.unit_divisor || 1 });
        if (!allLabels && labels) allLabels = labels;
      }
    }

    if (resolved.length === 0 || !resolved[0].values.length) {
      this.innerHTML = `<ha-card><div style="padding:16px;color:#999">${title}: no data</div></ha-card>`;
      return;
    }

    const data = resolved[0].values;
    const lbls = allLabels || data.map((_, i) => String(i));
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
      svg += `<text x="${padL - 4}" y="${y + 3}" text-anchor="end" fill="#999" font-size="9">${val < 1 ? val.toFixed(2) : val.toFixed(1)}</text>`;
    }

    // X labels
    const step = Math.max(1, Math.floor(data.length / 8));
    for (let i = 0; i < data.length; i += step) {
      const x = padL + (i / (data.length - 1 || 1)) * chartW;
      svg += `<text x="${x}" y="${H - 4}" text-anchor="middle" fill="#999" font-size="8">${lbls[i] || ''}</text>`;
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
          svg += `<rect x="${x}" y="${y}" width="${barW}" height="${h}" fill="${r.color}" rx="1" opacity="0.85">
            <title>${lbls[i]}: ${vals[i].toFixed(3)}</title></rect>`;
        }
      } else {
        // Line chart for many points
        let path = '';
        for (let i = 0; i < n; i++) {
          const x = padL + i * gap;
          const y = padT + chartH - (vals[i] / maxVal) * chartH;
          path += (i === 0 ? 'M' : 'L') + `${x.toFixed(1)},${y.toFixed(1)}`;
        }
        svg += `<path d="${path}" fill="none" stroke="${r.color}" stroke-width="1.5" opacity="0.9"/>`;
        // Dots
        for (let i = 0; i < n; i++) {
          const x = padL + i * gap;
          const y = padT + chartH - (vals[i] / maxVal) * chartH;
          svg += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2" fill="${r.color}">
            <title>${lbls[i]}: ${vals[i].toFixed(3)}</title></circle>`;
        }
      }
    }

    // Legend
    let legend = '';
    if (resolved.length > 1) {
      let lx = padL;
      for (const r of resolved) {
        legend += `<rect x="${lx}" y="2" width="10" height="6" fill="${r.color}"/>`;
        legend += `<text x="${lx + 13}" y="8" fill="#ccc" font-size="8">${r.name}</text>`;
        lx += r.name.length * 5.5 + 25;
      }
    }

    const unit = cfg.unit || '';
    this.innerHTML = `<ha-card>
      <div style="padding:12px 16px 4px;font-size:14px;font-weight:500">${title}</div>
      <div style="padding:0 8px 8px">
        <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">
          ${svg}${legend}
          <text x="${padL + chartW / 2}" y="${H}" text-anchor="middle" fill="#666" font-size="8">${unit}</text>
        </svg>
      </div>
    </ha-card>`;
  }
}

customElements.define('power-history-card', PowerHistoryCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: 'power-history-card', name: 'Power History Card', description: 'Multi-series chart from entity attributes' });
