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
        Charger1StatusSensor(coordinator, entry),
        Charger2StatusSensor(coordinator, entry),
        InverterStatusSensor(coordinator, entry),
        InverterActualPowerSensor(coordinator, entry),
        InverterTargetPowerSensor(coordinator, entry),
        NextCheapWindowSensor(coordinator, entry),
        NextExpensiveWindowSensor(coordinator, entry),
        BatteryPlanSensor(coordinator, entry),
        PlannedActionSensor(coordinator, entry),
        ExpectedSolarSensor(coordinator, entry),
    ]

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
            "charger_1_active": d.get("charger_1_active"),
            "charger_2_active": d.get("charger_2_active"),
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


class Charger1StatusSensor(BatteryStorageBaseSensor):
    """Sensor showing charger 1 status."""

    _attr_icon = "mdi:battery-charging"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "charger_1_status", "Ladegerät 1 Status")

    @property
    def native_value(self) -> str:
        if self.coordinator.data and self.coordinator.data.get("charger_1_active"):
            return "Aktiv"
        return "Inaktiv"


class Charger2StatusSensor(BatteryStorageBaseSensor):
    """Sensor showing charger 2 status."""

    _attr_icon = "mdi:battery-charging"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "charger_2_status", "Ladegerät 2 Status")

    @property
    def native_value(self) -> str:
        if self.coordinator.data and self.coordinator.data.get("charger_2_active"):
            return "Aktiv"
        return "Inaktiv"


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
