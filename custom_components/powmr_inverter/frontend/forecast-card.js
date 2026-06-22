// forecast-card.js — Smooth PV forecast sparkline for powmr_inverter
// Reads hourly forecast from sensor attributes and renders smooth SVG curve.
// Version: 1.0.0

class ForecastCard extends HTMLElement {
  constructor() {
    super();
    this._config = {};
    this._hass = null;
  }

  setConfig(config) {
    this._config = { ...config };
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() { return 2; }

  _render() {
    if (!this._hass || !this._config) return;

    const entity = this._config.entity || 'sensor.smart_solar_inverter_forecast_tomorrow';
    const state = this._hass.states[entity];
    const title = this._config.title || '☀️ Прогноз генерації (24г)';

    // Get hourly data from attributes
    let hourly = [];
    let totalKwh = 0;
    let peakW = 0;
    if (state && state.attributes) {
      hourly = state.attributes.hourly_forecast_w || [];
      totalKwh = state.attributes.total_kwh || 0;
      peakW = state.attributes.peak_power_w || 0;
    }

    // Current hour marker
    const now = new Date();
    const currentHour = now.getHours();

    // Build smooth SVG sparkline
    const W = 300, H = 60, PAD = 2;
    const maxVal = Math.max(...hourly, 1);
    const step = (W - PAD * 2) / Math.max(hourly.length - 1, 1);

    // Map data points
    const points = hourly.map((v, i) => ({
      x: PAD + i * step,
      y: H - PAD - (v / maxVal) * (H - PAD * 2),
    }));

    // Catmull-Rom to cubic bezier for smooth curves
    const smoothPath = (pts) => {
      if (pts.length < 2) return '';
      if (pts.length === 2) return `M${pts[0].x},${pts[0].y} L${pts[1].x},${pts[1].y}`;

      let d = `M${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)}`;
      for (let i = 0; i < pts.length - 1; i++) {
        const p0 = pts[Math.max(0, i - 1)];
        const p1 = pts[i];
        const p2 = pts[i + 1];
        const p3 = pts[Math.min(pts.length - 1, i + 2)];

        const cp1x = p1.x + (p2.x - p0.x) / 6;
        const cp1y = p1.y + (p2.y - p0.y) / 6;
        const cp2x = p2.x - (p3.x - p1.x) / 6;
        const cp2y = p2.y - (p3.y - p1.y) / 6;

        d += ` C${cp1x.toFixed(1)},${cp1y.toFixed(1)} ${cp2x.toFixed(1)},${cp2y.toFixed(1)} ${p2.x.toFixed(1)},${p2.y.toFixed(1)}`;
      }
      return d;
    };

    const linePath = smoothPath(points);

    // Fill path (close to bottom)
    const fillPath = points.length > 0
      ? linePath + ` L${points[points.length - 1].x.toFixed(1)},${H} L${points[0].x.toFixed(1)},${H} Z`
      : '';

    // Current hour vertical line position
    const curX = PAD + currentHour * step;

    // Hour labels (every 6h)
    const hourLabels = [0, 6, 12, 18].map(h => {
      const x = PAD + h * step;
      return `<text x="${x.toFixed(1)}" y="${H - 1}" text-anchor="middle" font-size="7" fill="#8b949e">${h}:00</text>`;
    }).join('');

    // Grid lines
    const gridLines = [0, 6, 12, 18].map(h => {
      const x = PAD + h * step;
      return `<line x1="${x.toFixed(1)}" y1="0" x2="${x.toFixed(1)}" y2="${H - 8}" stroke="#21262d" stroke-width="0.5"/>`;
    }).join('');

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .card {
          background: #0d1117;
          border: 1px solid #21262d;
          border-radius: 12px;
          padding: 12px 14px;
          font-family: 'Segoe UI', Arial, sans-serif;
        }
        .title {
          font-size: 0.72rem;
          font-weight: 700;
          letter-spacing: 1px;
          text-transform: uppercase;
          color: #8b949e;
          margin-bottom: 8px;
          display: flex;
          align-items: center;
          gap: 7px;
        }
        .title::after { content: ''; flex: 1; height: 1px; background: #21262d; }
        .stats { display: flex; gap: 12px; margin-top: 6px; font-size: 0.68rem; color: #8b949e; }
        .stats span { font-weight: 600; }
        .stats .val { color: #f4d03f; }
        svg { display: block; width: 100%; }
      </style>
      <div class="card">
        <div class="title">${title}</div>
        ${hourly.length > 0 ? `
          <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
            ${gridLines}
            <defs>
              <linearGradient id="fcGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="rgba(255,200,50,0.35)"/>
                <stop offset="100%" stop-color="rgba(255,200,50,0)"/>
              </linearGradient>
            </defs>
            <path d="${fillPath}" fill="url(#fcGrad)"/>
            <path d="${linePath}" fill="none" stroke="#f4d03f" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            <line x1="${curX.toFixed(1)}" y1="0" x2="${curX.toFixed(1)}" y2="${H - 8}" stroke="rgba(255,255,255,0.25)" stroke-width="0.8" stroke-dasharray="2,2"/>
            ${hourLabels}
          </svg>
          <div class="stats">
            <div>Пікова: <span class="val">${peakW} W</span></div>
            <div>Добова: <span class="val">${totalKwh} kWh</span></div>
          </div>
        ` : `<div style="text-align:center;color:#8b949e;padding:20px;font-size:0.75rem;">Завантаження прогнозу...</div>`}
      </div>
    `;
  }
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'forecast-card',
  name: 'PV Forecast Sparkline',
  description: 'Smooth 24-hour PV generation forecast with Catmull-Rom spline interpolation.',
  preview: true,
  version: '1.0.0',
});
customElements.define('forecast-card', ForecastCard);
