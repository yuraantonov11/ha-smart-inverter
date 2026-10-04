// total-energy-card.js v1.0 — informative total + today/year breakdown.
// Companion to the powmr_inverter Energy Flow overview.
// Reads the inverter's lifetime + today + 30-day aggregate sensors
// (created and registered by the powmr_inverter integration) and
// renders a compact "total" card with two chip rows.
//
// Card contract:
//   setConfig({ entity, title?, today_attribute?, year_attribute? })
//     - entity: a sensor whose state is the lifetime total
//     - today_attribute: attribute name for today's kWh
//     - year_attribute: attribute name for the current year kWh
//   set hass(hass)
//   getCardSize() — returns 3 rows.
class TotalEnergyCard extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    this._render();
  }
  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error('entity is required');
    }
    this._config = Object.assign({}, config);
  }
  getCardSize() {
    return 3;
  }

  _attr(entityId, key) {
    if (!this._hass || !entityId || !key) return null;
    const s = this._hass.states[entityId];
    if (!s) return null;
    const v = s.attributes[key];
    if (typeof v === 'number' && Number.isFinite(v)) return v;
    return null;
  }

  _fmtKwh(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—';
    if (v >= 1000) return (v / 1000).toFixed(2) + ' MWh';
    return v.toFixed(2) + ' kWh';
  }

  _render() {
    if (!this._hass || !this._config) return;
    const cfg = this._config;
    const state = this._hass.states[cfg.entity];
    if (!state) {
      this.innerHTML = `
        <ha-card>
          <div class="te-title">${cfg.title || 'Total energy'}</div>
          <div class="te-empty">Sensor ${cfg.entity} unavailable</div>
        </ha-card>
      `;
      return;
    }
    const total = Number(state.state);
    const today = this._attr(cfg.entity, cfg.today_attribute || 'today_kwh');
    const year = this._attr(cfg.entity, cfg.year_attribute || 'year_kwh');
    this.innerHTML = `
      <ha-card>
        <div class="te-title">${cfg.title || 'Total energy'}</div>
        <div class="te-total">${this._fmtKwh(total)}</div>
        <div class="te-row">
          <span class="te-chip">Today: ${this._fmtKwh(today)}</span>
          <span class="te-chip">Year: ${this._fmtKwh(year)}</span>
        </div>
        <style>
          .te-title { font-size: 14px; opacity: 0.7; padding: 8px 12px 0; }
          .te-total { font-size: 28px; font-weight: 600; padding: 4px 12px 8px; }
          .te-row { display: flex; gap: 8px; padding: 0 12px 12px; }
          .te-chip {
            flex: 1; text-align: center;
            background: var(--secondary-background-color, #f5f5f5);
            color: var(--primary-text-color, #222);
            border-radius: 4px; padding: 6px 8px;
            font-size: 12px;
          }
          .te-empty { padding: 12px; opacity: 0.6; }
        </style>
      </ha-card>
    `;
  }
}

customElements.define('total-energy-card', TotalEnergyCard);
window.customCards = window.customCards || [];
if (!window.customCards.some(c => c.type === 'total-energy-card')) {
  window.customCards.push({
    type: 'total-energy-card',
    name: 'Total Energy Card',
    description: 'Lifetime + today + year kWh breakdown',
  });
}
