/**
 * Battery Storage Manager – Status Card
 *
 * A compact overview card showing battery SOC, current price,
 * operating mode, and runtime toggles. Ships with the integration.
 */

const STATUS_CARD_VERSION = "1.0.1";

const MODE_ICONS = {
  idle:        { icon: "mdi:sleep",                color: "#9E9E9E", label: "Inaktiv" },
  charging:    { icon: "mdi:battery-charging-100", color: "#4CAF50", label: "Laden" },
  discharging: { icon: "mdi:battery-arrow-down",   color: "#FF9800", label: "Entladen" },
};

class BatteryStatusCard extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;

    const entityId = this._config.entity;
    if (!entityId) {
      this._renderPlaceholder();
      return;
    }

    // Only re-render when relevant data actually changed
    const stateObj = hass.states[entityId];
    if (!stateObj) {
      this._lastStateKey = null;
      this._render();
      return;
    }
    const attrs = stateObj.attributes;
    const key = [
      stateObj.state,
      attrs.battery_soc,
      attrs.current_price,
      attrs.grid_power,
      attrs.strategy,
      attrs.inverter_actual_power,
      attrs.planned_action,
    ].join("|");
    if (key === this._lastStateKey) return;
    this._lastStateKey = key;
    this._render();
  }

  setConfig(config) {
    this._config = config;
  }

  static getConfigElement() {
    return document.createElement("battery-status-card-editor");
  }

  static getStubConfig() {
    return { entity: "" };
  }

  getCardSize() {
    return 3;
  }

  _render() {
    if (!this._hass || !this._config) return;

    const entityId = this._config.entity;
    const stateObj = this._hass.states[entityId];
    if (!stateObj) {
      this._renderError(`Entity nicht gefunden: ${entityId}`);
      return;
    }

    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }

    const attrs = stateObj.attributes;
    const soc = attrs.battery_soc;
    const price = attrs.current_price;
    const mode = attrs.operating_mode || "idle";
    const strategy = attrs.strategy || "manual";
    const gridPower = attrs.grid_power;
    const plannedAction = attrs.planned_action;
    const inverterActualPower = attrs.inverter_actual_power;
    const inverterTargetPower = attrs.inverter_target_power || 0;

    const modeCfg = MODE_ICONS[mode] || MODE_ICONS.idle;

    // SOC ring calculation
    const socPct = soc != null ? Math.max(0, Math.min(100, soc)) : 0;
    const socColor = socPct > 60 ? "#4CAF50" : socPct > 30 ? "#FF9800" : "#f44336";
    const circumference = 2 * Math.PI * 40;
    const offset = circumference - (socPct / 100) * circumference;

    // Price display
    const priceCt = price != null ? (price * 100).toFixed(1) : "–";

    // Grid power display
    let gridText = "–";
    let gridColor = "var(--bsm-text2)";
    if (gridPower != null) {
      if (gridPower > 10) {
        gridText = `${Math.round(gridPower)} W`;
        gridColor = "#f44336";
      } else if (gridPower < -10) {
        gridText = `${Math.round(Math.abs(gridPower))} W`;
        gridColor = "#4CAF50";
      } else {
        gridText = "~0 W";
      }
    }

    // Strategy label
    const stratLabels = {
      price_optimized: "Preisoptimiert",
      self_consumption: "Eigenverbrauch",
      manual: "Manuell",
    };

    // Toggle entities
    const toggles = this._config.toggle_entities || [];

    const title = this._config.title || "Batteriespeicher";

    this.shadowRoot.innerHTML = `
      <ha-card>
        <style>${this._getStyles()}</style>
        <div class="card-content">
          <div class="header-row">
            <span class="card-title">${title}</span>
            <span class="strategy-badge">${stratLabels[strategy] || strategy}</span>
          </div>

          <div class="main-row">
            <!-- SOC Ring -->
            <div class="soc-ring">
              <svg viewBox="0 0 100 100">
                <circle cx="50" cy="50" r="40" fill="none"
                        stroke="var(--bsm-border)" stroke-width="6" />
                <circle cx="50" cy="50" r="40" fill="none"
                        stroke="${socColor}" stroke-width="6"
                        stroke-dasharray="${circumference}"
                        stroke-dashoffset="${offset}"
                        stroke-linecap="round"
                        transform="rotate(-90 50 50)" />
              </svg>
              <div class="soc-text">
                <span class="soc-value">${soc != null ? Math.round(soc) : "–"}</span>
                <span class="soc-unit">%</span>
              </div>
            </div>

            <!-- Stats -->
            <div class="stats">
              <div class="stat-row">
                <ha-icon icon="mdi:currency-eur" style="color:var(--bsm-text2)"></ha-icon>
                <span class="stat-label">Preis</span>
                <span class="stat-value">${priceCt} ct/kWh</span>
              </div>
              <div class="stat-row">
                <ha-icon icon="${modeCfg.icon}" style="color:${modeCfg.color}"></ha-icon>
                <span class="stat-label">Modus</span>
                <span class="stat-value" style="color:${modeCfg.color}">${modeCfg.label}</span>
              </div>
              <div class="stat-row">
                <ha-icon icon="mdi:transmission-tower" style="color:${gridColor}"></ha-icon>
                <span class="stat-label">Netz</span>
                <span class="stat-value" style="color:${gridColor}">
                  ${gridPower != null && gridPower > 10 ? "Bezug " : gridPower != null && gridPower < -10 ? "Einsp. " : ""}${gridText}
                </span>
              </div>
              ${inverterActualPower != null || inverterTargetPower > 0 ? `
              <div class="stat-row">
                <ha-icon icon="mdi:solar-power" style="color:#FF9800"></ha-icon>
                <span class="stat-label">Wechselr.</span>
                <span class="stat-value">${inverterActualPower != null ? Math.round(inverterActualPower) + " W" : Math.round(inverterTargetPower) + " W (Soll)"}</span>
              </div>
              ` : ""}
            </div>
          </div>

          ${toggles.length > 0 ? this._renderToggles(toggles) : ""}
        </div>
      </ha-card>
    `;

    // Bind toggle events
    this.shadowRoot.querySelectorAll(".toggle-switch").forEach(el => {
      el.addEventListener("click", () => {
        const eid = el.dataset.entity;
        const state = this._hass.states[eid];
        if (state) {
          this._hass.callService("switch", state.state === "on" ? "turn_off" : "turn_on", {
            entity_id: eid,
          });
        }
      });
    });
  }

  _renderToggles(toggles) {
    let html = '<div class="toggles">';
    toggles.forEach(eid => {
      const state = this._hass.states[eid];
      if (!state) return;
      const isOn = state.state === "on";
      const name = state.attributes.friendly_name || eid;
      // Shorten: remove "Battery Storage Manager " prefix
      const shortName = name.replace(/^Battery Storage Manager\s*/i, "");
      html += `
        <div class="toggle-item">
          <span class="toggle-label">${shortName}</span>
          <div class="toggle-switch ${isOn ? "on" : ""}" data-entity="${eid}">
            <div class="toggle-track">
              <div class="toggle-thumb"></div>
            </div>
          </div>
        </div>
      `;
    });
    html += "</div>";
    return html;
  }

  _renderPlaceholder() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <ha-card header="Battery Status">
        <div style="padding: 16px; color: var(--secondary-text-color, #888);">
          Bitte Entity konfigurieren (Betriebsmodus-Sensor)
        </div>
      </ha-card>
    `;
  }

  _renderError(msg) {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <ha-card>
        <div style="padding: 16px; color: var(--error-color, #db4437);">
          ${msg}
        </div>
      </ha-card>
    `;
  }

  _getStyles() {
    return `
      :host {
        --bsm-text: var(--primary-text-color, #e1e1e1);
        --bsm-text2: var(--secondary-text-color, #aaa);
        --bsm-border: var(--divider-color, #333);
      }
      .card-content {
        padding: 16px;
      }
      .header-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 12px;
      }
      .card-title {
        font-size: 16px;
        font-weight: 500;
        color: var(--bsm-text);
      }
      .strategy-badge {
        font-size: 11px;
        background: var(--bsm-border);
        color: var(--bsm-text2);
        padding: 3px 10px;
        border-radius: 12px;
      }
      .main-row {
        display: flex;
        align-items: center;
        gap: 20px;
      }
      .soc-ring {
        position: relative;
        width: 100px;
        height: 100px;
        flex-shrink: 0;
      }
      .soc-ring svg {
        width: 100%;
        height: 100%;
      }
      .soc-text {
        position: absolute;
        top: 50%; left: 50%;
        transform: translate(-50%, -50%);
        text-align: center;
      }
      .soc-value {
        font-size: 28px;
        font-weight: 600;
        color: var(--bsm-text);
        line-height: 1;
      }
      .soc-unit {
        font-size: 14px;
        color: var(--bsm-text2);
      }
      .stats {
        flex: 1;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .stat-row {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .stat-row ha-icon {
        --mdc-icon-size: 18px;
        flex-shrink: 0;
      }
      .stat-label {
        color: var(--bsm-text2);
        font-size: 12px;
        min-width: 50px;
      }
      .stat-value {
        font-size: 13px;
        font-weight: 500;
        color: var(--bsm-text);
        margin-left: auto;
      }

      /* Toggles */
      .toggles {
        margin-top: 14px;
        padding-top: 12px;
        border-top: 1px solid var(--bsm-border);
        display: flex;
        flex-wrap: wrap;
        gap: 8px 16px;
      }
      .toggle-item {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .toggle-label {
        font-size: 12px;
        color: var(--bsm-text2);
      }
      .toggle-switch {
        cursor: pointer;
      }
      .toggle-track {
        width: 36px;
        height: 20px;
        border-radius: 10px;
        background: var(--bsm-border);
        position: relative;
        transition: background 0.2s;
      }
      .toggle-switch.on .toggle-track {
        background: var(--primary-color, #03a9f4);
      }
      .toggle-thumb {
        width: 16px;
        height: 16px;
        border-radius: 50%;
        background: #fff;
        position: absolute;
        top: 2px;
        left: 2px;
        transition: left 0.2s;
      }
      .toggle-switch.on .toggle-thumb {
        left: 18px;
      }
    `;
  }
}

