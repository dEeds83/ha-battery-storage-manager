"""Number entities for Battery Storage Manager."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BatteryStorageCoordinator

_LOGGER = logging.getLogger(__name__)


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
        InverterSettleSecondsNumber(coordinator, entry),
    ]

    async_add_entities(entities)


class BatteryStorageBaseNumber(CoordinatorEntity, RestoreEntity, NumberEntity):
    """Base number entity with state restore support."""

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

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in ("unknown", "unavailable", None):
            try:
                restored = float(last_state.state)
                self._apply_restored_value(restored)
                _LOGGER.debug(
                    "Restored %s = %s", self._attr_name, restored
                )
            except (ValueError, TypeError):
                pass

    def _apply_restored_value(self, value: float) -> None:
        """Apply the restored value to the coordinator. Override in subclass."""


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
        await self.coordinator.async_request_refresh()

    def _apply_restored_value(self, value: float) -> None:
        self.coordinator.min_soc = int(value)


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
        await self.coordinator.async_request_refresh()

    def _apply_restored_value(self, value: float) -> None:
        self.coordinator.max_soc = int(value)


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
        await self.coordinator.async_request_refresh()

    def _apply_restored_value(self, value: float) -> None:
        self.coordinator.price_low_threshold = value


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
        await self.coordinator.async_request_refresh()

    def _apply_restored_value(self, value: float) -> None:
        self.coordinator.price_high_threshold = value


class InverterSettleSecondsNumber(BatteryStorageBaseNumber):
    """Settle-Zeit nach jedem Inverter-Setpoint-Write."""

    _attr_icon = "mdi:timer-cog-outline"
    _attr_native_min_value = 0
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "s"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry,
            "inverter_settle_seconds", "Wechselrichter Settle-Zeit",
        )

    @property
    def native_value(self) -> float:
        return self.coordinator._inverter_settle_seconds

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator._inverter_settle_seconds = float(value)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    def _apply_restored_value(self, value: float) -> None:
        self.coordinator._inverter_settle_seconds = float(value)
