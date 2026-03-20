"""Coordinator for Battery Storage Manager."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

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
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CHARGER_1_POWER,
    CONF_CHARGER_1_SWITCH,
    CONF_CHARGER_2_POWER,
    CONF_CHARGER_2_SWITCH,
    CONF_HOUSE_CONSUMPTION_W,
    CONF_INVERTER_FEED_ACTUAL_POWER_ENTITY,
    CONF_INVERTER_FEED_POWER,
    CONF_INVERTER_FEED_POWER_ENTITY,
    CONF_INVERTER_FEED_SWITCH,
    CONF_MAX_SOC,
    CONF_MIN_SOC,
    CONF_PRICE_HIGH_THRESHOLD,
    CONF_PRICE_LOW_THRESHOLD,
    CONF_SOLAR_FORECAST_ENTITIES,
    CONF_SOLAR_FORECAST_ENTITY,
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
    STRATEGY_MANUAL,
    STRATEGY_PRICE_OPTIMIZED,
    STRATEGY_SELF_CONSUMPTION,
)

_LOGGER = logging.getLogger(__name__)


class BatteryStorageCoordinator(DataUpdateCoordinator):
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

        # Entity IDs from config
        self._tibber_price_entity = self._config.get(CONF_TIBBER_PRICE_ENTITY, "")
        self._tibber_prices_entity = self._config.get(CONF_TIBBER_PRICES_ENTITY, "")
        self._pulse_consumption_entity = self._config.get(CONF_TIBBER_PULSE_CONSUMPTION_ENTITY, "")
        self._pulse_production_entity = self._config.get(CONF_TIBBER_PULSE_PRODUCTION_ENTITY, "")
        self._charger_1_switch = self._config.get(CONF_CHARGER_1_SWITCH, "")
        self._charger_2_switch = self._config.get(CONF_CHARGER_2_SWITCH, "")
        self._charger_1_power = self._config.get(CONF_CHARGER_1_POWER, 0)
        self._charger_2_power = self._config.get(CONF_CHARGER_2_POWER, 0)
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
        self._house_consumption_w = self._config.get(
            CONF_HOUSE_CONSUMPTION_W, DEFAULT_HOUSE_CONSUMPTION_W
        )

        # State
        self._strategy = STRATEGY_PRICE_OPTIMIZED
        self._operating_mode = MODE_IDLE
        self._current_price: float | None = None
        self._price_forecast: list[dict] = []
        self._battery_soc: float | None = None
        self._grid_power: float | None = None  # positive = import, negative = export
        self._estimated_savings: float = 0.0
        self._unsub_listeners: list = []
        self._charger_1_active = False
        self._charger_2_active = False
        self._inverter_active = False
        self._inverter_target_power: float = 0  # current target power for zero-feed
        self._inverter_actual_power: float | None = None  # actual power from sensor

        # Runtime toggles
        self._allow_grid_charging = True
        self._allow_discharging = True
        self._use_solar_forecast = bool(
            self._solar_forecast_entity or self._solar_forecast_entities
        )

        # Solar forecast and battery plan
        self._solar_forecast: dict[str, float] = {}  # hour_key -> Wh expected
        self._battery_plan: list[dict] = []  # hourly plan entries
        self._plan_summary: str = ""
        self._expected_solar_kwh: float = 0.0

        # Track whether entities have ever been seen (for startup race condition)
        self._price_entity_seen = False
        self._prices_entity_seen = False
        self._fallback_price_range: dict | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and process data, then decide on battery action."""
        _LOGGER.debug(
            "Update cycle - Price entity: '%s', Prices entity: '%s'",
            self._tibber_price_entity,
            self._tibber_prices_entity,
        )
        self._read_sensor_states()
        await self._update_price_forecast()
        await self._read_solar_forecast()
        self._create_battery_plan()

        if self._strategy == STRATEGY_PRICE_OPTIMIZED:
            await self._run_price_optimization()
        elif self._strategy == STRATEGY_SELF_CONSUMPTION:
            await self._run_self_consumption()
        # STRATEGY_MANUAL: no automatic actions

        return self._build_data()

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

        _LOGGER.debug("Price forecast built with %d entries", len(self._price_forecast))

    # ── Solar forecast ──────────────────────────────────────────────

    async def _read_solar_forecast(self) -> None:
        """Read solar production forecast from configured entities.

        Supports multiple entities (e.g. multiple roof arrays, mixed services).
        All forecasts are summed together per hour.

        Supported formats per entity:
        - Forecast.Solar energy platform (wh_hours via config entry)
        - Sensor attributes: watt_hours_period {ISO_datetime: Wh}
        - Solcast / generic: forecast attribute [{period_start, pv_estimate}, ...]
        - Sensor attributes: watt_hours (cumulative)
        """
        self._solar_forecast = {}
        self._expected_solar_kwh = 0.0

        if not self._use_solar_forecast:
            return

        # Collect all configured solar forecast entity IDs
        entity_ids: list[str] = []
        if self._solar_forecast_entity:
            entity_ids.append(self._solar_forecast_entity)
        for eid in self._solar_forecast_entities:
            if eid and eid not in entity_ids:
                entity_ids.append(eid)

        if not entity_ids:
            return

        # Try to read from the HA energy platform first (works for forecast_solar)
        energy_data_found = await self._read_energy_solar_forecasts(entity_ids)

        # For entities not covered by energy platform, fall back to attributes
        for entity_id in entity_ids:
            if entity_id not in energy_data_found:
                self._read_single_solar_forecast(entity_id)

        if self._solar_forecast:
            _LOGGER.debug(
                "Combined solar forecast from %d entities: %d hours, total %.1f kWh",
                len(entity_ids),
                len(self._solar_forecast),
                sum(self._solar_forecast.values()) / 1000,
            )

        now = dt_util.now()
        now_key = now.strftime("%Y-%m-%dT%H")
        remaining = {k: v for k, v in self._solar_forecast.items() if k >= now_key}
        self._expected_solar_kwh = sum(remaining.values()) / 1000

    async def _read_energy_solar_forecasts(
        self, entity_ids: list[str]
    ) -> set[str]:
        """Try to read solar forecasts via the HA energy platform.

        This works for integrations like forecast_solar that expose data
        through the energy platform but not through sensor attributes.

        Returns set of entity_ids that were successfully read.
        """
        covered: set[str] = set()

        # Map entity_ids to their config entries
        entity_registry = er.async_get(self.hass)
        entries_to_fetch: dict[str, list[str]] = {}  # config_entry_id -> [entity_ids]

        for entity_id in entity_ids:
            entry = entity_registry.async_get(entity_id)
            if not entry or not entry.config_entry_id:
                continue
            config_entry = self.hass.config_entries.async_get_entry(
                entry.config_entry_id
            )
            if not config_entry or config_entry.domain != "forecast_solar":
                continue
            entries_to_fetch.setdefault(entry.config_entry_id, []).append(
                entity_id
            )

        if not entries_to_fetch:
            return covered

        for config_entry_id, eids in entries_to_fetch.items():
            try:
                config_entry = self.hass.config_entries.async_get_entry(
                    config_entry_id
                )
                if (
                    not config_entry
                    or not hasattr(config_entry, "runtime_data")
                    or config_entry.runtime_data is None
                ):
                    continue

                # forecast_solar stores an Estimate object in runtime_data
                estimate = config_entry.runtime_data
                wh_period = getattr(estimate, "wh_period", None)
                if not wh_period:
                    # Try .data.wh_period for wrapped data
                    data = getattr(estimate, "data", None)
                    if data:
                        wh_period = getattr(data, "wh_period", None)

                if not isinstance(wh_period, dict) or not wh_period:
                    _LOGGER.debug(
                        "forecast_solar entry %s: no wh_period data found",
                        config_entry_id,
                    )
                    continue

                for dt_obj, wh in wh_period.items():
                    try:
                        if isinstance(dt_obj, datetime):
                            local_dt = dt_util.as_local(dt_obj) if dt_obj.tzinfo is not None else dt_obj
                            hour_key = local_dt.strftime("%Y-%m-%dT%H")
                        else:
                            hour_key = self._to_hour_key(str(dt_obj))
                        self._solar_forecast[hour_key] = (
                            self._solar_forecast.get(hour_key, 0) + float(wh)
                        )
                    except (ValueError, TypeError):
                        continue

                covered.update(eids)
                _LOGGER.debug(
                    "Solar forecast from forecast_solar entry %s "
                    "(energy platform): %d entries",
                    config_entry_id,
                    len(wh_period),
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Could not read forecast_solar energy data for %s",
                    config_entry_id,
                    exc_info=True,
                )

        return covered

    def _read_single_solar_forecast(self, entity_id: str) -> None:
        """Read solar forecast from a single entity and add to combined forecast."""
        state = self.hass.states.get(entity_id)
        if not state:
            _LOGGER.debug("Solar forecast entity '%s' not found", entity_id)
            return

        attrs = state.attributes
        parsed = False

        # Format 1: Forecast.Solar watt_hours_period {datetime_str: Wh}
        wh_period = attrs.get("watt_hours_period")
        if isinstance(wh_period, dict) and wh_period:
            for dt_str, wh in wh_period.items():
                try:
                    hour_key = self._to_hour_key(dt_str)
                    self._solar_forecast[hour_key] = (
                        self._solar_forecast.get(hour_key, 0) + float(wh)
                    )
                except (ValueError, TypeError):
                    continue
            parsed = True
            _LOGGER.debug(
                "Solar forecast '%s' (watt_hours_period): %d entries",
                entity_id,
                len(wh_period),
            )

        # Format 2: Solcast / generic forecast list
        if not parsed:
            forecast_list = attrs.get("forecast") or attrs.get("detailedForecast")
            if isinstance(forecast_list, list) and forecast_list:
                for entry in forecast_list:
                    if not isinstance(entry, dict):
                        continue
                    dt_str = entry.get("period_start") or entry.get("datetime") or entry.get("start")
                    pv = entry.get("pv_estimate") or entry.get("pv_estimate10") or entry.get("power_production")
                    if dt_str and pv is not None:
                        try:
                            hour_key = self._to_hour_key(str(dt_str))
                            self._solar_forecast[hour_key] = (
                                self._solar_forecast.get(hour_key, 0) + float(pv) * 1000
                            )
                        except (ValueError, TypeError):
                            continue
                parsed = True
                _LOGGER.debug(
                    "Solar forecast '%s' (forecast list): %d entries",
                    entity_id,
                    len(forecast_list),
                )

        # Format 3: Forecast.Solar watt_hours (cumulative) → derive per-period
        if not parsed:
            wh_cum = attrs.get("watt_hours")
            if isinstance(wh_cum, dict) and len(wh_cum) > 1:
                sorted_entries = sorted(wh_cum.items())
                for i in range(1, len(sorted_entries)):
                    dt_str, cum_wh = sorted_entries[i]
                    prev_wh = sorted_entries[i - 1][1]
                    try:
                        hour_key = self._to_hour_key(dt_str)
                        delta = float(cum_wh) - float(prev_wh)
                        if delta > 0:
                            self._solar_forecast[hour_key] = (
                                self._solar_forecast.get(hour_key, 0) + delta
                            )
                    except (ValueError, TypeError):
                        continue
                _LOGGER.debug(
                    "Solar forecast '%s' (watt_hours cumulative): %d entries",
                    entity_id,
                    len(sorted_entries),
                )

    @staticmethod
    def _to_hour_key(dt_str: str) -> str:
        """Convert a datetime string to an hour key like '2024-01-15T14'.

        Always normalizes to local time so that keys from different sources
        (Tibber in UTC+1, Forecast.Solar in UTC, etc.) match correctly.
        """
        dt = datetime.fromisoformat(str(dt_str))
        if dt.tzinfo is not None:
            dt = dt_util.as_local(dt)
        return dt.strftime("%Y-%m-%dT%H")

    # ── Battery plan ────────────────────────────────────────────────

    def _create_battery_plan(self) -> None:
        """Create an optimized 24h battery charge/discharge plan.

        Combines price forecast, solar forecast, current SOC, and battery
        capacity to decide when to charge from grid, let solar charge,
        discharge, or hold.
        """
        now = dt_util.now()
        now_key = now.strftime("%Y-%m-%dT%H")

        # Build hourly data: price + solar for each upcoming hour
        hourly_data = self._build_hourly_data(now)
        if not hourly_data:
            self._battery_plan = []
            self._plan_summary = "Keine Preisdaten verfügbar"
            return

        # Battery parameters
        usable_kwh = (self._max_soc - self._min_soc) / 100 * self._battery_capacity
        current_soc = self._battery_soc if self._battery_soc is not None else 50.0
        current_stored_kwh = (current_soc - self._min_soc) / 100 * self._battery_capacity
        headroom_kwh = (self._max_soc - current_soc) / 100 * self._battery_capacity

        # Charge power (combined chargers)
        charge_power_w = (self._charger_1_power or 0) + (self._charger_2_power or 0)
        charge_kwh_per_hour = charge_power_w / 1000
        discharge_power_w = self._inverter_power or 800
        discharge_kwh_per_hour = discharge_power_w / 1000
        house_kwh_per_hour = self._house_consumption_w / 1000

        # Step 1: Estimate solar contribution per hour (net after house consumption)
        total_solar_charge_kwh = 0.0
        for h in hourly_data:
            solar_wh = self._solar_forecast.get(h["hour_key"], 0)
            h["solar_kwh"] = solar_wh / 1000
            # Net solar surplus after house consumption
            h["solar_surplus_kwh"] = max(0, h["solar_kwh"] - house_kwh_per_hour)
            total_solar_charge_kwh += min(h["solar_surplus_kwh"], charge_kwh_per_hour)

        # Solar will fill this much of the battery
        solar_fills_kwh = min(total_solar_charge_kwh, headroom_kwh)
        # How much we still need from grid
        grid_charge_needed_kwh = max(0, headroom_kwh - solar_fills_kwh)

        # Step 2: Determine how many grid-charge hours we need
        grid_charge_hours_needed = (
            int(grid_charge_needed_kwh / charge_kwh_per_hour) + 1
            if charge_kwh_per_hour > 0 and grid_charge_needed_kwh > 0
            else 0
        )

        # Step 3: Determine how many discharge hours we can sustain
        discharge_hours_available = (
            int(current_stored_kwh / discharge_kwh_per_hour)
            if discharge_kwh_per_hour > 0
            else 0
        )

        # Step 4: Sort hours by price to find optimal charge/discharge windows
        sorted_by_price = sorted(hourly_data, key=lambda h: h["price"])

        # Cheapest hours → charge candidates (only if no significant solar)
        charge_candidates = []
        for h in sorted_by_price:
            if h["solar_surplus_kwh"] < charge_kwh_per_hour * 0.5:
                charge_candidates.append(h["hour_key"])
            if len(charge_candidates) >= grid_charge_hours_needed:
                break

        # Most expensive hours → discharge candidates
        discharge_candidates = []
        sorted_expensive = sorted(hourly_data, key=lambda h: h["price"], reverse=True)
        for h in sorted_expensive:
            # Only discharge if price is significantly above average
            avg_price = sum(x["price"] for x in hourly_data) / len(hourly_data)
            if h["price"] >= avg_price and h["solar_surplus_kwh"] < house_kwh_per_hour * 0.5:
                discharge_candidates.append(h["hour_key"])
            if len(discharge_candidates) >= discharge_hours_available:
                break

        # Step 5: Build the plan with expected SOC tracking
        self._battery_plan = []
        charge_count = 0
        discharge_count = 0
        solar_count = 0
        estimated_soc = current_soc

        for h in hourly_data:
            key = h["hour_key"]
            entry = {
                "hour": key + ":00",
                "price": round(h["price"], 4),
                "solar_kwh": round(h["solar_kwh"], 2),
                "solar_surplus_kwh": round(h["solar_surplus_kwh"], 2),
                "expected_soc": round(estimated_soc, 1),
            }

            if h["solar_surplus_kwh"] > 0.05:
                # Solar surplus available → charge battery from solar via chargers
                entry["action"] = "solar_charge"
                entry["reason"] = f"Solarüberschuss {h['solar_surplus_kwh']:.1f} kWh"
                solar_count += 1
                delta_kwh = min(h["solar_surplus_kwh"], charge_kwh_per_hour)
                estimated_soc += delta_kwh / self._battery_capacity * 100
            elif key in charge_candidates:
                entry["action"] = "charge"
                entry["reason"] = f"Günstiger Strom ({h['price']*100:.1f} ct/kWh)"
                charge_count += 1
                estimated_soc += charge_kwh_per_hour / self._battery_capacity * 100
            elif key in discharge_candidates:
                entry["action"] = "discharge"
                entry["reason"] = f"Teurer Strom ({h['price']*100:.1f} ct/kWh)"
                discharge_count += 1
                estimated_soc -= discharge_kwh_per_hour / self._battery_capacity * 100
            else:
                # Check if expensive hours are still coming → hold charge
                remaining_expensive = [
                    d for d in discharge_candidates if d > key
                ]
                if remaining_expensive:
                    entry["action"] = "hold"
                    entry["reason"] = "Ladung halten für teure Stunden"
                else:
                    entry["action"] = "idle"
                    entry["reason"] = "Keine Aktion nötig"

            estimated_soc = max(self._min_soc, min(self._max_soc, estimated_soc))
            self._battery_plan.append(entry)

        # Summary
        parts = []
        if solar_count:
            parts.append(f"{solar_count}h Solar")
        if charge_count:
            parts.append(f"{charge_count}h Laden ({grid_charge_needed_kwh:.1f} kWh)")
        if discharge_count:
            parts.append(f"{discharge_count}h Entladen")
        self._plan_summary = " | ".join(parts) if parts else "Kein Plan erstellt"

        _LOGGER.debug(
            "Battery plan: %s (SOC: %.0f%%, Solar: %.1f kWh, Headroom: %.1f kWh)",
            self._plan_summary,
            current_soc,
            self._expected_solar_kwh,
            headroom_kwh,
        )

    def _build_hourly_data(self, now: datetime) -> list[dict]:
        """Build list of hourly data combining price and solar forecasts."""
        now_key = now.strftime("%Y-%m-%dT%H")
        hourly = []

        for p in self._price_forecast:
            try:
                start = datetime.fromisoformat(p["start"])
                if start.tzinfo is not None:
                    start = dt_util.as_local(start)
                hour_key = start.strftime("%Y-%m-%dT%H")
                if hour_key >= now_key:
                    hourly.append({
                        "hour_key": hour_key,
                        "price": p.get("total", 0),
                    })
            except (ValueError, TypeError):
                continue

        # Deduplicate (keep first occurrence)
        seen = set()
        unique = []
        for h in hourly:
            if h["hour_key"] not in seen:
                seen.add(h["hour_key"])
                unique.append(h)

        return unique

    def _get_current_plan_action(self) -> str | None:
        """Get the planned action for the current hour."""
        if not self._battery_plan:
            return None

        now_key = dt_util.now().strftime("%Y-%m-%dT%H")
        for entry in self._battery_plan:
            if entry["hour"].startswith(now_key):
                return entry["action"]
        return None

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

    def _is_in_cheap_window(self) -> bool:
        """Check if current time falls within a cheap price window."""
        if self._current_price is None:
            return False

        # Dynamic threshold: use the lower third of available prices
        if self._price_forecast:
            prices = [p.get("total", 0) for p in self._price_forecast]
            if prices:
                sorted_prices = sorted(prices)
                threshold_idx = len(sorted_prices) // 3
                dynamic_threshold = sorted_prices[threshold_idx]
                return self._current_price <= dynamic_threshold

        # Fallback: use summary price range from entity attributes
        if self._fallback_price_range:
            price_range = self._fallback_price_range
            # "Cheap" = in the lower third of today's price range
            low_third = price_range["min"] + (price_range["max"] - price_range["min"]) / 3
            return self._current_price <= low_third

        return self._current_price <= self._price_low / 100  # convert ct to EUR

    def _is_in_expensive_window(self) -> bool:
        """Check if current time falls within an expensive price window."""
        if self._current_price is None:
            return False

        # Dynamic threshold: use the upper third of available prices
        if self._price_forecast:
            prices = [p.get("total", 0) for p in self._price_forecast]
            if prices:
                sorted_prices = sorted(prices)
                threshold_idx = (len(sorted_prices) * 2) // 3
                dynamic_threshold = sorted_prices[threshold_idx]
                return self._current_price >= dynamic_threshold

        # Fallback: use summary price range from entity attributes
        if self._fallback_price_range:
            price_range = self._fallback_price_range
            # "Expensive" = in the upper third of today's price range
            high_third = price_range["min"] + (price_range["max"] - price_range["min"]) * 2 / 3
            return self._current_price >= high_third

        return self._current_price >= self._price_high / 100  # convert ct to EUR

    async def _run_price_optimization(self) -> None:
        """Main price optimization logic using the battery plan.

        If a plan exists, follow it. Otherwise fall back to simple
        threshold-based logic.
        """
        if self._battery_soc is None:
            _LOGGER.debug("Missing SOC data, staying idle")
            await self._set_mode_idle()
            return

        # Use plan-based decisions if a plan is available
        # Solar actions don't need price data, so check plan first
        planned_action = self._get_current_plan_action()
        if planned_action:
            await self._execute_plan_action(planned_action)
            return

        # Fallback: simple threshold-based logic (needs price data)
        if self._current_price is None:
            _LOGGER.debug("No plan and no price data, staying idle")
            await self._set_mode_idle()
            return

        is_cheap = self._is_in_cheap_window()
        is_expensive = self._is_in_expensive_window()

        if is_cheap and self._battery_soc < self._max_soc and self._allow_grid_charging:
            await self._start_charging()
        elif is_expensive and self._battery_soc > self._min_soc and self._allow_discharging:
            await self._start_discharging()
        else:
            await self._set_mode_idle()

    async def _execute_plan_action(self, action: str) -> None:
        """Execute the action from the battery plan for the current hour."""
        if action == "charge" and self._battery_soc < self._max_soc:
            if not self._allow_grid_charging:
                _LOGGER.debug("Plan action: CHARGE skipped (grid charging disabled)")
                await self._set_mode_idle()
                return
            _LOGGER.debug("Plan action: CHARGE (grid)")
            await self._start_charging()
        elif action == "discharge" and self._battery_soc > self._min_soc:
            if not self._allow_discharging:
                _LOGGER.debug("Plan action: DISCHARGE skipped (discharging disabled)")
                await self._set_mode_idle()
                return
            _LOGGER.debug("Plan action: DISCHARGE")
            await self._start_discharging()
        elif action == "solar_charge":
            # AC-coupled system: solar surplus flows through the house network
            # and needs the chargers to be ON to charge the battery.
            # grid_power already includes the draw of active chargers, so we
            # must add their power back to get the true solar surplus.
            true_surplus = self._calculate_true_solar_surplus()
            if (
                true_surplus is not None
                and true_surplus > 50
                and self._battery_soc < self._max_soc
            ):
                _LOGGER.debug(
                    "Plan action: SOLAR_CHARGE - charging from solar surplus "
                    "(grid_power=%.0fW, true_surplus=%.0fW)",
                    self._grid_power, true_surplus,
                )
                await self._start_solar_charging(true_surplus)
            elif (
                self._allow_discharging
                and self._grid_power is not None
                and self._grid_power > 50
                and not self._charger_1_active
                and not self._charger_2_active
                and self._battery_soc > self._min_soc
            ):
                _LOGGER.debug("Plan action: SOLAR_CHARGE - discharging to cover grid import")
                await self._start_discharging()
            else:
                _LOGGER.debug("Plan action: SOLAR_CHARGE - idle (no surplus/full)")
                await self._set_mode_idle()
        elif action == "hold":
            _LOGGER.debug("Plan action: HOLD - keeping charge for later")
            await self._set_mode_idle()
        else:
            _LOGGER.debug("Plan action: IDLE")
            await self._set_mode_idle()

    async def _run_self_consumption(self) -> None:
        """Self-consumption optimization.

        Always try to cover house demand from battery, charge when
        there is excess solar/grid production.
        """
        if self._battery_soc is None or self._grid_power is None:
            await self._set_mode_idle()
            return

        if self._grid_power > 50 and self._battery_soc > self._min_soc and self._allow_discharging:
            # House is importing from grid - discharge battery to cover it
            await self._start_discharging()
        elif self._grid_power < -50 and self._battery_soc < self._max_soc:
            # Excess production (exporting) - charge battery (solar, not grid)
            await self._start_charging()
        else:
            await self._set_mode_idle()

    async def _start_charging(self) -> None:
        """Activate chargers to charge the battery."""
        if self._operating_mode == MODE_CHARGING:
            return

        _LOGGER.info(
            "Starting battery charge (SOC: %.1f%%, Price: %.4f EUR/kWh)",
            self._battery_soc or 0,
            self._current_price or 0,
        )

        # Turn on chargers
        if self._charger_1_switch:
            await self.hass.services.async_call(
                "switch", "turn_on", {"entity_id": self._charger_1_switch}
            )
            self._charger_1_active = True

        if self._charger_2_switch:
            await self.hass.services.async_call(
                "switch", "turn_on", {"entity_id": self._charger_2_switch}
            )
            self._charger_2_active = True

        # Turn off inverter feed
        if self._inverter_switch:
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": self._inverter_switch}
            )
        await self._set_inverter_power(0)
        self._inverter_active = False

        self._operating_mode = MODE_CHARGING

    def _calculate_true_solar_surplus(self) -> float | None:
        """Calculate the true solar surplus, compensating for active charger draw.

        grid_power reflects what the meter sees *after* chargers are already
        drawing.  To know the real solar surplus we must add back the power
        that active chargers are consuming, and subtract any inverter feed.

        Example: grid_power = -200W, charger1 (500W) active
          → true surplus = 500 - (-200) = 700W
        """
        if self._grid_power is None:
            return None

        active_draw = 0
        if self._charger_1_active:
            active_draw += self._charger_1_power or 0
        if self._charger_2_active:
            active_draw += self._charger_2_power or 0

        # Inverter feeds power INTO the house network, reducing grid import.
        # Subtract it to get the pure solar contribution.
        inverter_feed = 0
        if self._inverter_active and self._inverter_target_power > 0:
            inverter_feed = self._inverter_target_power

        # true_surplus = charger_draw + inverter_feed - grid_power
        # (grid_power negative = export, so subtracting it adds the export)
        return active_draw + inverter_feed - self._grid_power

    async def _start_solar_charging(self, surplus_w: float) -> None:
        """Activate chargers proportionally to available solar surplus.

        In an AC-coupled system the chargers draw from the house network.
        Only turn on as many chargers as the current surplus can sustain,
        so we avoid pulling power from the grid.
        """
        c1_power = self._charger_1_power or 0
        c2_power = self._charger_2_power or 0

        # Determine which chargers to run based on surplus
        # Use a small margin (80%) to avoid grid import from fluctuations
        want_c1 = c1_power > 0 and surplus_w >= c1_power * 0.8
        want_c2 = c2_power > 0 and surplus_w >= c2_power * 0.8
        want_both = (
            c1_power > 0
            and c2_power > 0
            and surplus_w >= (c1_power + c2_power) * 0.8
        )

        if want_both:
            need_c1, need_c2 = True, True
        elif want_c1 and want_c2:
            # Surplus covers either but not both – pick the larger one
            need_c1 = c1_power >= c2_power
            need_c2 = not need_c1
        elif want_c1:
            need_c1, need_c2 = True, False
        elif want_c2:
            need_c1, need_c2 = False, True
        else:
            # Surplus too small for any single charger – use the smaller
            # charger combined with the inverter to cover the deficit,
            # so solar energy is not wasted by feeding it back to the grid.
            min_charger_power = min(
                (p for p in (c1_power, c2_power) if p > 0), default=0
            )
            deficit_w = min_charger_power - surplus_w
            if (
                min_charger_power > 0
                and surplus_w > deficit_w
                and self._inverter_power_entity
            ):
                need_c1 = c1_power == min_charger_power
                need_c2 = not need_c1 if c2_power > 0 else False
                _LOGGER.debug(
                    "Solar surplus %.0fW < charger – using inverter to cover "
                    "deficit (charger=%dW, deficit=%.0fW)",
                    surplus_w, min_charger_power,
                    min_charger_power - surplus_w,
                )
                await self._start_solar_charging_with_inverter(
                    surplus_w, need_c1, need_c2,
                )
                return

            _LOGGER.debug(
                "Solar surplus %.0fW too low for chargers (C1=%dW, C2=%dW)",
                surplus_w, c1_power, c2_power,
            )
            await self._set_mode_idle()
            return

        # Apply charger states
        if self._charger_1_switch:
            if need_c1 and not self._charger_1_active:
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": self._charger_1_switch}
                )
                self._charger_1_active = True
            elif not need_c1 and self._charger_1_active:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": self._charger_1_switch}
                )
                self._charger_1_active = False

        if self._charger_2_switch:
            if need_c2 and not self._charger_2_active:
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": self._charger_2_switch}
                )
                self._charger_2_active = True
            elif not need_c2 and self._charger_2_active:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": self._charger_2_switch}
                )
                self._charger_2_active = False

        # Turn off inverter
        if self._inverter_active:
            if self._inverter_switch:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": self._inverter_switch}
                )
            await self._set_inverter_power(0)
            self._inverter_active = False

        self._operating_mode = MODE_CHARGING
        _LOGGER.info(
            "Solar charging: surplus=%.0fW, C1=%s (%dW), C2=%s (%dW)",
            surplus_w,
            "ON" if need_c1 else "OFF", c1_power,
            "ON" if need_c2 else "OFF", c2_power,
        )

    async def _start_solar_charging_with_inverter(
        self, surplus_w: float, need_c1: bool, need_c2: bool,
    ) -> None:
        """Charge from solar with inverter covering the deficit.

        When the solar surplus is too small for a charger alone, the inverter
        feeds the difference back into the house network so that the charger
        can run with minimal grid import.  The net effect is that most of the
        solar surplus ends up in the battery (minus round-trip losses on the
        deficit portion).
        """
        c1_power = self._charger_1_power or 0
        c2_power = self._charger_2_power or 0
        charger_power = (c1_power if need_c1 else 0) + (c2_power if need_c2 else 0)
        deficit_w = max(0, charger_power - surplus_w)

        # Turn on the chosen charger(s)
        if self._charger_1_switch:
            if need_c1 and not self._charger_1_active:
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": self._charger_1_switch}
                )
                self._charger_1_active = True
            elif not need_c1 and self._charger_1_active:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": self._charger_1_switch}
                )
                self._charger_1_active = False

        if self._charger_2_switch:
            if need_c2 and not self._charger_2_active:
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": self._charger_2_switch}
                )
                self._charger_2_active = True
            elif not need_c2 and self._charger_2_active:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": self._charger_2_switch}
                )
                self._charger_2_active = False

        # Turn on inverter and set power to cover the deficit
        if self._inverter_switch and not self._inverter_active:
            await self.hass.services.async_call(
                "switch", "turn_on", {"entity_id": self._inverter_switch}
            )
        self._inverter_active = True

        # Set inverter to cover the deficit between charger draw and solar surplus
        self._inverter_target_power = deficit_w
        domain = self._inverter_power_entity.split(".")[0]
        await self.hass.services.async_call(
            domain,
            "set_value",
            {
                "entity_id": self._inverter_power_entity,
                "value": round(deficit_w),
            },
        )

        self._operating_mode = MODE_CHARGING
        _LOGGER.info(
            "Solar+inverter charging: surplus=%.0fW, charger=%dW, "
            "inverter covers deficit=%.0fW",
            surplus_w, charger_power, deficit_w,
        )

    async def _start_discharging(self) -> None:
        """Activate inverter to discharge battery into home network.

        If an inverter power entity is configured, use zero-feed regulation
        to match the inverter output to the current grid import, preventing
        any export back to the grid.
        """
        # Turn off chargers (always, even if already discharging)
        if self._operating_mode != MODE_DISCHARGING:
            _LOGGER.info(
                "Starting battery discharge (SOC: %.1f%%, Price: %.4f EUR/kWh)",
                self._battery_soc or 0,
                self._current_price or 0,
            )

            if self._charger_1_switch:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": self._charger_1_switch}
                )
                self._charger_1_active = False

            if self._charger_2_switch:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": self._charger_2_switch}
                )
                self._charger_2_active = False

            # Turn on inverter switch if configured (simple on/off mode)
            if self._inverter_switch:
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": self._inverter_switch}
                )

            self._inverter_active = True
            self._operating_mode = MODE_DISCHARGING

        # Zero-feed regulation: adjust inverter power to match grid import
        if self._inverter_power_entity:
            await self._regulate_zero_feed()

    async def _regulate_zero_feed(self) -> None:
        """Regulate inverter power to match grid import (zero-feed).

        Reads current grid_power (positive = importing from grid) and adjusts
        the inverter output so that grid import approaches zero without
        exporting back to the grid.
        """
        if self._grid_power is None:
            _LOGGER.debug("No grid power data available for zero-feed regulation")
            return

        max_power = self._inverter_power or 800

        # Target: current inverter output + what we're still importing from grid
        # grid_power is positive when importing, negative when exporting
        new_target = self._inverter_target_power + self._grid_power

        # Clamp between 0 and max power (never negative, never over max)
        new_target = max(0, min(max_power, new_target))

        # Only update if the change is significant (> 20W) to avoid excessive calls
        if abs(new_target - self._inverter_target_power) < 20:
            return

        self._inverter_target_power = new_target

        _LOGGER.debug(
            "Zero-feed regulation: grid_power=%.0fW, setting inverter to %.0fW (max: %dW)",
            self._grid_power,
            new_target,
            max_power,
        )

        domain = self._inverter_power_entity.split(".")[0]
        await self.hass.services.async_call(
            domain,
            "set_value",
            {
                "entity_id": self._inverter_power_entity,
                "value": round(new_target),
            },
        )

    async def _set_mode_idle(self) -> None:
        """Set idle mode - turn off all devices."""
        if self._operating_mode == MODE_IDLE:
            return

        _LOGGER.info("Setting battery to idle mode")
        await self.stop_all()

    async def stop_all(self) -> None:
        """Turn off all chargers and inverters."""
        if self._charger_1_switch:
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": self._charger_1_switch}
            )
            self._charger_1_active = False

        if self._charger_2_switch:
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": self._charger_2_switch}
            )
            self._charger_2_active = False

        if self._inverter_switch:
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": self._inverter_switch}
            )
        await self._set_inverter_power(0)
        self._inverter_active = False

        self._operating_mode = MODE_IDLE

    async def _set_inverter_power(self, value: float) -> None:
        """Set inverter power entity to a specific value."""
        if not self._inverter_power_entity:
            return

        value = max(0, round(value))
        if value == round(self._inverter_target_power):
            return

        self._inverter_target_power = value
        domain = self._inverter_power_entity.split(".")[0]
        await self.hass.services.async_call(
            domain,
            "set_value",
            {
                "entity_id": self._inverter_power_entity,
                "value": value,
            },
        )

    async def force_charge(self) -> None:
        """Force battery into charging mode."""
        self._strategy = STRATEGY_MANUAL
        await self._start_charging()

    async def force_discharge(self) -> None:
        """Force battery into discharging mode."""
        self._strategy = STRATEGY_MANUAL
        await self._start_discharging()

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
        self._charger_1_switch = options.get(
            CONF_CHARGER_1_SWITCH, self._charger_1_switch
        )
        self._charger_2_switch = options.get(
            CONF_CHARGER_2_SWITCH, self._charger_2_switch
        )
        self._charger_1_power = options.get(
            CONF_CHARGER_1_POWER, self._charger_1_power
        )
        self._charger_2_power = options.get(
            CONF_CHARGER_2_POWER, self._charger_2_power
        )
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
        self._house_consumption_w = options.get(
            CONF_HOUSE_CONSUMPTION_W, self._house_consumption_w
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
            "charger_1_active": self._charger_1_active,
            "charger_2_active": self._charger_2_active,
            "inverter_active": self._inverter_active,
            "inverter_target_power": self._inverter_target_power,
            "inverter_actual_power": self._inverter_actual_power,
            "price_forecast": self._price_forecast,
            "cheap_hours": cheap_hours,
            "expensive_hours": expensive_hours,
            "min_soc": self._min_soc,
            "max_soc": self._max_soc,
            "battery_capacity_kwh": self._battery_capacity,
            "allow_grid_charging": self._allow_grid_charging,
            "allow_discharging": self._allow_discharging,
            "use_solar_forecast": self._use_solar_forecast,
        }

    # Properties for entities
    @property
    def strategy(self) -> str:
        return self._strategy

    @property
    def operating_mode(self) -> str:
        return self._operating_mode

    @property
    def current_price(self) -> float | None:
        return self._current_price

    @property
    def battery_soc(self) -> float | None:
        return self._battery_soc

    @property
    def grid_power(self) -> float | None:
        return self._grid_power

    @property
    def min_soc(self) -> int:
        return self._min_soc

    @min_soc.setter
    def min_soc(self, value: int) -> None:
        self._min_soc = max(0, min(100, value))

    @property
    def max_soc(self) -> int:
        return self._max_soc

    @max_soc.setter
    def max_soc(self, value: int) -> None:
        self._max_soc = max(0, min(100, value))

    @property
    def price_low_threshold(self) -> float:
        return self._price_low

    @price_low_threshold.setter
    def price_low_threshold(self, value: float) -> None:
        self._price_low = value

    @property
    def price_high_threshold(self) -> float:
        return self._price_high

    @price_high_threshold.setter
    def price_high_threshold(self, value: float) -> None:
        self._price_high = value

    @property
    def allow_grid_charging(self) -> bool:
        return self._allow_grid_charging

    @allow_grid_charging.setter
    def allow_grid_charging(self, value: bool) -> None:
        self._allow_grid_charging = value
        _LOGGER.info("Grid charging %s", "enabled" if value else "disabled")

    @property
    def allow_discharging(self) -> bool:
        return self._allow_discharging

    @allow_discharging.setter
    def allow_discharging(self, value: bool) -> None:
        self._allow_discharging = value
        _LOGGER.info("Discharging %s", "enabled" if value else "disabled")

    @property
    def use_solar_forecast(self) -> bool:
        return self._use_solar_forecast

    @use_solar_forecast.setter
    def use_solar_forecast(self, value: bool) -> None:
        self._use_solar_forecast = value
        _LOGGER.info("Solar forecast %s", "enabled" if value else "disabled")
