"""Sensor entities for Battery Storage Manager."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_BATTERY_PLAN, ATTR_EXPECTED_SOLAR_KWH, ATTR_PLAN_SUMMARY, DOMAIN
from .coordinator import BatteryStorageCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator: BatteryStorageCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        OperatingModeSensor(coordinator, entry),
        StrategySensor(coordinator, entry),
        CurrentPriceSensor(coordinator, entry),
        BatterySOCSensor(coordinator, entry),
        GridPowerSensor(coordinator, entry),
        InverterStatusSensor(coordinator, entry),
        InverterActualPowerSensor(coordinator, entry),
        InverterTargetPowerSensor(coordinator, entry),
        NextCheapWindowSensor(coordinator, entry),
        NextExpensiveWindowSensor(coordinator, entry),
        BatteryPlanSensor(coordinator, entry),
        PlannedActionSensor(coordinator, entry),
        ExpectedSolarSensor(coordinator, entry),
        ConsumptionForecastSensor(coordinator, entry),
        PriceForecastSensor(coordinator, entry),
        SolarCalibrationFactorSensor(coordinator, entry),
        OptimizationLogSensor(coordinator, entry),
        ActionHistorySensor(coordinator, entry),
        MeasuredEfficiencySensor(coordinator, entry),
    ]

    # Dynamic charger status sensors
    for i in range(len(coordinator.chargers)):
        entities.append(ChargerStatusSensor(coordinator, entry, i))

    async_add_entities(entities)


class BatteryStorageBaseSensor(CoordinatorEntity, SensorEntity):
    """Base sensor for battery storage manager."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BatteryStorageCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Battery Storage Manager",
            "manufacturer": "Custom",
            "model": "Battery Storage Manager",
            "sw_version": "1.0.0",
        }


class OperatingModeSensor(BatteryStorageBaseSensor):
    """Sensor showing current operating mode."""

    _attr_icon = "mdi:battery-sync"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "operating_mode", "Betriebsmodus")

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            return self.coordinator.data.get("operating_mode")
        return None

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}
        d = self.coordinator.data
        return {
            "chargers": [
                {"index": c.get("index"), "active": c.get("active"), "power": c.get("power")}
                for c in d.get("chargers", [])
            ],
            "inverter_active": d.get("inverter_active"),
            "inverter_target_power": d.get("inverter_target_power"),
            "inverter_actual_power": d.get("inverter_actual_power"),
            "strategy": d.get("strategy"),
            "current_price": d.get("current_price"),
            "battery_soc": d.get("battery_soc"),
            "grid_power": d.get("grid_power"),
            "planned_action": d.get("planned_action"),
            "allow_grid_charging": d.get("allow_grid_charging"),
            "allow_discharging": d.get("allow_discharging"),
            "use_solar_forecast": d.get("use_solar_forecast"),
            "grid_max_soc": d.get("grid_max_soc"),
            "solar_headroom_pct": d.get("solar_headroom_pct"),
            "solar_power_w": d.get("solar_power_w"),
            "solar_surplus_w": d.get("solar_surplus_w"),
            "version": d.get("version"),
            "source_hash": d.get("source_hash"),
        }


class StrategySensor(BatteryStorageBaseSensor):
    """Sensor showing current strategy."""

    _attr_icon = "mdi:strategy"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "strategy", "Strategie")

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            return self.coordinator.data.get("strategy")
        return None


class CurrentPriceSensor(BatteryStorageBaseSensor):
    """Sensor showing current electricity price."""

    _attr_icon = "mdi:currency-eur"
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "current_price", "Aktueller Strompreis")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            return self.coordinator.data.get("current_price")
        return None

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}
        return {
            "cheap_hours": self.coordinator.data.get("cheap_hours", []),
            "expensive_hours": self.coordinator.data.get("expensive_hours", []),
        }


