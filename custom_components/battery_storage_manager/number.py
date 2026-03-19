"""Number entities for Battery Storage Manager."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BatteryStorageCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities."""
    coordinator: BatteryStorageCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        MinSOCNumber(coordinator, entry),
        MaxSOCNumber(coordinator, entry),
        PriceLowThresholdNumber(coordinator, entry),
        PriceHighThresholdNumber(coordinator, entry),
    ]

    async_add_entities(entities)


class BatteryStorageBaseNumber(CoordinatorEntity, NumberEntity):
    """Base number entity for battery storage manager."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: BatteryStorageCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
    ) -> None:
        """Initialize the number entity."""
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


class MinSOCNumber(BatteryStorageBaseNumber):
    """Number entity for minimum SOC threshold."""

    _attr_icon = "mdi:battery-low"
    _attr_native_min_value = 0
    _attr_native_max_value = 50
    _attr_native_step = 5
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "min_soc", "Minimaler Ladestand")

    @property
    def native_value(self) -> float:
        return self.coordinator.min_soc

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.min_soc = int(value)
        self.async_write_ha_state()


class MaxSOCNumber(BatteryStorageBaseNumber):
    """Number entity for maximum SOC threshold."""

    _attr_icon = "mdi:battery-high"
    _attr_native_min_value = 50
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "max_soc", "Maximaler Ladestand")

    @property
    def native_value(self) -> float:
        return self.coordinator.max_soc

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.max_soc = int(value)
        self.async_write_ha_state()


class PriceLowThresholdNumber(BatteryStorageBaseNumber):
    """Number entity for low price threshold."""

    _attr_icon = "mdi:currency-eur"
    _attr_native_min_value = 0
    _attr_native_max_value = 50
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "ct/kWh"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "price_low_threshold", "Preisschwelle niedrig"
        )

    @property
    def native_value(self) -> float:
        return self.coordinator.price_low_threshold

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.price_low_threshold = value
        self.async_write_ha_state()


class PriceHighThresholdNumber(BatteryStorageBaseNumber):
    """Number entity for high price threshold."""

    _attr_icon = "mdi:currency-eur"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "ct/kWh"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "price_high_threshold", "Preisschwelle hoch"
        )

    @property
    def native_value(self) -> float:
        return self.coordinator.price_high_threshold

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.price_high_threshold = value
        self.async_write_ha_state()
