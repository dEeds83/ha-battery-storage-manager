"""Switch entities for Battery Storage Manager."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    STRATEGY_MANUAL,
    STRATEGY_PRICE_OPTIMIZED,
    STRATEGY_SELF_CONSUMPTION,
)
from .coordinator import BatteryStorageCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities."""
    coordinator: BatteryStorageCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        AutoModeSwitch(coordinator, entry),
        ForceChargeSwitch(coordinator, entry),
        ForceDischargeSwitch(coordinator, entry),
        AllowGridChargingSwitch(coordinator, entry),
        AllowDischargingSwitch(coordinator, entry),
        AllowSolarChargingSwitch(coordinator, entry),
        UseSolarForecastSwitch(coordinator, entry),
        AllowSolarPvGateSwitch(coordinator, entry),
        ForceSolarOffSwitch(coordinator, entry),
    ]

    async_add_entities(entities)


class BatteryStorageBaseSwitch(CoordinatorEntity, RestoreEntity, SwitchEntity):
    """Base switch with state restore support."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BatteryStorageCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Battery Storage Manager",
            "manufacturer": "Custom",
            "model": "Battery Storage Manager",
            "sw_version": "1.0.0",
        }

    async def async_added_to_hass(self) -> None:
        """Restore last known state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in ("unknown", "unavailable", None):
            restored_on = last_state.state == "on"
            self._apply_restored_state(restored_on)
            _LOGGER.debug("Restored %s = %s", self._attr_name, last_state.state)

    def _apply_restored_state(self, is_on: bool) -> None:
        """Apply restored state to coordinator. Override in subclass."""


class AutoModeSwitch(BatteryStorageBaseSwitch):
    """Switch to enable/disable automatic price optimization."""

    _attr_icon = "mdi:robot"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "auto_mode", "Automatik-Modus")

    @property
    def is_on(self) -> bool:
        return self.coordinator.strategy in (
            STRATEGY_PRICE_OPTIMIZED,
            STRATEGY_SELF_CONSUMPTION,
        )

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.set_strategy(STRATEGY_PRICE_OPTIMIZED)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.set_strategy(STRATEGY_MANUAL)
        await self.coordinator.stop_all()
        self.async_write_ha_state()


class ForceChargeSwitch(BatteryStorageBaseSwitch):
    """Switch to force battery charging."""

    _attr_icon = "mdi:battery-charging-100"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "force_charge", "Zwangsladen")

    @property
    def is_on(self) -> bool:
        return (
            self.coordinator.strategy == STRATEGY_MANUAL
            and self.coordinator.operating_mode == "charging"
        )

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.force_charge()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.stop_all()
        self.async_write_ha_state()

    def _apply_restored_state(self, is_on: bool) -> None:
        if is_on:
            self.hass.async_create_task(self.coordinator.force_charge())


class AllowGridChargingSwitch(BatteryStorageBaseSwitch):
    """Switch to allow/disallow charging from grid."""

    _attr_icon = "mdi:transmission-tower-import"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "allow_grid_charging", "Netzladen erlauben")

    @property
    def is_on(self) -> bool:
        return self.coordinator.allow_grid_charging

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.allow_grid_charging = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.allow_grid_charging = False
        self.async_write_ha_state()

    def _apply_restored_state(self, is_on: bool) -> None:
        self.coordinator.allow_grid_charging = is_on


class AllowDischargingSwitch(BatteryStorageBaseSwitch):
    """Switch to allow/disallow battery discharging."""

    _attr_icon = "mdi:battery-arrow-down-outline"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "allow_discharging", "Entladen erlauben")

    @property
    def is_on(self) -> bool:
        return self.coordinator.allow_discharging

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.allow_discharging = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.allow_discharging = False
        self.async_write_ha_state()

    def _apply_restored_state(self, is_on: bool) -> None:
        self.coordinator.allow_discharging = is_on


class AllowSolarChargingSwitch(BatteryStorageBaseSwitch):
    """Master switch for solar-surplus absorption (zero-export toggle).

    When OFF: no opportunistic absorption, all excess solar is exported.
    When ON (default): chargers/dimmer absorb surplus per existing logic.
    """

    _attr_icon = "mdi:solar-power"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "allow_solar_charging", "Solarladen erlauben"
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.allow_solar_charging

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.allow_solar_charging = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.allow_solar_charging = False
        # Immediately stop active solar absorption.
        await self.coordinator.stop_all()
        self.async_write_ha_state()

    def _apply_restored_state(self, is_on: bool) -> None:
        self.coordinator.allow_solar_charging = is_on


class UseSolarForecastSwitch(BatteryStorageBaseSwitch):
    """Switch to enable/disable solar-aware planning."""

    _attr_icon = "mdi:solar-power-variant-outline"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "use_solar_forecast", "Solarprognose nutzen"
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.use_solar_forecast

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.use_solar_forecast = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.use_solar_forecast = False
        self.async_write_ha_state()

    def _apply_restored_state(self, is_on: bool) -> None:
        self.coordinator.use_solar_forecast = is_on


class AllowSolarPvGateSwitch(BatteryStorageBaseSwitch):
    """Toggle PV auto-disable at negative grid prices.

    ON (default): konfigurierte PV-Switches werden bei negativem
    Tibber-Preis ausgeschaltet, bei Preis >= 0 wieder ein.
    OFF: keine automatische Steuerung; falls aktuell pausiert,
    werden Switches einmalig wieder eingeschaltet (kein Stranded-Off).
    """

    _attr_icon = "mdi:solar-panel-large"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "allow_solar_pv_gate",
            "PV-Abschaltung bei Negativpreis",
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.allow_solar_pv_gate

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.allow_solar_pv_gate = True
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.allow_solar_pv_gate = False
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    def _apply_restored_state(self, is_on: bool) -> None:
        self.coordinator.allow_solar_pv_gate = is_on


class ForceSolarOffSwitch(BatteryStorageBaseSwitch):
    """Manueller Override: PV-Schalter zwangsweise aus.

    ON: alle konfigurierten PV-Switches werden ausgeschaltet, unabhängig
    vom Strompreis oder vom PV-Gate-Toggle. Speicherplan rechnet mit
    Solar=0 für die gesamte Forecast-Horizont.
    OFF (Default): normale Logik (Negativpreis-Gate falls aktiv).
    """

    _attr_icon = "mdi:solar-panel"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "force_solar_off",
            "Solaranlagen aus (manuell)",
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.force_solar_off

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.force_solar_off = True
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.force_solar_off = False
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    def _apply_restored_state(self, is_on: bool) -> None:
        # Bewusst kein Restore: Force-Off ist ein manueller Override und
        # soll bei jedem HA-Restart / Plugin-Reload auf False starten.
        # User-Wunsch v2.41.11: nach Reaktivierung der Integration default
        # OFF, sonst bleibt PV ungewollt deaktiviert.
        return


class ForceDischargeSwitch(BatteryStorageBaseSwitch):
    """Switch to force battery discharging."""

    _attr_icon = "mdi:battery-arrow-down"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "force_discharge", "Zwangsentladen")

    @property
    def is_on(self) -> bool:
        return (
            self.coordinator.strategy == STRATEGY_MANUAL
            and self.coordinator.operating_mode == "discharging"
        )

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.force_discharge()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.stop_all()
        self.async_write_ha_state()

    def _apply_restored_state(self, is_on: bool) -> None:
        if is_on:
            self.hass.async_create_task(self.coordinator.force_discharge())