class BatterySOCSensor(BatteryStorageBaseSensor):
    """Sensor showing battery state of charge."""

    _attr_icon = "mdi:battery"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "battery_soc", "Speicher Ladestand")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            return self.coordinator.data.get("battery_soc")
        return None

    @property
    def icon(self) -> str:
        soc = self.native_value
        if soc is None:
            return "mdi:battery-unknown"
        if soc >= 90:
            return "mdi:battery"
        if soc >= 70:
            return "mdi:battery-80"
        if soc >= 50:
            return "mdi:battery-60"
        if soc >= 30:
            return "mdi:battery-40"
        if soc >= 10:
            return "mdi:battery-20"
        return "mdi:battery-alert"


class GridPowerSensor(BatteryStorageBaseSensor):
    """Sensor showing current grid power."""

    _attr_icon = "mdi:transmission-tower"
    _attr_native_unit_of_measurement = "W"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "grid_power", "Netzleistung")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            return self.coordinator.data.get("grid_power")
        return None

    @property
    def extra_state_attributes(self):
        grid = self.native_value
        if grid is None:
            return {}
        return {
            "direction": "Netzbezug" if grid > 0 else "Einspeisung",
        }


class ChargerStatusSensor(BatteryStorageBaseSensor):
    """Sensor showing charger N status (dynamically created per charger)."""

    _attr_icon = "mdi:battery-charging"

    def __init__(self, coordinator, entry, charger_index: int):
        self._charger_index = charger_index
        num = charger_index + 1
        super().__init__(
            coordinator, entry,
            f"charger_{num}_status",
            f"Ladegerät {num} Status",
        )

    @property
    def native_value(self) -> str:
        if self.coordinator.data:
            chargers = self.coordinator.data.get("chargers", [])
            if self._charger_index < len(chargers):
                return "Aktiv" if chargers[self._charger_index].get("active") else "Inaktiv"
        return "Inaktiv"

    @property
    def extra_state_attributes(self):
        if self.coordinator.data:
            chargers = self.coordinator.data.get("chargers", [])
            if self._charger_index < len(chargers):
                c = chargers[self._charger_index]
                return {"power_w": c.get("power", 0), "switch": c.get("switch", "")}
        return {}


class InverterStatusSensor(BatteryStorageBaseSensor):
    """Sensor showing feed inverter status."""

    _attr_icon = "mdi:solar-power"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "inverter_status", "Einspeise-Wechselrichter Status"
        )

    @property
    def native_value(self) -> str:
        if self.coordinator.data and self.coordinator.data.get("inverter_active"):
            return "Aktiv"
        return "Inaktiv"


class InverterActualPowerSensor(BatteryStorageBaseSensor):
    """Sensor showing the actual power output of the feed inverter."""

    _attr_icon = "mdi:solar-power"
    _attr_native_unit_of_measurement = "W"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "inverter_actual_power", "Wechselrichter Leistung"
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            return self.coordinator.data.get("inverter_actual_power")
        return None


class InverterTargetPowerSensor(BatteryStorageBaseSensor):
    """Sensor showing the target power the plugin sets for the feed inverter."""

    _attr_icon = "mdi:tune-vertical"
    _attr_native_unit_of_measurement = "W"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "inverter_target_power", "Wechselrichter Soll-Leistung"
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            return self.coordinator.data.get("inverter_target_power")
        return None


class NextCheapWindowSensor(BatteryStorageBaseSensor):
    """Sensor showing next cheap price window."""

    _attr_icon = "mdi:clock-check"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "next_cheap_window", "Nächstes günstiges Fenster"
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            return self.coordinator.data.get("next_cheap_window")
        return None


class NextExpensiveWindowSensor(BatteryStorageBaseSensor):
    """Sensor showing next expensive price window."""

    _attr_icon = "mdi:clock-alert"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "next_expensive_window", "Nächstes teures Fenster"
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            return self.coordinator.data.get("next_expensive_window")
        return None


