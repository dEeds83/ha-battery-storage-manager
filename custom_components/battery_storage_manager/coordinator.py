"""Coordinator for Battery Storage Manager."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

import aiohttp

from .const import (
    ATTR_BATTERY_PLAN,
    ATTR_CURRENT_PRICE,
    ATTR_ESTIMATED_SAVINGS,
    ATTR_EXPECTED_SOLAR_KWH,
    ATTR_NEXT_CHEAP_WINDOW,
    ATTR_NEXT_EXPENSIVE_WINDOW,
    ATTR_OPERATING_MODE,
    ATTR_PLAN_SUMMARY,
    ATTR_STRATEGY,
    CHARGER_TYPE_DIMMER,
    CHARGER_TYPE_SWITCH,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CURRENT_ENTITY,
    CONF_BATTERY_CYCLE_COST,
    CONF_BATTERY_EFFICIENCY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_VOLTAGE_ENTITY,
    CONF_CHARGERS,
    CONF_EPEX_PREDICTOR_ENABLED,
    CONF_EPEX_PREDICTOR_REGION,
    CONSUMPTION_STATS_ROLLING_DAYS,
    DEFAULT_EPEX_PREDICTOR_REGION,
    EPEX_PREDICTOR_BASE_URL,
    STORAGE_KEY_CONSUMPTION,
    STORAGE_VERSION_CONSUMPTION,
    CONF_HOUSE_CONSUMPTION_W,
    CONF_OUTSIDE_TEMPERATURE_ENTITY,
    DEFAULT_BATTERY_CYCLE_COST,
    DEFAULT_BATTERY_EFFICIENCY,
    CONF_INVERTER_FEED_ACTUAL_POWER_ENTITY,
    CONF_INVERTER_FEED_POWER,
    CONF_INVERTER_FEED_POWER_ENTITY,
    CONF_INVERTER_FEED_SWITCH,
    CONF_MAX_SOC,
    CONF_MIN_SOC,
    CONF_PRICE_HIGH_THRESHOLD,
    CONF_PRICE_LOW_THRESHOLD,
    CONF_SOLAR_ENERGY_TODAY_ENTITY,
    CONF_SOLAR_FORECAST_ENTITIES,
    CONF_SOLAR_FORECAST_ENTITY,
    CONF_SOLAR_POWER_ENTITY,
    SOLAR_CALIBRATION_ROLLING_DAYS,
    STORAGE_KEY_SOLAR_CALIBRATION,
    STORAGE_VERSION_SOLAR_CALIBRATION,
    CONF_TIBBER_PRICE_ENTITY,
    CONF_TIBBER_PRICES_ENTITY,
    CONF_TIBBER_PULSE_CONSUMPTION_ENTITY,
    CONF_TIBBER_PULSE_PRODUCTION_ENTITY,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_HOUSE_CONSUMPTION_W,
    DEFAULT_MAX_SOC,
    DEFAULT_MIN_SOC,
    DEFAULT_PRICE_HIGH_THRESHOLD,
    DEFAULT_PRICE_LOW_THRESHOLD,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MODE_CHARGING,
    MODE_DISCHARGING,
    MODE_IDLE,
    MODE_SOLAR_CHARGING,
    STRATEGY_MANUAL,
    STRATEGY_PRICE_OPTIMIZED,
    STRATEGY_SELF_CONSUMPTION,
)

from . import optimizer as optimizer
from .solar import SolarMixin
from .consumption import ConsumptionMixin
from .devices import DevicesMixin
from .epex import EpexMixin
from .history import HistoryMixin

_LOGGER = logging.getLogger(__name__)


class BatteryStorageCoordinator(
    SolarMixin,
    ConsumptionMixin,
    DevicesMixin,
    EpexMixin,
    HistoryMixin,
    DataUpdateCoordinator,
):
    """Coordinator that manages battery charging/discharging based on energy prices."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.entry = entry
        self._config = {**entry.data, **entry.options}

        # Version from manifest.json + source hash for code verification
        try:
            component_dir = Path(__file__).parent
            manifest_path = component_dir / "manifest.json"
            self._version = json.loads(manifest_path.read_text()).get("version", "?")
        except Exception:
            self._version = "?"
            component_dir = None

        # MD5 hash of all .py files for remote code verification via MCP
        import hashlib
        try:
            hasher = hashlib.md5()
            for py_file in sorted(component_dir.glob("*.py")):
                hasher.update(py_file.read_bytes())
            self._source_hash = hasher.hexdigest()[:12]
        except Exception:
            self._source_hash = "?"

        # Entity IDs from config
        self._tibber_price_entity = self._config.get(CONF_TIBBER_PRICE_ENTITY, "")
        self._tibber_prices_entity = self._config.get(CONF_TIBBER_PRICES_ENTITY, "")
        self._pulse_consumption_entity = self._config.get(CONF_TIBBER_PULSE_CONSUMPTION_ENTITY, "")
        self._pulse_production_entity = self._config.get(CONF_TIBBER_PULSE_PRODUCTION_ENTITY, "")

        # Chargers: list of {"switch", "power", "power_entity", "type", "min_power", "active", ...}
        self._chargers: list[dict] = [
            self._build_charger_entry(c) for c in self._config.get(CONF_CHARGERS, [])
        ]

        self._inverter_switch = self._config.get(CONF_INVERTER_FEED_SWITCH, "")
        self._inverter_power = self._config.get(CONF_INVERTER_FEED_POWER, 0)
        self._inverter_power_entity = self._config.get(CONF_INVERTER_FEED_POWER_ENTITY, "")
        self._inverter_actual_power_entity = self._config.get(CONF_INVERTER_FEED_ACTUAL_POWER_ENTITY, "")
        self._battery_soc_entity = self._config.get(CONF_BATTERY_SOC_ENTITY, "")
        self._battery_capacity = self._config.get(
            CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY
        )
        self._min_soc = self._config.get(CONF_MIN_SOC, DEFAULT_MIN_SOC)
        self._max_soc = self._config.get(CONF_MAX_SOC, DEFAULT_MAX_SOC)
        self._price_low = self._config.get(
            CONF_PRICE_LOW_THRESHOLD, DEFAULT_PRICE_LOW_THRESHOLD
        )
        self._price_high = self._config.get(
            CONF_PRICE_HIGH_THRESHOLD, DEFAULT_PRICE_HIGH_THRESHOLD
        )
        self._solar_forecast_entity = self._config.get(CONF_SOLAR_FORECAST_ENTITY, "")
        self._solar_forecast_entities: list[str] = self._config.get(
            CONF_SOLAR_FORECAST_ENTITIES, []
        )
        self._solar_power_entity = self._config.get(CONF_SOLAR_POWER_ENTITY, "")
        self._solar_power: float | None = None  # current solar production in W
        self._solar_energy_today_entity = self._config.get(CONF_SOLAR_ENERGY_TODAY_ENTITY, "")

        # Solar forecast calibration
        self._solar_calibration_store = Store(
            hass,
            STORAGE_VERSION_SOLAR_CALIBRATION,
            f"{DOMAIN}.{entry.entry_id}.{STORAGE_KEY_SOLAR_CALIBRATION}",
        )
        self._solar_calibration_factor: float = 1.0  # multiplier for forecast
        self._solar_calibration_history: list[float] = []  # daily ratios
        self._solar_calibration_loaded = False
        self._solar_calibration_last_date: str | None = None
        self._solar_forecast_today_kwh: float = 0.0  # today's total forecast
        self._intraday_solar_factor: float = 1.0  # intraday correction factor

        self._house_consumption_w = self._config.get(
            CONF_HOUSE_CONSUMPTION_W, DEFAULT_HOUSE_CONSUMPTION_W
        )

        # Optional sensors: outside temperature + Smartshunt
        self._outside_temp_entity = self._config.get(CONF_OUTSIDE_TEMPERATURE_ENTITY, "")
        self._outside_temp: float | None = None
        self._battery_voltage_entity = self._config.get(CONF_BATTERY_VOLTAGE_ENTITY, "")
        self._battery_current_entity = self._config.get(CONF_BATTERY_CURRENT_ENTITY, "")
        self._battery_voltage: float | None = None
        self._battery_current: float | None = None
        self._battery_real_power: float | None = None  # V x A

        # Efficiency tracking from Smartshunt V×I vs charger/inverter power
        # Persistent, 7-day rolling average with daily data points
        self._eff_charge_grid_kwh: float = 0.0    # today's accumulated grid energy
        self._eff_charge_battery_kwh: float = 0.0  # today's accumulated battery-in energy
        self._eff_discharge_battery_kwh: float = 0.0  # today's accumulated battery-out energy
        self._eff_discharge_grid_kwh: float = 0.0  # today's accumulated inverter energy
        self._eff_last_update: datetime | None = None
        self._eff_today_date: str | None = None
        self._eff_history: list[dict] = []  # [{date, charge_eff, discharge_eff}, ...]
        self._eff_rolling_days = 7
        self._eff_store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.efficiency")
        self._eff_loaded = False
        self._measured_charge_efficiency: float | None = None
        self._measured_discharge_efficiency: float | None = None
        self._measured_roundtrip_efficiency: float | None = None

        # Action history: records what was ACTUALLY executed (not just planned)
        # Persistent, 10-min intervals, max 288 entries (48h)
        self._action_history: list[dict] = []
        self._action_history_last_key: str | None = None
        self._action_history_store = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.action_history"
        )
        self._action_history_loaded = False

        # EPEX Predictor for long-term price forecast
        self._epex_enabled = bool(self._config.get(CONF_EPEX_PREDICTOR_ENABLED, False))
        self._epex_region = self._config.get(
            CONF_EPEX_PREDICTOR_REGION, DEFAULT_EPEX_PREDICTOR_REGION
        )
        self._epex_cache: list[dict] = []  # cached EPEX predictions
        self._epex_cache_time: datetime | None = None
        self._epex_cache_ttl = timedelta(hours=2)  # EPEX predictions change slowly
        self._epex_markup: dict | None = None  # regression coefficients {a, b}
        self._epex_terminal_value_per_kwh: float = 0.0  # EUR/kWh incentive for end SOC
        self._epex_visualization: list[dict] = []  # scaled prices for UI only

        # Battery economics
        self._cycle_cost = self._config.get(
            CONF_BATTERY_CYCLE_COST, DEFAULT_BATTERY_CYCLE_COST
        )  # ct/kWh
        self._battery_efficiency = self._config.get(
            CONF_BATTERY_EFFICIENCY, DEFAULT_BATTERY_EFFICIENCY
        ) / 100.0  # convert percent to factor

        # Optimization log (recent decisions for UI)
        self._solar_headroom_pct: float = 0.0
        self._dp_max_soc: float = self._max_soc
        self._optimization_log: list[str] = []
        self._max_log_entries = 50
        self._last_dp_signature: str = ""  # to avoid re-logging identical plans

        # State
        self._strategy = STRATEGY_PRICE_OPTIMIZED
        self._operating_mode = MODE_IDLE
        self._current_price: float | None = None
        self._price_forecast: list[dict] = []
        self._battery_soc: float | None = None
        self._grid_power: float | None = None  # positive = import, negative = export
        self._grid_power_ema: float | None = None  # EMA-smoothed grid power
        self._estimated_savings: float = 0.0
        self._unsub_listeners: list = []
        self._inverter_active = False
        self._inverter_target_power: float = 0  # current target power for zero-feed
        self._inverter_actual_power: float | None = None  # actual power from sensor

        # Runtime toggles
        self._allow_grid_charging = True
        self._allow_discharging = True
        self._allow_solar_charging = True  # zero-export master switch
        self._use_solar_forecast = bool(
            self._solar_forecast_entity or self._solar_forecast_entities
        )

        # Solar forecast and battery plan
        self._solar_forecast: dict[str, float] = {}  # hour_key -> Wh expected
        self._battery_plan: list[dict] = []  # hourly plan entries
        self._plan_summary: str = ""
        self._expected_solar_kwh: float = 0.0

        # PID controller state for zero-feed regulation
        self._pid_integral: float = 0.0
        self._pid_last_error: float | None = None
        self._pid_kp: float = 0.6   # proportional gain
        self._pid_ki: float = 0.15  # integral gain
        self._pid_kd: float = 0.1   # derivative gain

        # Hysteresis state for charger switching
        self._charger_last_switch_time: dict[int, datetime] = {}
        self._charger_min_on_time = timedelta(seconds=120)  # min 2 min on
        self._charger_min_off_time = timedelta(seconds=60)   # min 1 min off

        # Consumption statistics: rolling average per hour-of-day, split by day type
        # Format: {"wd_0": [w1, w2, ...], "we_0": [...], ...}
        # wd_ = weekday (Mon-Fri), we_ = weekend (Sat-Sun)
        self._consumption_store = Store(
            hass,
            STORAGE_VERSION_CONSUMPTION,
            f"{DOMAIN}.{entry.entry_id}.{STORAGE_KEY_CONSUMPTION}",
        )
        self._consumption_stats: dict[str, list[float]] = {}
        self._consumption_hourly_samples: list[float] = []  # samples within current hour
        self._consumption_last_hour: int | None = None
        self._consumption_last_daytype: str | None = None  # "wd" or "we"
        self._consumption_loaded = False

        # Tibber watchdog: restart integration if Pulse data is stale
        self._tibber_watchdog_stale_since: datetime | None = None
        self._tibber_watchdog_threshold = timedelta(minutes=5)
        self._tibber_last_restart: datetime | None = None
        self._tibber_restart_cooldown = timedelta(minutes=15)

        # Track whether entities have ever been seen (for startup race condition)
        self._price_entity_seen = False
        self._prices_entity_seen = False
        self._fallback_price_range: dict | None = None

    async def _check_tibber_watchdog(self) -> None:
        """Restart Tibber integration if Pulse data appears stale.

        Checks last_changed of the consumption entity. If it hasn't
        changed for > 5 minutes, reloads the Tibber config entry.
        Cooldown of 15 minutes between restarts to avoid loops.
        """
        if not self._pulse_consumption_entity:
            return

        state = self.hass.states.get(self._pulse_consumption_entity)
        if state is None:
            return

        now = dt_util.utcnow()

        # Check if state is fresh
        last_changed = state.last_changed
        if last_changed and (now - last_changed) < self._tibber_watchdog_threshold:
            # Data is fresh, reset stale tracker
            self._tibber_watchdog_stale_since = None
            return

        # Data is stale
        if self._tibber_watchdog_stale_since is None:
            self._tibber_watchdog_stale_since = now
            _LOGGER.warning(
                "Tibber Pulse data stale: %s last changed %s",
                self._pulse_consumption_entity,
                last_changed,
            )
            return

        # Check if we've been stale long enough to trigger a restart
        if (now - self._tibber_watchdog_stale_since) < self._tibber_watchdog_threshold:
            return

        # Check cooldown
        if self._tibber_last_restart and (now - self._tibber_last_restart) < self._tibber_restart_cooldown:
            _LOGGER.debug("Tibber watchdog: still in cooldown, skipping restart")
            return

        # Find and reload the Tibber config entry
        for entry in self.hass.config_entries.async_entries("tibber"):
            _LOGGER.warning(
                "Tibber Pulse data stale for >%d min – reloading Tibber integration (%s)",
                int(self._tibber_watchdog_threshold.total_seconds() / 60),
                entry.entry_id,
            )
            await self.hass.config_entries.async_reload(entry.entry_id)
            self._tibber_last_restart = now
            self._tibber_watchdog_stale_since = None
            break

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and process data, then decide on battery action."""
        _LOGGER.debug(
            "Update cycle - Price entity: '%s', Prices entity: '%s'",
            self._tibber_price_entity,
            self._tibber_prices_entity,
        )
        await self._load_consumption_stats()
        await self._load_solar_calibration()
        await self._load_efficiency_data()
        self._read_sensor_states()
        self._validate_operating_mode()
        await self._check_tibber_watchdog()
        await self._record_consumption()
        await self._update_price_forecast()
        await self._extend_prices_with_epex()
        await self._read_solar_forecast()
        self._apply_solar_calibration()
        self._apply_intraday_solar_correction()
        await self._calibrate_solar_forecast()
        self._create_battery_plan()

        if self._strategy == STRATEGY_PRICE_OPTIMIZED:
            await self._run_price_optimization()
        elif self._strategy == STRATEGY_SELF_CONSUMPTION:
            await self._run_self_consumption()
        # STRATEGY_MANUAL: no automatic charge/discharge actions

        # Always capture free solar surplus, regardless of strategy/mode.
        # _calculate_true_solar_surplus() now uses the solar power sensor
        # directly, so it won't mistake inverter overshoot for solar.
        if self._operating_mode in (
            MODE_IDLE, MODE_DISCHARGING, MODE_SOLAR_CHARGING
        ):
            await self._try_solar_opportunistic()

        # Persist efficiency data periodically (piggyback on action history ~10 min)
        await self._save_efficiency_data()

        # Record action history (every 10 min, 48h retention, persistent)
        await self._record_action_history()

        return self._build_data()

    @staticmethod
    def _build_charger_entry(c: dict) -> dict:
        """Normalize a CONF_CHARGERS dict into the runtime per-charger dict."""
        return {
            "switch": c.get("switch", ""),
            "power": c.get("power", 0),
            "power_entity": c.get("power_entity", ""),
            "actual_power_entity": c.get("actual_power_entity", ""),
            "type": c.get("type", CHARGER_TYPE_SWITCH),
            "min_power": c.get("min_power", 0),
            "active": False,
            "target_power": 0.0,
            "measured_power": None,
        }

    @property
    def is_dimmer_setup(self) -> bool:
        """True if the configured charger is a single dimmer."""
        return any(c.get("type") == CHARGER_TYPE_DIMMER for c in self._chargers)

    def _read_sensor_states(self) -> None:
        """Read current sensor values from Home Assistant."""
        # Current electricity price
        price_state = self.hass.states.get(self._tibber_price_entity)
        if price_state and price_state.state not in ("unknown", "unavailable"):
            try:
                self._current_price = float(price_state.state)
                _LOGGER.debug(
                    "Price read successfully: %s EUR/kWh from '%s' (state: '%s')",
                    self._current_price,
                    self._tibber_price_entity,
                    price_state.state,
                )
            except (ValueError, TypeError) as err:
                self._current_price = None
                _LOGGER.warning(
                    "Could not convert price state '%s' to float from entity '%s': %s",
                    price_state.state,
                    self._tibber_price_entity,
                    err,
                )
        else:
            self._current_price = None
            if price_state:
                _LOGGER.debug(
                    "Price entity '%s' has state '%s' - not usable",
                    self._tibber_price_entity,
                    price_state.state,
                )
            else:
                # Use debug level if entity was never seen (startup race condition)
                log_fn = _LOGGER.warning if self._price_entity_seen else _LOGGER.debug
                log_fn(
                    "Price entity '%s' not found in Home Assistant. "
                    "Check that the entity ID is correct and the Tibber integration is loaded.",
                    self._tibber_price_entity,
                )

        if price_state:
            self._price_entity_seen = True

        # Battery SOC
        soc_state = self.hass.states.get(self._battery_soc_entity)
        if soc_state and soc_state.state not in ("unknown", "unavailable"):
            try:
                self._battery_soc = float(soc_state.state)
            except (ValueError, TypeError):
                self._battery_soc = None

        # Grid power from Tibber Pulse: consumption - production = net grid power
        # positive = net import from grid, negative = net export to grid
        consumption = None
        production = None

        cons_state = self.hass.states.get(self._pulse_consumption_entity)
        if cons_state and cons_state.state not in ("unknown", "unavailable"):
            try:
                consumption = float(cons_state.state)
            except (ValueError, TypeError):
                pass

        prod_state = self.hass.states.get(self._pulse_production_entity)
        if prod_state and prod_state.state not in ("unknown", "unavailable"):
            try:
                production = float(prod_state.state)
            except (ValueError, TypeError):
                pass

        if consumption is not None and production is not None:
            self._grid_power = consumption - production
        elif consumption is not None:
            self._grid_power = consumption
        elif production is not None:
            self._grid_power = -production
        else:
            self._grid_power = None

        # Asymmetric EMA smoothing for PID:
        # - Fast down (α=0.7): reduce inverter quickly when load drops
        # - Slow up (α=0.2): dampen spikes from toggle devices (fridge etc.)
        if self._grid_power is not None:
            if self._grid_power_ema is None:
                self._grid_power_ema = self._grid_power
            else:
                alpha = 0.2 if self._grid_power > self._grid_power_ema else 0.85
                self._grid_power_ema = (
                    alpha * self._grid_power
                    + (1 - alpha) * self._grid_power_ema
                )

        # Inverter actual power
        if self._inverter_actual_power_entity:
            inv_state = self.hass.states.get(self._inverter_actual_power_entity)
            if inv_state and inv_state.state not in ("unknown", "unavailable"):
                try:
                    self._inverter_actual_power = float(inv_state.state)
                except (ValueError, TypeError):
                    self._inverter_actual_power = None
            else:
                self._inverter_actual_power = None

        # Current solar production (actual sensor, not forecast)
        if self._solar_power_entity:
            solar_state = self.hass.states.get(self._solar_power_entity)
            if solar_state and solar_state.state not in ("unknown", "unavailable"):
                try:
                    self._solar_power = float(solar_state.state)
                except (ValueError, TypeError):
                    self._solar_power = None
            else:
                self._solar_power = None

        # Outside temperature (for consumption forecast)
        if self._outside_temp_entity:
            temp_state = self.hass.states.get(self._outside_temp_entity)
            if temp_state and temp_state.state not in ("unknown", "unavailable"):
                try:
                    self._outside_temp = float(temp_state.state)
                except (ValueError, TypeError):
                    self._outside_temp = None
            else:
                self._outside_temp = None

        # Read measured charger power from per-charger power sensors.
        # For dimmer: prefer actual_power_entity (separate sensor); fall back
        # to power_entity (number setpoint) which equals target.
        for c in self._chargers:
            if c.get("type") == CHARGER_TYPE_DIMMER:
                read_entity = c.get("actual_power_entity", "") or c.get("power_entity", "")
            else:
                read_entity = c.get("power_entity", "")
            if read_entity:
                cp_state = self.hass.states.get(read_entity)
                if cp_state and cp_state.state not in ("unknown", "unavailable"):
                    try:
                        c["measured_power"] = abs(float(cp_state.state))
                    except (ValueError, TypeError):
                        c["measured_power"] = None
                else:
                    c["measured_power"] = None
            else:
                c["measured_power"] = None

        # Smartshunt battery voltage + current -> real power
        if self._battery_voltage_entity:
            v_state = self.hass.states.get(self._battery_voltage_entity)
            if v_state and v_state.state not in ("unknown", "unavailable"):
                try:
                    self._battery_voltage = float(v_state.state)
                except (ValueError, TypeError):
                    self._battery_voltage = None
            else:
                self._battery_voltage = None

        if self._battery_current_entity:
            c_state = self.hass.states.get(self._battery_current_entity)
            if c_state and c_state.state not in ("unknown", "unavailable"):
                try:
                    self._battery_current = float(c_state.state)
                except (ValueError, TypeError):
                    self._battery_current = None
            else:
                self._battery_current = None

        if self._battery_voltage is not None and self._battery_current is not None:
            self._battery_real_power = abs(
                self._battery_voltage * self._battery_current
            )
        else:
            self._battery_real_power = None

        # Efficiency tracking: accumulate energy from Smartshunt vs charger/inverter
        self._update_efficiency_tracking()

        # Sync charger active flags with actual switch states
        self._sync_device_states()

    async def _load_efficiency_data(self) -> None:
        """Load persisted efficiency history from store."""
        if self._eff_loaded:
            return
        self._eff_loaded = True
        data = await self._eff_store.async_load()
        if data and isinstance(data, dict):
            self._eff_history = data.get("history", [])
            today_data = data.get("today", {})
            self._eff_charge_grid_kwh = today_data.get("charge_grid_kwh", 0.0)
            self._eff_charge_battery_kwh = today_data.get("charge_battery_kwh", 0.0)
            self._eff_discharge_battery_kwh = today_data.get("discharge_battery_kwh", 0.0)
            self._eff_discharge_grid_kwh = today_data.get("discharge_grid_kwh", 0.0)
            self._eff_today_date = today_data.get("date")
            _LOGGER.info(
                "Loaded efficiency data: %d days history, today %s",
                len(self._eff_history), self._eff_today_date,
            )
        self._recalculate_rolling_efficiency()

    async def _save_efficiency_data(self) -> None:
        """Persist efficiency history and today's accumulators."""
        await self._eff_store.async_save({
            "history": self._eff_history[-self._eff_rolling_days:],
            "today": {
                "date": self._eff_today_date,
                "charge_grid_kwh": round(self._eff_charge_grid_kwh, 4),
                "charge_battery_kwh": round(self._eff_charge_battery_kwh, 4),
                "discharge_battery_kwh": round(self._eff_discharge_battery_kwh, 4),
                "discharge_grid_kwh": round(self._eff_discharge_grid_kwh, 4),
            },
        })

    def _recalculate_rolling_efficiency(self) -> None:
        """Recalculate rolling average efficiency from history + today."""
        # Collect all data points (history + today if enough data)
        charge_effs = []
        discharge_effs = []

        for entry in self._eff_history[-self._eff_rolling_days:]:
            if entry.get("charge_eff") is not None:
                charge_effs.append(entry["charge_eff"])
            if entry.get("discharge_eff") is not None:
                discharge_effs.append(entry["discharge_eff"])

        # Add today's live values if enough data
        if self._eff_charge_grid_kwh > 0.1:
            today_charge = min(1.0, self._eff_charge_battery_kwh / self._eff_charge_grid_kwh)
            charge_effs.append(today_charge)
        if self._eff_discharge_battery_kwh > 0.1:
            today_discharge = min(1.0, self._eff_discharge_grid_kwh / self._eff_discharge_battery_kwh)
            discharge_effs.append(today_discharge)

        self._measured_charge_efficiency = (
            sum(charge_effs) / len(charge_effs) if charge_effs else None
        )
        self._measured_discharge_efficiency = (
            sum(discharge_effs) / len(discharge_effs) if discharge_effs else None
        )
        if self._measured_charge_efficiency and self._measured_discharge_efficiency:
            self._measured_roundtrip_efficiency = (
                self._measured_charge_efficiency * self._measured_discharge_efficiency
            )
        else:
            self._measured_roundtrip_efficiency = None

    def _update_efficiency_tracking(self) -> None:
        """Accumulate charge/discharge energy for efficiency measurement.

        Compares Smartshunt V×I (actual battery power) with charger/inverter
        power to derive charge and discharge efficiency.  At midnight, today's
        values are saved as a history entry and a 7-day rolling average is
        maintained.  Data is persisted to survive HA restarts.
        """
        now = dt_util.utcnow()
        today_str = now.strftime("%Y-%m-%d")

        # Day rollover: save today's efficiency as history entry
        if self._eff_today_date and self._eff_today_date != today_str:
            entry: dict[str, Any] = {"date": self._eff_today_date}
            if self._eff_charge_grid_kwh > 0.1:
                entry["charge_eff"] = round(
                    min(1.0, self._eff_charge_battery_kwh / self._eff_charge_grid_kwh), 4
                )
                entry["charge_kwh"] = round(self._eff_charge_grid_kwh, 2)
            if self._eff_discharge_battery_kwh > 0.1:
                entry["discharge_eff"] = round(
                    min(1.0, self._eff_discharge_grid_kwh / self._eff_discharge_battery_kwh), 4
                )
                entry["discharge_kwh"] = round(self._eff_discharge_battery_kwh, 2)

            if "charge_eff" in entry or "discharge_eff" in entry:
                self._eff_history.append(entry)
                # Trim to rolling window
                self._eff_history = self._eff_history[-self._eff_rolling_days:]
                _LOGGER.info(
                    "Efficiency day closed (%s): charge=%.1f%% (%.2f kWh), "
                    "discharge=%.1f%% (%.2f kWh)",
                    self._eff_today_date,
                    entry.get("charge_eff", 0) * 100,
                    entry.get("charge_kwh", 0),
                    entry.get("discharge_eff", 0) * 100,
                    entry.get("discharge_kwh", 0),
                )

            # Reset today's accumulators
            self._eff_charge_grid_kwh = 0.0
            self._eff_charge_battery_kwh = 0.0
            self._eff_discharge_battery_kwh = 0.0
            self._eff_discharge_grid_kwh = 0.0

        self._eff_today_date = today_str

        # Need Smartshunt data and a previous timestamp for time delta
        if self._battery_voltage is None or self._battery_current is None:
            self._eff_last_update = now
            return

        if self._eff_last_update is None:
            self._eff_last_update = now
            return

        dt_hours = (now - self._eff_last_update).total_seconds() / 3600
        self._eff_last_update = now

        # Skip unreasonable time deltas (> 5 min means we missed cycles)
        if dt_hours <= 0 or dt_hours > 5 / 60:
            return

        battery_power_w = self._battery_voltage * self._battery_current
        # Positive current = charging, negative = discharging
        changed = False

        # Only track efficiency during grid charging.  Solar charging has
        # too much fluctuation (clouds, surplus variations) to give stable
        # efficiency readings.
        if self._operating_mode == MODE_CHARGING and battery_power_w > 10:
            grid_power_w = sum(
                c.get("measured_power") or c["power"]
                for c in self._chargers if c["active"]
            )
            if grid_power_w > 0:
                self._eff_charge_grid_kwh += grid_power_w * dt_hours / 1000
                self._eff_charge_battery_kwh += battery_power_w * dt_hours / 1000
                changed = True

        elif self._operating_mode == MODE_DISCHARGING and battery_power_w < -10:
            battery_out_w = abs(battery_power_w)
            inverter_w = self._inverter_actual_power or 0
            if inverter_w > 0:
                self._eff_discharge_battery_kwh += battery_out_w * dt_hours / 1000
                self._eff_discharge_grid_kwh += inverter_w * dt_hours / 1000
                changed = True

        if changed:
            self._recalculate_rolling_efficiency()

    def _validate_operating_mode(self) -> None:
        """Ensure operating mode matches actual device states.

        If we think we're charging but no charger is on, reset to idle.
        Same for discharging with inverter off.  This catches post-restart
        inconsistencies and external switch changes.
        """
        any_charger_on = any(c["active"] for c in self._chargers)

        if self._operating_mode in (MODE_CHARGING, MODE_SOLAR_CHARGING) and not any_charger_on:
            _LOGGER.warning(
                "Mode is %s but no charger is active -> resetting to IDLE",
                self._operating_mode,
            )
            self._operating_mode = MODE_IDLE

        if self._operating_mode == MODE_DISCHARGING and not self._inverter_active:
            _LOGGER.warning(
                "Mode is DISCHARGING but inverter is not active -> resetting to IDLE"
            )
            self._operating_mode = MODE_IDLE

    async def _update_price_forecast(self) -> None:
        """Update price forecast, preferring tibber.get_prices action."""
        # Try the tibber.get_prices action first (HA 2024.8+)
        if await self._fetch_prices_via_action():
            self._fallback_price_range = None
            _LOGGER.debug(
                "Price forecast built with %d entries (via tibber.get_prices action)",
                len(self._price_forecast),
            )
            return

        # Fallback: read from entity attributes (older Tibber integration)
        self._fetch_prices_from_attributes()

    async def _fetch_prices_via_action(self) -> bool:
        """Fetch prices using the tibber.get_prices action. Returns True on success."""
        if not self.hass.services.has_service("tibber", "get_prices"):
            _LOGGER.debug("tibber.get_prices action not available, using attribute fallback")
            return False

        now = dt_util.now()
        start = now.replace(minute=0, second=0, microsecond=0)
        end = (now + timedelta(days=1)).replace(hour=23, minute=59, second=0, microsecond=0)

        try:
            response = await self.hass.services.async_call(
                "tibber",
                "get_prices",
                {"start": start.isoformat(), "end": end.isoformat()},
                blocking=True,
                return_response=True,
            )
        except Exception:
            _LOGGER.debug("tibber.get_prices action call failed", exc_info=True)
            return False

        if not response or "prices" not in response:
            _LOGGER.debug("tibber.get_prices returned no price data: %s", response)
            return False

        # Response format: {"prices": {"HomeName": [{"start_time": "...", "price": 0.46}, ...]}}
        self._price_forecast = []
        for home_prices in response["prices"].values():
            if not isinstance(home_prices, list):
                continue
            for entry in home_prices:
                if isinstance(entry, dict) and "start_time" in entry and "price" in entry:
                    self._price_forecast.append({
                        "start": str(entry["start_time"]),
                        "total": entry["price"],
                    })
            # Use the first home's prices only
            break

        return len(self._price_forecast) > 0

    def _fetch_prices_from_attributes(self) -> None:
        """Fallback: read price forecast from Tibber entity attributes."""
        prices_entity = self._tibber_prices_entity or self._tibber_price_entity
        prices_state = self.hass.states.get(prices_entity)
        if not prices_state:
            log_fn = _LOGGER.warning if self._prices_entity_seen else _LOGGER.debug
            log_fn(
                "Prices forecast entity '%s' not found in Home Assistant.",
                prices_entity,
            )
            return

        self._prices_entity_seen = True

        # Tibber provides today and tomorrow prices in attributes
        today = prices_state.attributes.get("today", [])
        tomorrow = prices_state.attributes.get("tomorrow", [])

        _LOGGER.debug(
            "Price forecast from '%s': %d today entries, %d tomorrow entries. "
            "Available attributes: %s",
            prices_entity,
            len(today) if isinstance(today, list) else 0,
            len(tomorrow) if isinstance(tomorrow, list) else 0,
            list(prices_state.attributes.keys()),
        )

        self._price_forecast = []
        for price_entry in today + tomorrow:
            if isinstance(price_entry, dict):
                self._price_forecast.append({
                    "start": price_entry.get("startsAt", ""),
                    "total": price_entry.get("total", 0),
                })

        # Fallback: if no today/tomorrow forecast available, build a simplified
        # forecast from summary attributes (max_price, avg_price, min_price)
        if not self._price_forecast:
            max_price = prices_state.attributes.get("max_price")
            avg_price = prices_state.attributes.get("avg_price")
            min_price = prices_state.attributes.get("min_price")

            if max_price is not None and min_price is not None:
                _LOGGER.debug(
                    "No hourly forecast available. Using price summary attributes: "
                    "min=%.4f, avg=%.4f, max=%.4f",
                    min_price,
                    avg_price or 0,
                    max_price,
                )
                self._fallback_price_range = {
                    "min": float(min_price),
                    "avg": float(avg_price) if avg_price is not None else None,
                    "max": float(max_price),
                }
            else:
                self._fallback_price_range = None
                _LOGGER.debug(
                    "No hourly forecast and no summary price attributes available "
                    "on '%s'.",
                    prices_entity,
                )
        else:
            self._fallback_price_range = None

    # ── Battery plan ────────────────────────────────────────────────

    def _create_battery_plan(self) -> None:
        """Create a cost-optimized battery plan using Dynamic Programming.

        Uses backward DP over discretized SOC states to find the globally
        optimal sequence of charge/discharge/idle actions that maximizes
        profit (= revenue from discharging - cost of charging - cycle wear).

        Considers:
        - Effective charge cost (solar reduces grid draw)
        - Battery round-trip efficiency
        - Cycle degradation cost (configurable ct/kWh)
        - Pre-solar discharge to create headroom for free solar
        - Weekday/weekend consumption patterns
        - Intraday solar correction factor
        """
        now = dt_util.now()

        slot_data, slot_h = self._build_slot_data(now)
        if not slot_data:
            self._battery_plan = []
            self._plan_summary = "Keine Preisdaten verfügbar"
            return

        # Battery parameters scaled to slot duration
        current_soc = self._battery_soc if self._battery_soc is not None else 50.0
        # Use configured (nominal) power for DP planning — measured power
        # varies with SOC/voltage and would make the plan too pessimistic
        # at high SOC or too optimistic at low SOC.
        charge_power_w = sum(c["power"] for c in self._chargers)
        charge_kwh_slot = charge_power_w / 1000 * slot_h
        discharge_power_w = self._inverter_power or 800
        discharge_kwh_slot = discharge_power_w / 1000 * slot_h
        cap = self._battery_capacity
        # Use measured roundtrip efficiency if available, fallback to configured
        if self._measured_roundtrip_efficiency is not None:
            efficiency = self._measured_roundtrip_efficiency
        else:
            efficiency = self._battery_efficiency  # configured value, e.g. 0.90
        cycle_cost_eur = self._cycle_cost / 100  # convert ct to EUR

        # Per-day consumption forecast (weekday/weekend aware)
        # Cache forecasts per date to avoid recalculation
        consumption_cache: dict[str, dict[int, float]] = {}

        # Enrich slot data with solar info and effective charge cost
        slots_per_hour = max(1, round(1.0 / slot_h))
        for h in slot_data:
            hour_of_day = h.get("hour_of_day", 12)
            slot_date = h["start_dt"]
            date_key = slot_date.strftime("%Y-%m-%d")
            if date_key not in consumption_cache:
                consumption_cache[date_key] = self.get_hourly_consumption_forecast(slot_date)
            h["house_w"] = consumption_cache[date_key].get(hour_of_day, self._house_consumption_w)
            house_kwh_slot = h["house_w"] / 1000 * slot_h

            solar_wh_hour = self._solar_forecast.get(h["hour_key"], 0)
            h["solar_kwh"] = (solar_wh_hour / 1000) / slots_per_hour
            h["solar_wh_hour_raw"] = solar_wh_hour
            h["solar_surplus_kwh"] = max(0, h["solar_kwh"] - house_kwh_slot)

            if charge_kwh_slot > 0:
                grid_fraction = max(0, charge_kwh_slot - h["solar_surplus_kwh"]) / charge_kwh_slot
            else:
                grid_fraction = 1.0
            h["effective_charge_cost"] = grid_fraction * h["price"]
            h["grid_fraction"] = grid_fraction

        # ── Solar headroom: reserve SOC capacity for expected surplus ──
        # Sum expected solar surplus (free energy) and reduce grid max_soc
        # so the battery has room to absorb it.  Solar charging itself
        # (opportunistic) can still go up to the real max_soc.
        # Only count surplus until next sunset (first gap where solar == 0
        # after at least one slot with solar > 0), so tomorrow's forecast
        # doesn't block cheap grid charging today.
        expected_surplus_kwh = 0.0
        seen_solar = False
        for h in slot_data:
            if h.get("solar_wh_hour", 0) > 0:
                seen_solar = True
                expected_surplus_kwh += h.get("solar_surplus_kwh", 0)
            elif seen_solar:
                # Sun has set — stop counting
                break
        if cap > 0 and expected_surplus_kwh > 0:
            headroom_pct = min(
                expected_surplus_kwh / cap * 100,
                self._max_soc - self._min_soc - 10,  # keep at least 10% charge range
            )
            # Only apply headroom if significant (> 5% SOC worth of solar)
            if headroom_pct > 5:
                dp_max_soc = round(self._max_soc - headroom_pct, 1)
                _LOGGER.info(
                    "Solar headroom: %.1f kWh surplus expected -> "
                    "grid max_soc %.1f%% (real max_soc %.1f%%, headroom %.1f%%)",
                    expected_surplus_kwh, dp_max_soc,
                    self._max_soc, headroom_pct,
                )
            else:
                dp_max_soc = self._max_soc
                headroom_pct = 0.0
        else:
            dp_max_soc = self._max_soc
            headroom_pct = 0.0
        self._solar_headroom_pct = round(headroom_pct, 1)
        self._dp_max_soc = dp_max_soc

        n = len(slot_data)
        hourly_data = slot_data

        # ── Scenario-based DP ────────────────────────────────────
        scenarios = [
            {"name": "expected",    "solar_factor": 1.0, "consumption_factor": 1.0},
            {"name": "pessimistic", "solar_factor": 0.6, "consumption_factor": 1.2},
            {"name": "optimistic",  "solar_factor": 1.2, "consumption_factor": 0.8},
        ]

        scenario_actions: list[list[str]] = []
        scenario_profits: list[float] = []

        for scenario in scenarios:
            sf = scenario["solar_factor"]
            cf = scenario["consumption_factor"]

            # Adjust slot data for this scenario
            for h in hourly_data:
                adj_solar = h["solar_kwh"] * sf
                adj_house = h["house_w"] * cf / 1000 * slot_h
                adj_surplus = max(0, adj_solar - adj_house)
                if charge_kwh_slot > 0:
                    h["_scn_grid_frac"] = max(0, charge_kwh_slot - adj_surplus) / charge_kwh_slot
                else:
                    h["_scn_grid_frac"] = 1.0

            actions, profit = optimizer.solve_dp(
                hourly_data, n, current_soc, charge_kwh_slot, discharge_kwh_slot,
                cap, efficiency, cycle_cost_eur, slot_h,
                min_soc=self._min_soc,
                max_soc=dp_max_soc,
                epex_terminal_value_per_kwh=self._epex_terminal_value_per_kwh,
                battery_efficiency=self._battery_efficiency,
            )
            scenario_actions.append(actions)
            scenario_profits.append(profit)

        # Restore original grid_fraction for plan building
        for h in hourly_data:
            h["_scn_grid_frac"] = h["grid_fraction"]

        # Asymmetric vote: charge follows expected scenario (index 0),
        # discharge requires majority (>=2 of 3 scenarios).
        expected = scenario_actions[0]
        actions = []
        for t in range(n):
            exp_act = expected[t]
            if exp_act == "charge":
                actions.append("charge")
            elif exp_act == "discharge":
                votes = [sa[t] for sa in scenario_actions]
                if votes.count("discharge") >= 2:
                    actions.append("discharge")
                else:
                    actions.append("idle")
            else:
                actions.append(exp_act if exp_act in ("idle", "hold") else "idle")

        # Use the pessimistic profit as the reported profit (conservative)
        actual_profit = scenario_profits[1]  # pessimistic scenario

        _LOGGER.debug(
            "Scenario DP: expected=%.3f, pessimistic=%.3f, optimistic=%.3f EUR",
            scenario_profits[0], scenario_profits[1], scenario_profits[2],
        )

        # ── Smooth micro-cycles ─────────────────────────────────
        actions, smoothed = optimizer.smooth_plan(
            actions, hourly_data, n, efficiency, cycle_cost_eur,
            charge_kwh_slot, discharge_kwh_slot, cap, current_soc,
            min_soc=self._min_soc,
            max_soc=dp_max_soc,
            slot_h=slot_h,
        )

        # Log DP result (only when plan changes)
        charge_slots = sum(1 for a in actions if a == "charge")
        discharge_slots = sum(1 for a in actions if a == "discharge")
        dp_signature = f"{charge_slots}:{discharge_slots}:{actual_profit:.2f}"

        if dp_signature != self._last_dp_signature:
            profit_str = f"{actual_profit:.3f}".replace(".", ",")
            # Count how many charge slots have solar contribution
            solar_assisted = sum(
                1 for i, a in enumerate(actions)
                if a == "charge" and hourly_data[i]["solar_surplus_kwh"] > 0.05
            )
            solar_info = f", davon {solar_assisted} mit Solar" if solar_assisted else ""
            dp_msg = (
                f"DP-Optimierung: {charge_slots} Lade{solar_info}, "
                f"{discharge_slots} Entlade Slots \u2192 Profit {profit_str} EUR "
                f"(Effizienz {self._battery_efficiency*100:.0f}%, "
                f"Zykluskosten {self._cycle_cost:.0f} ct/kWh)"
            )
            _LOGGER.info(dp_msg)
            self._log_optimization(dp_msg)
            self._last_dp_signature = dp_signature

        # ── Pre-solar discharge enhancement ──────────────────────
        presolar_discharge_hours: set[int] = set()
        first_solar_idx = next(
            (i for i, h in enumerate(hourly_data) if h["solar_surplus_kwh"] > 0.05),
            n,
        )
        for i in range(first_solar_idx):
            if actions[i] == "discharge":
                later_solar = any(
                    hourly_data[j]["solar_surplus_kwh"] > 0.05
                    for j in range(i + 1, n)
                )
                if later_solar:
                    presolar_discharge_hours.add(i)

        # ── Build plan with SOC simulation and reasons ───────────
        self._battery_plan = []
        estimated_soc = current_soc
        charge_count = 0
        discharge_count = 0
        grid_charge_kwh = 0.0
        total_charge_kwh = 0.0

        for i, h in enumerate(hourly_data):
            action = actions[i]
            delta_kwh = 0.0

            if action == "charge":
                if estimated_soc >= self._max_soc:
                    action = "idle"
                else:
                    delta_kwh = min(charge_kwh_slot, (self._max_soc - estimated_soc) / 100 * cap)
                    grid_charge_kwh += delta_kwh * h["grid_fraction"]
                    total_charge_kwh += delta_kwh
            elif action == "discharge":
                if estimated_soc <= self._min_soc:
                    action = "idle"
                else:
                    delta_kwh = min(discharge_kwh_slot, (estimated_soc - self._min_soc) / 100 * cap)

            if action == "charge":
                estimated_soc += delta_kwh / cap * 100
                charge_count += 1
            elif action == "discharge":
                estimated_soc -= delta_kwh / cap * 100
                discharge_count += 1

            if action == "idle":
                has_future_discharge = any(
                    actions[j] == "discharge" for j in range(i + 1, n)
                )
                if has_future_discharge and estimated_soc > self._min_soc + 5:
                    action = "hold"

            estimated_soc = max(self._min_soc, min(self._max_soc, estimated_soc))

            # Build reason text with clear explanation
            reason = self._build_plan_reason(
                action, h, i, presolar_discharge_hours, hourly_data, actions
            )

            self._battery_plan.append({
                "hour": h.get("slot_key", h["hour_key"] + ":00"),
                "price": round(h["price"], 4),
                "solar_kwh": round(h["solar_kwh"], 3),
                "solar_wh_hour": round(h.get("solar_wh_hour_raw", 0)),
                "solar_surplus_kwh": round(h["solar_surplus_kwh"], 2),
                "expected_soc": round(estimated_soc, 1),
                "action": action,
                "reason": reason,
            })

        # Summary
        parts = []
        def _fmt_duration(slots: int) -> str:
            """Format a slot count as a human-readable duration string."""
            total_min = round(slots * slot_h * 60)
            if total_min >= 60 and total_min % 60 == 0:
                return f"{total_min // 60}h"
            if total_min >= 60:
                return f"{total_min // 60}h{total_min % 60:02d}"
            return f"{total_min}min"

        if charge_count:
            parts.append(
                f"{_fmt_duration(charge_count)} Laden "
                f"({self._fmt_ct(total_charge_kwh)} kWh, davon "
                f"{self._fmt_ct(grid_charge_kwh)} kWh Netz)"
            )
        if discharge_count:
            parts.append(f"{_fmt_duration(discharge_count)} Entladen")
        self._plan_summary = " | ".join(parts) if parts else "Kein Plan erstellt"

        self._estimated_savings = round(max(0, actual_profit), 2)

        _LOGGER.debug(
            "Battery plan (DP): %s | Profit: %.2f EUR "
            "(SOC: %.0f%%, Solar: %.1f kWh, Efficiency: %.0f%%)",
            self._plan_summary,
            actual_profit,
            current_soc,
            self._expected_solar_kwh,
            efficiency * 100,
        )

    @staticmethod
    def _fmt_ct(value: float) -> str:
        """Format a ct/kWh value with German decimal comma."""
        return f"{value:.1f}".replace(".", ",")

    def _build_plan_reason(
        self,
        action: str,
        h: dict,
        slot_idx: int,
        presolar_set: set[int],
        hourly_data: list[dict],
        actions: list[str],
    ) -> str:
        """Build a human-readable reason for a plan action."""
        fc = self._fmt_ct
        eff_pct = f", \u03b7={self._battery_efficiency*100:.0f}%" if self._battery_efficiency < 0.99 else ""
        cycle_info = f", Zyklus {fc(self._cycle_cost)}ct" if self._cycle_cost > 0 else ""

        if action == "charge" and h["solar_surplus_kwh"] > 0.05:
            grid_pct = round(h["grid_fraction"] * 100)
            return (
                f"Solar+Netz ({grid_pct}% Netz \u00e0 {fc(h['price']*100)} ct "
                f"\u2192 eff. {fc(h['effective_charge_cost']*100)} ct/kWh{cycle_info})"
            )
        if action == "charge":
            return f"Netz-Laden ({fc(h['price']*100)} ct/kWh{cycle_info})"
        if action == "solar_charge":
            return f"Solar {fc(h['solar_surplus_kwh'])} kWh (kostenlos)"
        if action == "discharge":
            if slot_idx in presolar_set:
                return f"Platz f\u00fcr Solar schaffen ({fc(h['price']*100)} ct/kWh)"
            nearby_charge = None
            for j in range(len(actions)):
                if actions[j] == "charge" and j != slot_idx:
                    nearby_charge = hourly_data[j]
                    break
            if nearby_charge:
                spread = (h["price"] - nearby_charge["effective_charge_cost"]) * 100
                eff_loss = nearby_charge["effective_charge_cost"] * 100 * (1 - self._battery_efficiency)
                net_spread = spread - self._cycle_cost - eff_loss
                return (
                    f"Entladen ({fc(h['price']*100)} ct, "
                    f"Spread {fc(spread)} ct, netto {fc(net_spread)} ct{eff_pct})"
                )
            return f"Entladen ({fc(h['price']*100)} ct/kWh{eff_pct})"
        if action == "hold":
            return "Halten f\u00fcr teure Stunden"
        return "Keine Aktion"

    def _build_slot_data(self, now: datetime) -> tuple[list[dict], float]:
        """Build list of time slots from price forecast.

        Supports both hourly and sub-hourly (e.g. 15-min) price data.
        Returns (slots, slot_duration_hours).
        """
        now_str = now.strftime("%Y-%m-%dT%H:%M")
        slots = []

        for p in self._price_forecast:
            try:
                start = datetime.fromisoformat(p["start"])
                if start.tzinfo is not None:
                    start = dt_util.as_local(start)
                slot_key = start.strftime("%Y-%m-%dT%H:%M")
                if slot_key >= now_str:
                    slots.append({
                        "slot_key": slot_key,
                        "hour_key": start.strftime("%Y-%m-%dT%H"),
                        "hour_of_day": start.hour,
                        "price": p.get("total", 0),
                        "start_dt": start,
                    })
            except (ValueError, TypeError):
                continue

        # Deduplicate (keep first occurrence)
        seen = set()
        unique = []
        for s in slots:
            if s["slot_key"] not in seen:
                seen.add(s["slot_key"])
                unique.append(s)

        # Detect slot duration from data (15min, 30min, or 60min)
        slot_duration_h = 1.0
        if len(unique) >= 2:
            dt1 = unique[0]["start_dt"]
            dt2 = unique[1]["start_dt"]
            delta_min = (dt2 - dt1).total_seconds() / 60
            if 10 <= delta_min <= 20:
                slot_duration_h = 0.25  # 15 min
            elif 25 <= delta_min <= 35:
                slot_duration_h = 0.5   # 30 min
            # else: default 1h

        # Log multi-day coverage
        if unique:
            first_dt = unique[0]["start_dt"]
            last_dt = unique[-1]["start_dt"]
            hours_covered = (last_dt - first_dt).total_seconds() / 3600
            days_covered = hours_covered / 24
            _LOGGER.debug(
                "Price slots: %d entries, %.0f min resolution, %.0fh coverage (%.1f days)",
                len(unique), slot_duration_h * 60, hours_covered, days_covered,
            )
        else:
            _LOGGER.debug("Price slots: 0 entries")

        return unique, slot_duration_h

    def _get_current_plan_action(self) -> str | None:
        """Get the planned action for the current time slot.

        Detects slot duration from the plan, rounds current time down to
        the nearest slot boundary, and does a simple string match.
        """
        if not self._battery_plan:
            return None

        now = dt_util.now()

        # Detect slot duration from first two entries
        slot_minutes = 60
        if len(self._battery_plan) >= 2:
            h1 = self._battery_plan[0]["hour"]
            h2 = self._battery_plan[1]["hour"]
            try:
                d1 = datetime.fromisoformat(h1)
                d2 = datetime.fromisoformat(h2)
                diff = int((d2 - d1).total_seconds() / 60)
                if 10 <= diff <= 60:
                    slot_minutes = diff
            except (ValueError, TypeError):
                pass

        # Round current time down to slot boundary
        minute = (now.minute // slot_minutes) * slot_minutes
        now_key = now.strftime("%Y-%m-%dT%H:") + f"{minute:02d}"

        # Simple string match
        for entry in self._battery_plan:
            if entry["hour"] == now_key:
                return entry["action"]

        # Fallback: match by hour only (for hourly plans without :MM)
        now_hour = now.strftime("%Y-%m-%dT%H")
        for entry in self._battery_plan:
            if entry["hour"].startswith(now_hour):
                return entry["action"]

        # Fallback: if current time is just before the plan starts,
        # use the first plan entry's action (avoids threshold-based
        # charging when the plan says hold).
        if self._battery_plan:
            first_time = self._battery_plan[0]["hour"]
            try:
                first_dt = datetime.fromisoformat(first_time)
                if hasattr(now, "tzinfo") and now.tzinfo and not first_dt.tzinfo:
                    first_dt = first_dt.replace(tzinfo=now.tzinfo)
                diff_min = (first_dt - now).total_seconds() / 60
                if 0 < diff_min <= slot_minutes:
                    return self._battery_plan[0]["action"]
            except (ValueError, TypeError):
                pass

        return None

    @staticmethod
    def _to_hour_key(dt_str: str) -> str:
        """Convert a datetime string to an hour key like '2024-01-15T14'.

        Always normalizes to local time so that keys from different sources
        (Tibber in UTC+1, Forecast.Solar in UTC, etc.) match correctly.

        Naive datetime strings (no timezone) are treated as local time,
        since Forecast.Solar's watt_hours_period attribute keys are
        local-time strings when accessed via the HA state API.
        """
        dt = datetime.fromisoformat(str(dt_str))
        if dt.tzinfo is not None:
            dt = dt_util.as_local(dt)
        return dt.strftime("%Y-%m-%dT%H")

    def _find_cheap_hours(self, count: int = 4) -> list[dict]:
        """Find the cheapest upcoming hours from the forecast."""
        now = dt_util.now()
        future_prices = []
        for p in self._price_forecast:
            try:
                start = datetime.fromisoformat(p["start"])
                if start >= now:
                    future_prices.append(p)
            except (ValueError, TypeError):
                continue

        future_prices.sort(key=lambda x: x.get("total", 999))
        return future_prices[:count]

    def _find_expensive_hours(self, count: int = 4) -> list[dict]:
        """Find the most expensive upcoming hours from the forecast."""
        now = dt_util.now()
        future_prices = []
        for p in self._price_forecast:
            try:
                start = datetime.fromisoformat(p["start"])
                if start >= now:
                    future_prices.append(p)
            except (ValueError, TypeError):
                continue

        future_prices.sort(key=lambda x: x.get("total", 0), reverse=True)
        return future_prices[:count]

    def _volatility_adjusted_quantile(self, prices: list[float], target_q: float) -> float:
        """Calculate a volatility-adjusted price quantile.

        On volatile days (large spread), use wider quantile bands to capture
        the full price range.  On calm days (tight spread), compress the bands
        so that even small differences trigger charge/discharge.

        The adjustment scales the quantile towards the edges when volatility
        is high (coefficient of variation > 15%) and towards the median when
        low (< 10%).
        """
        if not prices:
            return 0.0
        sorted_p = sorted(prices)
        mean = sum(sorted_p) / len(sorted_p)
        if mean <= 0:
            idx = int(target_q * (len(sorted_p) - 1))
            return sorted_p[idx]

        variance = sum((p - mean) ** 2 for p in sorted_p) / len(sorted_p)
        std_dev = variance ** 0.5
        cv = std_dev / mean  # coefficient of variation

        # Adjust quantile: low volatility → more aggressive (push towards edges)
        # high volatility → standard quantile
        if cv < 0.10:
            # Calm day: widen the band (charge more aggressively, discharge earlier)
            if target_q < 0.5:
                adjusted_q = target_q * 1.3  # e.g., 0.33 → 0.43
            else:
                adjusted_q = 1.0 - (1.0 - target_q) * 1.3  # e.g., 0.67 → 0.57
        elif cv > 0.20:
            # Volatile day: tighten the band (be more selective)
            if target_q < 0.5:
                adjusted_q = target_q * 0.7  # e.g., 0.33 → 0.23
            else:
                adjusted_q = 1.0 - (1.0 - target_q) * 0.7  # e.g., 0.67 → 0.77
        else:
            adjusted_q = target_q

        adjusted_q = max(0.0, min(1.0, adjusted_q))
        idx = int(adjusted_q * (len(sorted_p) - 1))
        return sorted_p[idx]

    def _is_in_cheap_window(self) -> bool:
        """Check if current time falls within a cheap price window."""
        if self._current_price is None:
            return False

        # Dynamic threshold with volatility adjustment
        if self._price_forecast:
            prices = [p.get("total", 0) for p in self._price_forecast]
            if prices:
                threshold = self._volatility_adjusted_quantile(prices, 1 / 3)
                return self._current_price <= threshold

        # Fallback: use summary price range from entity attributes
        if self._fallback_price_range:
            price_range = self._fallback_price_range
            low_third = price_range["min"] + (price_range["max"] - price_range["min"]) / 3
            return self._current_price <= low_third

        return self._current_price <= self._price_low / 100

    def _is_in_expensive_window(self) -> bool:
        """Check if current time falls within an expensive price window."""
        if self._current_price is None:
            return False

        # Dynamic threshold with volatility adjustment
        if self._price_forecast:
            prices = [p.get("total", 0) for p in self._price_forecast]
            if prices:
                threshold = self._volatility_adjusted_quantile(prices, 2 / 3)
                return self._current_price >= threshold

        # Fallback: use summary price range from entity attributes
        if self._fallback_price_range:
            price_range = self._fallback_price_range
            high_third = price_range["min"] + (price_range["max"] - price_range["min"]) * 2 / 3
            return self._current_price >= high_third

        return self._current_price >= self._price_high / 100

    async def force_charge(self) -> None:
        """Force battery into charging mode."""
        self._strategy = STRATEGY_MANUAL
        await self._start_charging()

    async def force_discharge(self) -> None:
        """Force battery into discharging mode."""
        self._strategy = STRATEGY_MANUAL
        await self._start_discharging()

    async def stop_all(self) -> None:
        """Turn off all chargers and inverters."""
        for i, charger in enumerate(self._chargers):
            if charger.get("type") == CHARGER_TYPE_DIMMER:
                # Komplettes Stop: Power 0 + optionaler Enable-Switch off.
                await self._set_dimmer_power(i, 0)
                enable_switch = charger.get("switch", "")
                if enable_switch:
                    await self.hass.services.async_call(
                        "switch", "turn_off", {"entity_id": enable_switch}
                    )
                continue
            if charger["switch"]:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": charger["switch"]}
                )
                charger["active"] = False

        if self._inverter_switch:
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": self._inverter_switch}
            )
        await self._set_inverter_power(0)
        self._inverter_active = False

        self._operating_mode = MODE_IDLE

    async def reset_dimmer_on_start(self) -> None:
        """At integration startup, force any dimmer charger to 0 W.

        Prevents a stale pre-restart setpoint from immediately drawing
        power before the optimizer / opportunistic logic has decided.
        """
        for i, charger in enumerate(self._chargers):
            if charger.get("type") == CHARGER_TYPE_DIMMER:
                _LOGGER.info("Startup: resetting dimmer C%d to 0 W", i + 1)
                await self._set_dimmer_power(i, 0)

    def apply_options(self, options: dict) -> None:
        """Apply updated options from the options flow (live, no restart needed)."""
        self._tibber_price_entity = options.get(
            CONF_TIBBER_PRICE_ENTITY, self._tibber_price_entity
        )
        self._tibber_prices_entity = options.get(
            CONF_TIBBER_PRICES_ENTITY, self._tibber_prices_entity
        )
        self._pulse_consumption_entity = options.get(
            CONF_TIBBER_PULSE_CONSUMPTION_ENTITY, self._pulse_consumption_entity
        )
        self._pulse_production_entity = options.get(
            CONF_TIBBER_PULSE_PRODUCTION_ENTITY, self._pulse_production_entity
        )
        if CONF_CHARGERS in options:
            self._chargers = [
                self._build_charger_entry(c) for c in options[CONF_CHARGERS]
            ]
        self._inverter_switch = options.get(
            CONF_INVERTER_FEED_SWITCH, self._inverter_switch
        )
        self._inverter_power = options.get(
            CONF_INVERTER_FEED_POWER, self._inverter_power
        )
        self._inverter_power_entity = options.get(
            CONF_INVERTER_FEED_POWER_ENTITY, self._inverter_power_entity
        )
        self._inverter_actual_power_entity = options.get(
            CONF_INVERTER_FEED_ACTUAL_POWER_ENTITY, self._inverter_actual_power_entity
        )
        self._battery_soc_entity = options.get(
            CONF_BATTERY_SOC_ENTITY, self._battery_soc_entity
        )
        self._battery_capacity = options.get(
            CONF_BATTERY_CAPACITY_KWH, self._battery_capacity
        )
        self._min_soc = options.get(CONF_MIN_SOC, self._min_soc)
        self._max_soc = options.get(CONF_MAX_SOC, self._max_soc)
        self._price_low = options.get(
            CONF_PRICE_LOW_THRESHOLD, self._price_low
        )
        self._price_high = options.get(
            CONF_PRICE_HIGH_THRESHOLD, self._price_high
        )
        self._solar_forecast_entity = options.get(
            CONF_SOLAR_FORECAST_ENTITY, self._solar_forecast_entity
        )
        self._solar_forecast_entities = options.get(
            CONF_SOLAR_FORECAST_ENTITIES, self._solar_forecast_entities
        )
        self._solar_power_entity = options.get(
            CONF_SOLAR_POWER_ENTITY, self._solar_power_entity
        )
        self._solar_energy_today_entity = options.get(
            CONF_SOLAR_ENERGY_TODAY_ENTITY, self._solar_energy_today_entity
        )
        self._house_consumption_w = options.get(
            CONF_HOUSE_CONSUMPTION_W, self._house_consumption_w
        )
        self._cycle_cost = options.get(
            CONF_BATTERY_CYCLE_COST, self._cycle_cost
        )
        eff_pct = options.get(CONF_BATTERY_EFFICIENCY)
        if eff_pct is not None:
            self._battery_efficiency = eff_pct / 100.0
        self._epex_enabled = bool(options.get(
            CONF_EPEX_PREDICTOR_ENABLED, self._epex_enabled
        ))
        self._epex_region = options.get(
            CONF_EPEX_PREDICTOR_REGION, self._epex_region
        )
        self._outside_temp_entity = options.get(
            CONF_OUTSIDE_TEMPERATURE_ENTITY, self._outside_temp_entity
        )
        self._battery_voltage_entity = options.get(
            CONF_BATTERY_VOLTAGE_ENTITY, self._battery_voltage_entity
        )
        self._battery_current_entity = options.get(
            CONF_BATTERY_CURRENT_ENTITY, self._battery_current_entity
        )
        _LOGGER.info("Configuration updated from options flow")

    def set_strategy(self, strategy: str) -> None:
        """Set the operating strategy."""
        if strategy in (STRATEGY_PRICE_OPTIMIZED, STRATEGY_SELF_CONSUMPTION, STRATEGY_MANUAL):
            self._strategy = strategy
            _LOGGER.info("Strategy changed to: %s", strategy)

    def stop(self) -> None:
        """Stop the coordinator and remove listeners."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()

    def _build_data(self) -> dict[str, Any]:
        """Build the data dict exposed to entities."""
        cheap_hours = self._find_cheap_hours(3)
        expensive_hours = self._find_expensive_hours(3)

        return {
            ATTR_CURRENT_PRICE: self._current_price,
            ATTR_OPERATING_MODE: self._operating_mode,
            ATTR_STRATEGY: self._strategy,
            ATTR_ESTIMATED_SAVINGS: self._estimated_savings,
            ATTR_NEXT_CHEAP_WINDOW: (
                cheap_hours[0]["start"] if cheap_hours else None
            ),
            ATTR_NEXT_EXPENSIVE_WINDOW: (
                expensive_hours[0]["start"] if expensive_hours else None
            ),
            ATTR_BATTERY_PLAN: self._battery_plan,
            ATTR_PLAN_SUMMARY: self._plan_summary,
            ATTR_EXPECTED_SOLAR_KWH: self._expected_solar_kwh,
            "planned_action": self._get_current_plan_action(),
            "battery_soc": self._battery_soc,
            "grid_power": self._grid_power,
            "chargers": [
                {
                    "index": i, "switch": c["switch"], "power": c["power"],
                    "active": c["active"],
                    "measured_power": c.get("measured_power"),
                    "type": c.get("type", CHARGER_TYPE_SWITCH),
                    "target_power": c.get("target_power", 0.0),
                    "min_power": c.get("min_power", 0),
                }
                for i, c in enumerate(self._chargers)
            ],
            "inverter_active": self._inverter_active,
            "inverter_target_power": self._inverter_target_power,
            "inverter_actual_power": self._inverter_actual_power,
            "price_forecast": self._price_forecast,
            "cheap_hours": cheap_hours,
            "expensive_hours": expensive_hours,
            "min_soc": self._min_soc,
            "max_soc": self._max_soc,
            "grid_max_soc": self._dp_max_soc,
            "solar_headroom_pct": self._solar_headroom_pct,
            "solar_power_w": self._solar_power,
            "solar_surplus_w": self._calculate_true_solar_surplus(),
            "solar_forecast_keys": dict(sorted(
                ((k, v) for k, v in self._solar_forecast.items()),
            )[:8]) if self._solar_forecast else None,
            "battery_capacity_kwh": self._battery_capacity,
            "allow_grid_charging": self._allow_grid_charging,
            "allow_discharging": self._allow_discharging,
            "allow_solar_charging": self._allow_solar_charging,
            "use_solar_forecast": self._use_solar_forecast,
            "solar_power": self._solar_power,
            "solar_calibration_factor": self._solar_calibration_factor,
            "intraday_solar_factor": self._intraday_solar_factor,
            "kalman_gain": round(getattr(self, "_kalman_p", 0.1) / (getattr(self, "_kalman_p", 0.1) + 0.05), 3),
            "battery_efficiency": round(self._battery_efficiency * 100),
            "measured_roundtrip_efficiency": round(self._measured_roundtrip_efficiency * 100, 1) if self._measured_roundtrip_efficiency else None,
            "measured_charge_efficiency": round(self._measured_charge_efficiency * 100, 1) if self._measured_charge_efficiency else None,
            "measured_discharge_efficiency": round(self._measured_discharge_efficiency * 100, 1) if self._measured_discharge_efficiency else None,
            "efficiency_charge_kwh": round(self._eff_charge_grid_kwh, 3),
            "efficiency_discharge_kwh": round(self._eff_discharge_battery_kwh, 3),
            "efficiency_history": self._eff_history,
            "efficiency_rolling_days": self._eff_rolling_days,
            "cycle_cost_ct": self._cycle_cost,
            "consumption_forecast": self.get_hourly_consumption_forecast(),
            "optimization_log": list(self._optimization_log),
            "epex_markup": self._epex_markup,
            "epex_terminal_value_ct": round(
                getattr(self, "_epex_terminal_value_per_kwh", 0) * 100, 1
            ),
            "epex_visualization": getattr(self, "_epex_visualization", []),
            "outside_temperature": self._outside_temp,
            "battery_real_power_w": round(self._battery_real_power, 1) if self._battery_real_power else None,
            "battery_voltage": round(self._battery_voltage, 2) if self._battery_voltage else None,
            "battery_current": round(self._battery_current, 2) if self._battery_current else None,
            "version": self._version,
            "source_hash": self._source_hash,
        }

    # Properties for entities
    @property
    def chargers(self) -> list[dict]:
        """List of configured charger devices with their state."""
        return self._chargers

    @property
    def strategy(self) -> str:
        """Current operating strategy (price_optimized, self_consumption, manual)."""
        return self._strategy

    @property
    def operating_mode(self) -> str:
        """Current operating mode (idle, charging, discharging, solar_charging)."""
        return self._operating_mode

    @property
    def current_price(self) -> float | None:
        """Current electricity price in EUR/kWh."""
        return self._current_price

    @property
    def battery_soc(self) -> float | None:
        """Current battery state of charge in percent."""
        return self._battery_soc

    @property
    def grid_power(self) -> float | None:
        """Net grid power in watts (positive=import, negative=export)."""
        return self._grid_power

    @property
    def min_soc(self) -> int:
        """Minimum allowed battery SOC in percent."""
        return self._min_soc

    @min_soc.setter
    def min_soc(self, value: int) -> None:
        """Set minimum SOC, clamped to 0-100."""
        self._min_soc = max(0, min(100, value))

    @property
    def max_soc(self) -> int:
        """Maximum allowed battery SOC in percent."""
        return self._max_soc

    @max_soc.setter
    def max_soc(self, value: int) -> None:
        """Set maximum SOC, clamped to 0-100."""
        self._max_soc = max(0, min(100, value))

    @property
    def price_low_threshold(self) -> float:
        """Low price threshold for charging decisions in ct/kWh."""
        return self._price_low

    @price_low_threshold.setter
    def price_low_threshold(self, value: float) -> None:
        """Set low price threshold."""
        self._price_low = value

    @property
    def price_high_threshold(self) -> float:
        """High price threshold for discharging decisions in ct/kWh."""
        return self._price_high

    @price_high_threshold.setter
    def price_high_threshold(self, value: float) -> None:
        """Set high price threshold."""
        self._price_high = value

    @property
    def allow_grid_charging(self) -> bool:
        """Whether grid charging is allowed."""
        return self._allow_grid_charging

    @allow_grid_charging.setter
    def allow_grid_charging(self, value: bool) -> None:
        """Enable or disable grid charging."""
        self._allow_grid_charging = value
        _LOGGER.info("Grid charging %s", "enabled" if value else "disabled")

    @property
    def allow_discharging(self) -> bool:
        """Whether battery discharging is allowed."""
        return self._allow_discharging

    @allow_discharging.setter
    def allow_discharging(self, value: bool) -> None:
        """Enable or disable battery discharging."""
        self._allow_discharging = value
        _LOGGER.info("Discharging %s", "enabled" if value else "disabled")

    @property
    def allow_solar_charging(self) -> bool:
        """Whether solar-surplus absorption (charging from PV) is allowed."""
        return self._allow_solar_charging

    @allow_solar_charging.setter
    def allow_solar_charging(self, value: bool) -> None:
        """Enable or disable solar-surplus absorption (zero-export master)."""
        self._allow_solar_charging = value
        _LOGGER.info("Solar charging %s", "enabled" if value else "disabled")

    @property
    def use_solar_forecast(self) -> bool:
        """Whether solar forecast is used in optimization."""
        return self._use_solar_forecast

    @use_solar_forecast.setter
    def use_solar_forecast(self, value: bool) -> None:
        """Enable or disable solar forecast usage."""
        self._use_solar_forecast = value
        _LOGGER.info("Solar forecast %s", "enabled" if value else "disabled")
