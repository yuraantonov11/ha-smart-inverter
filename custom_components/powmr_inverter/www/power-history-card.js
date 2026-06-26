// power-history-card.js v1.0
// Renders chart data from entity attribute arrays (e.g., hourly_power_kw, daily_energy_kwh)
class PowerHistoryCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) throw new Error('entity is required');
    this._config = config;
  }
  set hass(hass) {
    this._hass = hass;
    this._render();
  }
  getCardSize() { return 4; }

  _render() {
    if (!this._hass || !this._config) return;
    const eid = this._config.entity;
    const stateObj = this._hass.states[eid];
    if (!stateObj) {
      this.innerHTML = `<ha-card><div style="padding:16px;color:#999">Entity not found: ${eid}</div></ha-card>`;
      return;
    }

    const attrKey = this._config.attribute || 'hourly_power_kw';
    const labelKey = this._config.labels_attribute || 'hourly_labels';
    const title = this._config.title || stateObj.attributes.friendly_name || eid;
    const unit = this._config.unit || stateObj.attributes.unit_of_measurement || '';
    const chartType = this._config.chart_type || 'bar';
    const barColor = this._config.bar_color || '#f5b06a';
    const maxBars = this._config.max_bars || 100;

    // Try to find attribute data — could be in top-level or nested
    let values = stateObj.attributes[attrKey];
    let labels = stateObj.attributes[labelKey];

    if (!values || !Array.isArray(values)) {
      // Try common fallbacks
      for (const key of ['hourly_power_kw', 'daily_energy_kwh', 'monthly_energy_kwh', 'timePoints', 'values']) {
        if (Array.isArray(stateObj.attributes[key])) {
          values = stateObj.attributes[key];
          break;
        }
      }
    }

    if (!values || !Array.isArray(values) || values.length === 0) {
      this.innerHTML = `<ha-card><div style="padding:16px;color:#999">
        <div style="font-size:14px;font-weight:500;margin-bottom:8px">${title}</div>
        No chart data in attribute "${attrKey}"<br>
        Available: ${Object.keys(stateObj.attributes).join(', ')}
      </div></ha-card>`;
      return;
    }

    const data = values.slice(0, maxBars);
    const lbls = (labels && Array.isArray(labels)) ? labels.slice(0, maxBars) : data.map((_, i) => String(i));
    const maxVal = Math.max(...data, 0.01);

    // Build SVG bar chart
    const W = 400, H = 160, padL = 40, padR = 10, padT = 10, padB = 28;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;
    const barW = Math.max(1, (chartW / data.length) - 1);
    const gap = chartW / data.length;

    let bars = '';
    data.forEach((v, i) => {
      const x = padL + i * gap;
      const h = (v / maxVal) * chartH;
      const y = padT + chartH - h;
      bars += `<rect x="${x}" y="${y}" width="${Math.max(barW, 1)}" height="${h}" fill="${barColor}" rx="1">
        <title>${lbls[i]}: ${typeof v === 'number' ? v.toFixed(3) : v} ${unit}</title>
      </rect>`;
    });

    // Y-axis labels
    let yLabels = '';
    for (let i = 0; i <= 4; i++) {
      const val = (maxVal * i / 4);
      const y = padT + chartH - (chartH * i / 4);
      yLabels += `<text x="${padL - 4}" y="${y + 3}" text-anchor="end" fill="#999" font-size="9">${val < 1 ? val.toFixed(2) : val.toFixed(1)}</text>`;
    }

    // X-axis labels (show every Nth)
    let xLabels = '';
    const step = Math.max(1, Math.floor(data.length / 6));
    for (let i = 0; i < data.length; i += step) {
      const x = padL + i * gap + barW / 2;
      xLabels += `<text x="${x}" y="${H - 4}" text-anchor="middle" fill="#999" font-size="8">${lbls[i]}</text>`;
    }

    // Grid lines
    let grid = '';
    for (let i = 0; i <= 4; i++) {
      const y = padT + chartH - (chartH * i / 4);
      grid += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="#333" stroke-width="0.5"/>`;
    }

    this.innerHTML = `
      <ha-card>
        <div style="padding:12px 16px 4px;font-size:14px;font-weight:500">${title}</div>
        <div style="padding:0 8px 8px">
          <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">
            ${grid}
            ${bars}
            ${yLabels}
            ${xLabels}
            <text x="${padL + chartW / 2}" y="${H}" text-anchor="middle" fill="#666" font-size="8">${unit}</text>
          </svg>
        </div>
      </ha-card>`;
  }
}

customElements.define('power-history-card', PowerHistoryCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'power-history-card',
  name: 'Power History Card',
  description: 'Renders chart data from entity attribute arrays',
});