class BatteryPlanSensor(BatteryStorageBaseSensor):
    """Sensor showing the battery plan summary with full plan as attribute."""

    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "battery_plan", "Speicherplan"
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            return self.coordinator.data.get(ATTR_PLAN_SUMMARY)
        return None

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}
        plan = self.coordinator.data.get(ATTR_BATTERY_PLAN, [])
        attrs = {"plan": plan}
        # Add counts per action type
        action_counts = {}
        for entry in plan:
            action = entry.get("action", "unknown")
            action_counts[action] = action_counts.get(action, 0) + 1
        attrs["action_counts"] = action_counts
        d = self.coordinator.data
        headroom = d.get("solar_headroom_pct", 0)
        if headroom > 0:
            attrs["solar_headroom_pct"] = headroom
            attrs["grid_max_soc"] = d.get("grid_max_soc")
        return attrs


class PlannedActionSensor(BatteryStorageBaseSensor):
    """Sensor showing the currently planned action for this hour."""

    _attr_icon = "mdi:play-circle"

    ACTION_LABELS = {
        "charge": "Laden (Netz)",
        "discharge": "Entladen",
        "solar_charge": "Laden (Solar)",
        "hold": "Halten",
        "idle": "Inaktiv",
    }

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "planned_action", "Geplante Aktion"
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            action = self.coordinator.data.get("planned_action")
            if action:
                return self.ACTION_LABELS.get(action, action)
        return None

    @property
    def icon(self) -> str:
        if not self.coordinator.data:
            return "mdi:play-circle"
        action = self.coordinator.data.get("planned_action")
        icons = {
            "charge": "mdi:battery-charging",
            "discharge": "mdi:battery-arrow-down",
            "solar_charge": "mdi:solar-power",
            "hold": "mdi:battery-lock",
            "idle": "mdi:battery-outline",
        }
        return icons.get(action, "mdi:play-circle")


class ExpectedSolarSensor(BatteryStorageBaseSensor):
    """Sensor showing expected remaining solar production today."""

    _attr_icon = "mdi:solar-power-variant"
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "expected_solar", "Erwartete Solarproduktion"
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get(ATTR_EXPECTED_SOLAR_KWH)
            if val is not None:
                return round(val, 2)
        return None


class ConsumptionForecastSensor(BatteryStorageBaseSensor):
    """Sensor showing the current hour's predicted consumption based on rolling average."""

    _attr_icon = "mdi:home-lightning-bolt"
    _attr_native_unit_of_measurement = "W"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "consumption_forecast", "Verbrauchsprognose"
        )

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        forecast = self.coordinator.data.get("consumption_forecast", {})
        if not forecast:
            return None
        from homeassistant.util import dt as dt_util
        current_hour = dt_util.now().hour
        val = forecast.get(current_hour)
        return round(val) if val is not None else None

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}
        forecast = self.coordinator.data.get("consumption_forecast", {})
        return {
            "hourly_forecast_w": {
                f"{h:02d}:00": round(w) for h, w in sorted(forecast.items())
            },
        }


