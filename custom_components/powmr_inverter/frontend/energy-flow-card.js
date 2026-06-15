/**
 * Smart Solar Energy Flow Card — bundled with powmr_inverter integration.
 * No external dependencies. Renders an animated energy flow diagram.
 */

class SmartSolarEnergyFlow extends HTMLElement {
  set hass(hass) {
    if (!this._initialized) {
      this._init();
    }
    this._hass = hass;
    this._render();
  }

  setConfig(config) {
    this._config = config;
  }

  _init() {
    this._initialized = true;
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; padding: 16px; background: var(--card-background-color, #fff); border-radius: 12px; }
        .flow { display: flex; align-items: center; justify-content: center; gap: 8px; flex-wrap: wrap; }
        .node { display: flex; flex-direction: column; align-items: center; padding: 12px 16px; border-radius: 10px; min-width: 90px; }
        .node .value { font-size: 1.6em; font-weight: bold; }
        .node .label { font-size: 0.75em; opacity: 0.7; margin-top: 2px; }
        .flow-line { font-size: 1.4em; opacity: 0.4; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%,100%{ opacity:0.3; } 50%{ opacity:0.9; } }
        .solar  { color: #f59e0b; background: #fef3c7; }
        .home   { color: #3b82f6; background: #dbeafe; }
        .grid   { color: #6b7280; background: #f3f4f6; }
        .battery{ color: #10b981; background: #d1fae5; }
        @media (prefers-color-scheme: dark) {
          .solar  { background: #78350f; }
          .home   { background: #1e3a5f; }
          .grid   { background: #374151; }
          .battery{ background: #064e3b; }
        }
      </style>
      <div class="flow" id="flow"></div>
    `;
  }

  _render() {
    const flow = this.shadowRoot.getElementById("flow");
    if (!flow || !this._hass || !this._config) return;

    const { solar, home, grid, battery } = this._config.entities || {};
    const s = (entityId) => {
      const st = this._hass.states[entityId];
      if (!st) return { v: "—", u: "" };
      const u = st.attributes.unit_of_measurement || "";
      return { v: Number(st.state).toFixed(0), u };
    };
    const u = (entityId) => (s(entityId).u || "W");

    flow.innerHTML = `
      <div class="node solar">${this._icon("mdi:solar-power")}<span class="value">${s(solar).v}</span><span class="label">PV ${u(solar)}</span></div>
      <span class="flow-line">⟶</span>
      <div class="node home">${this._icon("mdi:home-lightning-bolt")}<span class="value">${s(home).v}</span><span class="label">Дім ${u(home)}</span></div>
      <span class="flow-line">⟷</span>
      <div class="node grid">${this._icon("mdi:transmission-tower")}<span class="value">${s(grid).v}</span><span class="label">Мережа ${u(grid)}</span></div>
      <span class="flow-line">⟷</span>
      <div class="node battery">${this._icon("mdi:battery-charging")}<span class="value">${s(battery).v}</span><span class="label">АКБ ${u(battery)}</span></div>
    `;
  }

  _icon(icon) {
    return `<ha-icon icon="${icon}" style="--mdc-icon-size:28px;margin-bottom:4px;"></ha-icon>`;
  }

  static getConfigElement() { return document.createElement("div"); }
  static getStubConfig() {
    return {
      entities: { solar: "", home: "", grid: "", battery: "" }
    };
  }
}

customElements.define("smart-solar-energy-flow", SmartSolarEnergyFlow);
