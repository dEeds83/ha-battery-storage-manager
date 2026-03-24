/**
 * Battery Storage Manager – Plan Overview Card
 *
 * A custom Lovelace card that visualizes the battery plan as a
 * color-coded timeline chart + detail table. Ships with the
 * integration and is auto-registered.
 */

const CARD_VERSION = "1.1.3";

const ACTION_CONFIG = {
  charge:       { color: "#4CAF50", icon: "mdi:battery-charging",     label: "Laden (Netz)", short: "Laden" },
  discharge:    { color: "#FF9800", icon: "mdi:battery-arrow-down",   label: "Entladen",     short: "Entl." },
  solar_charge: { color: "#FFD600", icon: "mdi:solar-power",          label: "Laden (Solar)", short: "Solar" },
  hold:         { color: "#2196F3", icon: "mdi:lock",                 label: "Halten",        short: "Halt" },
  idle:         { color: "#9E9E9E", icon: "mdi:sleep",                label: "Inaktiv",       short: "Idle" },
};

class BatteryPlanCard extends HTMLElement {
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
      this._lastState = null;
      this._render();
      return;
    }
    const newState = stateObj.state;
    const newPlan = stateObj.attributes.plan;
    const planKey = newPlan ? JSON.stringify(newPlan) : "";
    if (newState === this._lastState && planKey === this._lastPlanKey) return;
    this._lastState = newState;
    this._lastPlanKey = planKey;
    this._render();
  }

  setConfig(config) {
    this._config = config;
    this._showTable = false;
    this._showEpex = false;
  }

  static getConfigElement() {
    return document.createElement("battery-plan-card-editor");
  }

  static getStubConfig() {
    return { entity: "", price_entity: "" };
  }

  getCardSize() {
    return 6;
  }

  _render() {
    if (!this._hass || !this._config) return;

    const entityId = this._config.entity;
    const stateObj = this._hass.states[entityId];
    if (!stateObj) {
      this._renderError(`Entity nicht gefunden: ${entityId}`);
      return;
    }

    const fullPlan = stateObj.attributes.plan;
    if (!fullPlan || !fullPlan.length) {
      this._renderError("Kein Plan verfügbar");
      return;
    }

    // Get EPEX visualization data from price entity (separate from plan)
    const priceForecastEntity = this._config.price_entity || "";
    const priceState = priceForecastEntity ? this._hass.states[priceForecastEntity] : null;
    const extForecast = priceState?.attributes?.extended_forecast || [];
    const epexEntries = extForecast.filter(e => e.source === "epex_predictor");
    const hasEpexSlots = epexEntries.length > 0;
    const epexSlotSet = new Set(epexEntries.map(e => e.time));

    // Build plan: Tibber plan + optional EPEX price-only visualization
    let plan = [...fullPlan];
    if (this._showEpex && epexEntries.length > 0) {
      // Append EPEX entries as visualization-only slots (idle action, price shown)
      for (const e of epexEntries) {
        plan.push({
          hour: e.time,
          price: e.price,
          solar_kwh: 0,
          expected_soc: null,
          action: "idle",
          reason: "EPEX Predictor (Prognose)",
        });
      }
    }

    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }

    const now = new Date();
    // Round down to nearest slot boundary for current-slot matching
    const roundedMin = Math.floor(now.getMinutes() / 15) * 15;
    const nowSlot = now.getFullYear() + "-"
      + String(now.getMonth() + 1).padStart(2, "0") + "-"
      + String(now.getDate()).padStart(2, "0") + "T"
      + String(now.getHours()).padStart(2, "0") + ":"
      + String(roundedMin).padStart(2, "0");

    // Find price range for scaling
    const prices = plan.map(e => e.price);
    const minPrice = Math.min(...prices);
    const maxPrice = Math.max(...prices);
    const priceRange = maxPrice - minPrice || 1;

    // Detect slot duration and count actions
    let slotMinutes = 60;
    if (plan.length >= 2) {
      try {
        const t1 = new Date(plan[0].hour).getTime();
        const t2 = new Date(plan[1].hour).getTime();
        const diff = (t2 - t1) / 60000;
        if (diff > 0 && diff <= 60) slotMinutes = diff;
      } catch (e) { /* keep default */ }
    }

    const counts = {};
    plan.forEach(e => {
      counts[e.action] = (counts[e.action] || 0) + 1;
    });

    const title = this._config.title || "Speicherplan";
    const showLegend = this._config.show_legend !== false;
    const showSolar = this._config.show_solar !== false;

    // Preserve scroll position of table container across re-renders
    const oldContainer = this.shadowRoot.querySelector(".table-container");
    const scrollTop = oldContainer ? oldContainer.scrollTop : 0;

    this.shadowRoot.innerHTML = `
      <ha-card header="${title}">
        <style>
          ${this._getStyles()}
        </style>
        <div class="card-content">
          ${showLegend ? this._renderLegend(counts, slotMinutes, hasEpexSlots) : ""}
          <div class="summary">${stateObj.state || ""}</div>
          <div class="chart-container">
            ${this._renderChart(plan, nowSlot, minPrice, maxPrice, priceRange, showSolar, epexSlotSet)}
          </div>
          <div class="toggle-row">
            <button class="toggle-btn" id="toggleTable">
              ${this._showTable ? "Tabelle ausblenden" : "Details anzeigen"}
            </button>
            ${this._showTable ? `<button class="toggle-btn" id="exportCsv" style="margin-left:8px">CSV Export</button>` : ""}
            ${hasEpexSlots ? `<button class="toggle-btn${this._showEpex ? " active" : ""}" id="toggleEpex" style="margin-left:8px">
              ${this._showEpex ? "Prognose ausblenden" : "Prognose anzeigen"}
            </button>` : ""}
          </div>
          ${this._showTable ? this._renderTable(plan, nowSlot) : ""}
        </div>
      </ha-card>
    `;

    // Restore scroll position
    const newContainer = this.shadowRoot.querySelector(".table-container");
    if (newContainer && scrollTop) newContainer.scrollTop = scrollTop;

    this.shadowRoot.getElementById("toggleTable")
      .addEventListener("click", () => {
        this._showTable = !this._showTable;
        this._render();
      });

    const exportBtn = this.shadowRoot.getElementById("exportCsv");
    if (exportBtn) {
      exportBtn.addEventListener("click", () => {
        this._exportCsv(plan);
      });
    }

    const epexBtn = this.shadowRoot.getElementById("toggleEpex");
    if (epexBtn) {
      epexBtn.addEventListener("click", () => {
        this._showEpex = !this._showEpex;
        this._render();
      });
    }
  }

  _exportCsv(plan) {
    const header = "Zeit;Preis (ct);Solar (kWh);SOC (%);Aktion;Grund";
    const rows = plan.map(e => {
      const price = (e.price * 100).toFixed(1);
      const solar = e.solar_kwh > 0 ? e.solar_kwh.toFixed(3) : "0";
      const soc = e.expected_soc || "";
      const action = e.action || "";
      const reason = e.reason || "";
      return `${e.hour};${price};${solar};${soc};${action};${reason}`;
    });
    const csv = [header, ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `speicherplan_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  _renderPlaceholder() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <ha-card header="Speicherplan">
        <div style="padding: 16px; color: var(--secondary-text-color, #888);">
          Bitte Entity konfigurieren (Speicherplan-Sensor)
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

  _formatDuration(slots, slotMinutes) {
    const totalMin = slots * slotMinutes;
    if (totalMin >= 60 && totalMin % 60 === 0) return `${totalMin / 60}h`;
    if (totalMin >= 60) return `${Math.floor(totalMin / 60)}h${String(totalMin % 60).padStart(2, "0")}`;
    return `${totalMin}min`;
  }

  _renderLegend(counts, slotMinutes, hasEpexSlots) {
    let html = '<div class="legend">';
    for (const [action, cfg] of Object.entries(ACTION_CONFIG)) {
      const count = counts[action] || 0;
      if (count === 0) continue;
      html += `
        <span class="legend-item">
          <span class="legend-dot" style="background:${cfg.color}"></span>
          ${cfg.short} (${this._formatDuration(count, slotMinutes)})
        </span>
      `;
    }
    if (hasEpexSlots) {
      html += `
        <span class="legend-item">
          <span class="legend-dot" style="background:repeating-linear-gradient(135deg,#ff9800,#ff9800 2px,transparent 2px,transparent 4px); border:1px solid #ff9800"></span>
          Prognose
        </span>
      `;
    }
    html += "</div>";
    return html;
  }

  _renderChart(plan, nowSlot, minPrice, maxPrice, priceRange, showSolar, epexSlots) {
    const barWidth = Math.max(100 / plan.length, 2);
    const chartHeight = 120;

    let barsHtml = "";
    let solarPoints = [];
    let labelsHtml = "";

    // Find max solar for scaling
    const maxSolar = Math.max(...plan.map(e => e.solar_kwh || 0), 0.1);
    epexSlots = epexSlots || new Set();

    plan.forEach((entry, i) => {
      const cfg = ACTION_CONFIG[entry.action] || ACTION_CONFIG.idle;
      const pricePct = ((entry.price - minPrice) / priceRange) * 80 + 15;
      const left = (i / plan.length) * 100;
      const isCurrent = entry.hour && entry.hour === nowSlot;
      const isEpex = epexSlots.has(entry.hour);

      // Price bar (EPEX slots get striped pattern + lower opacity)
      const epexStyle = isEpex
        ? `background:repeating-linear-gradient(135deg,${cfg.color},${cfg.color} 3px,transparent 3px,transparent 6px); opacity:0.55`
        : `background:${cfg.color}; opacity:${isCurrent ? 1 : 0.75}`;
      const epexLabel = isEpex ? " (Prognose)" : "";
      barsHtml += `
        <div class="bar${isCurrent ? " current" : ""}"
             style="left:${left}%; width:${barWidth}%; height:${pricePct}%; ${epexStyle}"
             title="${this._formatHour(entry.hour)} - ${(entry.price * 100).toFixed(1)} ct/kWh${epexLabel}\n${cfg.label}: ${entry.reason}">
        </div>
      `;

      // Collect solar points for polyline
      if (showSolar && entry.solar_kwh > 0) {
        const solarPct = (entry.solar_kwh / maxSolar) * 80 + 5;
        const centerX = left + barWidth / 2;
        solarPoints.push({ x: centerX, y: 100 - solarPct });
      }

      // Time labels: show only at full hours, every 3 hours (or current)
      const hourMatch = entry.hour.match(/T(\d{2}):(\d{2})/);
      if (hourMatch) {
        const hourNum = parseInt(hourMatch[1], 10);
        const minuteNum = parseInt(hourMatch[2], 10);
        const isFullHour = minuteNum === 0;
        if (isFullHour && (hourNum % 3 === 0 || isCurrent)) {
          labelsHtml += `
            <span class="time-label${isCurrent ? " current" : ""}"
                  style="left:${left + barWidth / 2}%">
              ${String(hourNum).padStart(2, "0")}
            </span>
          `;
        }
      }
    });

    // Render solar as SVG overlay
    let solarOverlay = "";
    if (showSolar && solarPoints.length > 0) {
      const polyPoints = solarPoints.map(p => `${p.x},${p.y}`).join(" ");
      solarOverlay = `<div class="solar-layer">
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" style="width:100%;height:100%;position:absolute;top:0;left:0;overflow:visible">
          ${solarPoints.length > 1 ? `<polyline points="${polyPoints}" fill="none" stroke="#FFD600" stroke-width="2" vector-effect="non-scaling-stroke"/>` : ""}
          ${solarPoints.map(p => `<circle cx="${p.x}" cy="${p.y}" r="3" fill="#FFD600" vector-effect="non-scaling-stroke"/>`).join("")}
        </svg>
      </div>`;
    }

    // Price axis labels
    const priceAxisHtml = `
      <div class="price-axis">
        <span class="price-tick top">${(maxPrice * 100).toFixed(0)} ct</span>
        <span class="price-tick bottom">${(minPrice * 100).toFixed(0)} ct</span>
      </div>
    `;

    // "Now" marker
    let nowMarkerHtml = "";
    const nowIdx = plan.findIndex(e => e.hour && e.hour.startsWith(nowSlot));
    if (nowIdx >= 0) {
      const nowLeft = (nowIdx / plan.length) * 100 + barWidth / 2;
      nowMarkerHtml = `<div class="now-marker" style="left:${nowLeft}%"></div>`;
    }

    // EPEX boundary marker (where Tibber ends and EPEX begins)
    let epexMarkerHtml = "";
    const firstEpexIdx = plan.findIndex(e => epexSlots.has(e.hour));
    if (firstEpexIdx > 0) {
      const epexLeft = (firstEpexIdx / plan.length) * 100;
      epexMarkerHtml = `<div class="epex-marker" style="left:${epexLeft}%" title="Ab hier: EPEX Predictor (Prognose)"></div>`;
    }

    return `
      <div class="chart" style="height:${chartHeight}px">
        ${priceAxisHtml}
        <div class="bars">
          ${barsHtml}
          ${solarOverlay}
          ${nowMarkerHtml}
          ${epexMarkerHtml}
        </div>
        <div class="time-labels">
          ${labelsHtml}
        </div>
      </div>
    `;
  }

  _renderTable(plan, nowSlot) {
    let rows = "";
    plan.forEach(entry => {
      const cfg = ACTION_CONFIG[entry.action] || ACTION_CONFIG.idle;
      const isCurrent = entry.hour && entry.hour === nowSlot;
      const soc = entry.expected_soc != null ? entry.expected_soc.toFixed(0) + " %" : "-";
      rows += `
        <tr class="${isCurrent ? "current-row" : ""}">
          <td class="td-time">${this._formatHour(entry.hour)}</td>
          <td class="td-price">${(entry.price * 100).toFixed(1)} ct</td>
          <td class="td-solar">${entry.solar_kwh > 0 ? (entry.solar_kwh >= 0.1 ? entry.solar_kwh.toFixed(2) + " kWh" : Math.round(entry.solar_kwh * 1000) + " Wh") : "-"}</td>
          <td class="td-soc">${soc}</td>
          <td class="td-action">
            <span class="action-badge" style="background:${cfg.color}">
              ${cfg.short}
            </span>
          </td>
          <td class="td-reason">${entry.reason}</td>
        </tr>
      `;
    });

    return `
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>Zeit</th>
              <th>Preis</th>
              <th>Solar</th>
              <th>SOC</th>
              <th>Aktion</th>
              <th>Grund</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  _formatHour(hourStr) {
    if (!hourStr) return "?";
    // hourStr is like "2024-03-19T14:00"
    const parts = hourStr.split("T");
    if (parts.length === 2) {
      const datePart = parts[0].slice(5); // "03-19"
      const timePart = parts[1].slice(0, 5); // "14:00"
      return `${datePart} ${timePart}`;
    }
    return hourStr;
  }

  _getStyles() {
    return `
      :host {
        --bsm-bg: var(--card-background-color, var(--ha-card-background, #1c1c1c));
        --bsm-text: var(--primary-text-color, #e1e1e1);
        --bsm-text2: var(--secondary-text-color, #aaa);
        --bsm-border: var(--divider-color, #333);
      }
      .card-content {
        padding: 0 16px 16px;
      }
      .summary {
        font-size: 13px;
        color: var(--bsm-text2);
        margin-bottom: 12px;
      }
      .legend {
        display: flex;
        flex-wrap: wrap;
        gap: 8px 16px;
        margin-bottom: 10px;
        font-size: 12px;
        color: var(--bsm-text2);
      }
      .legend-item {
        display: flex;
        align-items: center;
        gap: 4px;
      }
      .legend-dot {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        display: inline-block;
      }

      /* Chart */
      .chart {
        position: relative;
        margin-bottom: 4px;
      }
      .bars {
        position: absolute;
        top: 0; left: 28px; right: 0; bottom: 20px;
        overflow: hidden;
      }
      .bar {
        position: absolute;
        bottom: 0;
        border-radius: 2px 2px 0 0;
        transition: opacity 0.2s;
        cursor: pointer;
      }
      .bar:hover {
        opacity: 1 !important;
        filter: brightness(1.2);
      }
      .bar.current {
        box-shadow: 0 0 0 2px var(--primary-color, #03a9f4);
      }
      .solar-layer {
        position: absolute;
        top: 0; left: 0; right: 0; bottom: 0;
        pointer-events: none;
        overflow: hidden;
      }
      .now-marker {
        position: absolute;
        top: 0;
        bottom: 0;
        width: 2px;
        background: var(--primary-color, #03a9f4);
        opacity: 0.6;
        pointer-events: none;
      }
      .epex-marker {
        position: absolute;
        top: 0;
        bottom: 0;
        width: 2px;
        background: var(--warning-color, #ff9800);
        opacity: 0.7;
        pointer-events: none;
        border-left: 1px dashed var(--warning-color, #ff9800);
      }
      .price-axis {
        position: absolute;
        left: 0;
        top: 0;
        bottom: 20px;
        width: 28px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        font-size: 10px;
        color: var(--bsm-text2);
      }
      .price-tick { padding-right: 4px; text-align: right; }
      .time-labels {
        position: absolute;
        bottom: 0;
        left: 28px;
        right: 0;
        height: 18px;
      }
      .time-label {
        position: absolute;
        transform: translateX(-50%);
        font-size: 10px;
        color: var(--bsm-text2);
      }
      .time-label.current {
        color: var(--primary-color, #03a9f4);
        font-weight: bold;
      }

      /* Toggle */
      .toggle-row {
        text-align: center;
        margin: 8px 0 4px;
      }
      .toggle-btn {
        background: none;
        border: 1px solid var(--bsm-border);
        color: var(--bsm-text2);
        padding: 4px 16px;
        border-radius: 16px;
        cursor: pointer;
        font-size: 12px;
      }
      .toggle-btn:hover {
        background: var(--bsm-border);
      }
      .toggle-btn.active {
        background: var(--warning-color, #ff9800);
        color: #fff;
        border-color: var(--warning-color, #ff9800);
      }

      /* Table */
      .table-container {
        max-height: 400px;
        overflow-y: auto;
        margin-top: 8px;
      }
      table {
        width: 100%;
        border-collapse: collapse;
        font-size: 12px;
      }
      th {
        text-align: left;
        padding: 6px 4px;
        border-bottom: 2px solid var(--bsm-border);
        color: var(--bsm-text2);
        font-weight: 500;
        position: sticky;
        top: 0;
        background: var(--bsm-bg);
      }
      td {
        padding: 5px 4px;
        border-bottom: 1px solid var(--bsm-border);
        color: var(--bsm-text);
      }
      .current-row {
        background: rgba(3, 169, 244, 0.1);
      }
      .current-row td {
        font-weight: 500;
      }
      .td-price, .td-solar, .td-soc {
        text-align: right;
        font-variant-numeric: tabular-nums;
      }
      .action-badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 10px;
        color: #fff;
        font-size: 11px;
        font-weight: 500;
      }
      .td-reason {
        color: var(--bsm-text2);
        font-size: 11px;
      }
    `;
  }
}

/* ── Card Editor ───────────────────────────────────────── */
class BatteryPlanCardEditor extends HTMLElement {
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
        .row input, .row select { flex: 2; }
      </style>
      <div class="row">
        <label>Entity</label>
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
  if (!customElements.get("battery-plan-card")) {
    customElements.define("battery-plan-card", BatteryPlanCard);
  }
  if (!customElements.get("battery-plan-card-editor")) {
    customElements.define("battery-plan-card-editor", BatteryPlanCardEditor);
  }
} catch (e) {
  console.warn("battery-plan-card: registration failed", e);
}

window.customCards = window.customCards || [];
if (!window.customCards.find(c => c.type === "battery-plan-card")) {
  window.customCards.push({
    type: "battery-plan-card",
    name: "Battery Storage Plan",
    description: "Visualizes the battery storage plan as a timeline chart with detail table.",
    preview: false,
    documentationURL: "https://github.com/dEeds83/ha-battery-storage-manager",
  });
}

console.info(
  `%c BATTERY-PLAN-CARD %c v${CARD_VERSION} `,
  "background: #4CAF50; color: #fff; font-weight: bold; padding: 2px 6px; border-radius: 4px 0 0 4px;",
  "background: #333; color: #fff; padding: 2px 6px; border-radius: 0 4px 4px 0;"
);