class PriceForecastSensor(BatteryStorageBaseSensor):
    """Sensor providing upcoming prices as CSV for ePaper display and automations."""

    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "price_forecast_csv", "Preisprognose"
        )

    @property
    def native_value(self) -> str | None:
        """Return next 12h of prices as comma-separated hourly averages.

        For 15-min data, averages 4 slots per hour to keep the CSV
        within the 255 char HA state limit (12 values × ~7 chars = ~84).
        """
        if not self.coordinator.data:
            return None
        forecast = self.coordinator.data.get("price_forecast", [])
        if not forecast:
            return None

        from homeassistant.util import dt as dt_util
        from datetime import datetime, timedelta
        now = dt_util.now()
        cutoff = now + timedelta(hours=12)

        # Collect all prices within 12h, grouped by hour
        hourly: dict[int, list[float]] = {}
        for entry in forecast:
            try:
                start = datetime.fromisoformat(entry["start"])
                if start.tzinfo is not None:
                    start = dt_util.as_local(start)
                if start >= now.replace(minute=0, second=0, microsecond=0) and start < cutoff:
                    h = start.hour
                    hourly.setdefault(h, []).append(entry.get("total", 0))
            except (ValueError, TypeError, KeyError):
                continue

        if not hourly:
            return None

        # Build ordered list starting from current hour
        cur_hour = now.hour
        prices = []
        for i in range(12):
            h = (cur_hour + i) % 24
            if h in hourly:
                avg = sum(hourly[h]) / len(hourly[h])
                prices.append(str(round(avg, 4)))

        return ",".join(prices) if prices else None

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}
        forecast = self.coordinator.data.get("price_forecast", [])

        from homeassistant.util import dt as dt_util
        now = dt_util.now()

        from datetime import datetime, timedelta
        cutoff = now + timedelta(hours=12)

        prices_list = []
        for entry in forecast:
            try:
                start = datetime.fromisoformat(entry["start"])
                if start.tzinfo is not None:
                    start = dt_util.as_local(start)
                if start >= now.replace(second=0, microsecond=0) and start < cutoff:
                    prices_list.append({
                        "time": start.strftime("%H:%M"),
                        "price": round(entry.get("total", 0), 4),
                    })
            except (ValueError, TypeError, KeyError):
                continue

        # Detect slot duration (minutes between entries)
        slot_minutes = 60
        if len(prices_list) >= 2:
            t1 = prices_list[0]["time"]
            t2 = prices_list[1]["time"]
            m1 = int(t1[:2]) * 60 + int(t1[3:])
            m2 = int(t2[:2]) * 60 + int(t2[3:])
            diff = (m2 - m1) % 1440
            if 10 <= diff <= 60:
                slot_minutes = diff

        all_prices = [p["price"] for p in prices_list]

        # Extended forecast: Tibber prices + EPEX visualization data
        extended = []
        for entry in forecast:
            try:
                start = datetime.fromisoformat(entry["start"])
                if start.tzinfo is not None:
                    start = dt_util.as_local(start)
                if start >= now.replace(second=0, microsecond=0):
                    extended.append({
                        "time": start.strftime("%Y-%m-%dT%H:%M"),
                        "price": round(entry.get("total", 0), 4),
                        "source": "tibber",
                    })
            except (ValueError, TypeError, KeyError):
                continue

        # Append EPEX visualization data (separate from price_forecast)
        epex_viz = self.coordinator.data.get("epex_visualization", [])
        for entry in epex_viz:
            try:
                start = datetime.fromisoformat(entry["start"])
                if start.tzinfo is not None:
                    start = dt_util.as_local(start)
                item = {
                    "time": start.strftime("%Y-%m-%dT%H:%M"),
                    "price": entry.get("total", 0),
                    "source": "epex_predictor",
                }
                if "epex_spot" in entry:
                    item["epex_spot"] = entry["epex_spot"]
                extended.append(item)
            except (ValueError, TypeError, KeyError):
                continue

        epex_markup = self.coordinator.data.get("epex_markup")
        epex_tv = self.coordinator.data.get("epex_terminal_value_ct")

        # Build actions_csv: dominant action per hour for ePaper display
        # D=discharge, C=charge, H=hold, I=idle, S=solar_charge
        actions_csv = ""
        plan = self.coordinator.data.get("battery_plan", [])
        if plan:
            cur_hour = now.hour
            for i in range(12):
                h = (cur_hour + i) % 24
                # Find all plan entries for this hour
                hour_actions = []
                for entry in plan:
                    try:
                        eh = int(entry["hour"][11:13])
                        if eh == h:
                            hour_actions.append(entry.get("action", "idle"))
                    except (ValueError, KeyError, TypeError):
                        continue
                if hour_actions:
                    # Dominant action (most frequent)
                    from collections import Counter
                    dominant = Counter(hour_actions).most_common(1)[0][0]
                    code = {"discharge": "D", "charge": "C", "hold": "H",
                            "idle": "I", "solar_charge": "S"}.get(dominant, "I")
                    actions_csv += code
                else:
                    actions_csv += "I"
                if i < 11:
                    actions_csv += ","

        return {
            "prices": prices_list,
            "count": len(prices_list),
            "slot_minutes": slot_minutes,
            "min_price": round(min(all_prices), 4) if all_prices else None,
            "max_price": round(max(all_prices), 4) if all_prices else None,
            "avg_price": round(sum(all_prices) / len(all_prices), 4) if all_prices else None,
            "actions_csv": actions_csv or None,
            "extended_forecast": extended,
            "extended_count": len(extended),
            "epex_regression": epex_markup,
            "epex_terminal_value_ct": epex_tv,
        }