/* ── Card Editor ───────────────────────────────────────── */
class BatteryStatusCardEditor extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
  }

  setConfig(config) {
    this._config = config;
    this._render();
  }

  _render() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        .row { display: flex; align-items: center; padding: 8px 0; }
        .row label { flex: 1; }
        .row input { flex: 2; }
      </style>
      <div class="row">
        <label>Entity (Betriebsmodus-Sensor)</label>
        <input id="entity" value="${this._config.entity || ""}">
      </div>
      <div class="row">
        <label>Titel</label>
        <input id="title" value="${this._config.title || ""}">
      </div>
    `;
    this.shadowRoot.getElementById("entity").addEventListener("change", (e) => {
      this._fire({ ...this._config, entity: e.target.value });
    });
    this.shadowRoot.getElementById("title").addEventListener("change", (e) => {
      this._fire({ ...this._config, title: e.target.value });
    });
  }

  _fire(config) {
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config } }));
  }
}

/* ── Registration ──────────────────────────────────────── */
try {
  if (!customElements.get("battery-status-card")) {
    customElements.define("battery-status-card", BatteryStatusCard);
  }
  if (!customElements.get("battery-status-card-editor")) {
    customElements.define("battery-status-card-editor", BatteryStatusCardEditor);
  }
} catch (e) {
  console.warn("battery-status-card: registration failed", e);
}

window.customCards = window.customCards || [];
if (!window.customCards.find(c => c.type === "battery-status-card")) {
  window.customCards.push({
    type: "battery-status-card",
    name: "Battery Storage Status",
    description: "Compact overview of battery SOC, price, mode, and runtime toggles.",
    preview: false,
    documentationURL: "https://github.com/dEeds83/ha-battery-storage-manager",
  });
}

console.info(
  `%c BATTERY-STATUS-CARD %c v${STATUS_CARD_VERSION} `,
  "background: #2196F3; color: #fff; font-weight: bold; padding: 2px 6px; border-radius: 4px 0 0 4px;",
  "background: #333; color: #fff; padding: 2px 6px; border-radius: 0 4px 4px 0;"
);