class SolarCalibrationFactorSensor(BatteryStorageBaseSensor):
    """Sensor showing the solar forecast calibration/correction factor."""

    _attr_icon = "mdi:solar-power-variant-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "solar_calibration_factor", "Solar Korrekturfaktor"
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("solar_calibration_factor")
            if val is not None:
                return round(val, 3)
        return None

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}
        factor = self.coordinator.data.get("solar_calibration_factor", 1.0)
        intraday = self.coordinator.data.get("intraday_solar_factor", 1.0)
        return {
            "description": f"Forecast × {factor:.2f} = calibrated value",
            "deviation_percent": round((factor - 1.0) * 100, 1),
            "intraday_factor": round(intraday, 3),
            "intraday_deviation_percent": round((intraday - 1.0) * 100, 1),
        }


class OptimizationLogSensor(BatteryStorageBaseSensor):
    """Sensor exposing the optimization decision log for UI access."""

    _attr_icon = "mdi:text-box-search"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "optimization_log", "Optimierungs-Log"
        )

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        log = self.coordinator.data.get("optimization_log", [])
        return log[-1] if log else "Keine Einträge"

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}
        log = self.coordinator.data.get("optimization_log", [])
        return {
            "entries": log,
            "count": len(log),
            "efficiency_percent": self.coordinator.data.get("battery_efficiency"),
            "cycle_cost_ct": self.coordinator.data.get("cycle_cost_ct"),
        }


class ActionHistorySensor(BatteryStorageBaseSensor):
    """Sensor recording what was actually executed (48h rolling history)."""

    _attr_icon = "mdi:history"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "action_history", "Aktionshistorie"
        )

    @property
    def native_value(self) -> str | None:
        history = self.coordinator._action_history
        if not history:
            return "Keine Einträge"
        last = history[-1]
        return f"{last['time'][11:16]} {last['mode']} SOC {last.get('soc', '?')}%"

    @property
    def extra_state_attributes(self):
        history = self.coordinator._action_history
        # Summarize: count actions in last hour
        now_entries = history[-4:] if history else []  # last ~4 min
        return {
            "history": history,
            "count": len(history),
            "hours_covered": round(len(history) / 60, 1),
        }


class MeasuredEfficiencySensor(BatteryStorageBaseSensor):
    """Sensor showing measured battery roundtrip efficiency from Smartshunt data."""

    _attr_icon = "mdi:gauge"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "measured_efficiency", "Gemessene Effizienz"
        )

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        val = self.coordinator.data.get("measured_roundtrip_efficiency")
        return val if val is not None else None

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}
        d = self.coordinator.data
        attrs = {
            "charge_efficiency": d.get("measured_charge_efficiency"),
            "discharge_efficiency": d.get("measured_discharge_efficiency"),
            "roundtrip_efficiency": d.get("measured_roundtrip_efficiency"),
            "configured_efficiency": d.get("battery_efficiency"),
            "charge_energy_today_kwh": d.get("efficiency_charge_kwh"),
            "discharge_energy_today_kwh": d.get("efficiency_discharge_kwh"),
            "rolling_days": d.get("efficiency_rolling_days"),
            "history": d.get("efficiency_history", []),
        }
        measured = d.get("measured_roundtrip_efficiency")
        configured = d.get("battery_efficiency")
        attrs["optimizer_uses"] = (
            f"gemessen ({measured}%)" if measured else f"konfiguriert ({configured}%)"
        )
        return attrs
