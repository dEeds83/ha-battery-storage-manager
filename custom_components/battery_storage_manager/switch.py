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


class CoordinatorAttributeSwitch(BatteryStorageBaseSwitch):
    """Generischer Switch der ein Coordinator-Attribute toggelt.

    Spart die wiederholte is_on / turn_on / turn_off / restore-Boilerplate.
    Subklassen setzen nur _attr_icon und rufen super().__init__ mit dem
    Coordinator-Attribut-Namen.

    Optionale Hooks:
      _on_turn_off_extra(): zusaetzliche async-Aktion beim Off (z.B. stop_all)
      _restore_via_action: bool — bei True wird Coordinator-Setter NICHT
        aufgerufen, stattdessen das Attribut beim Restore ignoriert.
    """

    _coord_attr: str = ""
    _restore_via_action: bool = True  # default: restore setzt das Attribut

    def __init__(self, coordinator, entry, key: str, name: str, attr: str):
        super().__init__(coordinator, entry, key, name)
        self._coord_attr = attr

    @property
    def is_on(self) -> bool:
        return bool(getattr(self.coordinator, self._coord_attr))

    async def async_turn_on(self, **kwargs) -> None:
        setattr(self.coordinator, self._coord_attr, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        setattr(self.coordinator, self._coord_attr, False)
        await self._on_turn_off_extra()
        self.async_write_ha_state()

    async def _on_turn_off_extra(self) -> None:
        """Override fuer zusaetzliche Aktionen beim Off (z.B. stop_all)."""

    def _apply_restored_state(self, is_on: bool) -> None:
        if self._restore_via_action:
            setattr(self.coordinator, self._coord_attr, is_on)


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


class AllowGridChargingSwitch(CoordinatorAttributeSwitch):
    """Switch to allow/disallow charging from grid."""
    _attr_icon = "mdi:transmission-tower-import"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "allow_grid_charging",
                         "Netzladen erlauben", "allow_grid_charging")


class AllowDischargingSwitch(CoordinatorAttributeSwitch):
    """Switch to allow/disallow battery discharging."""
    _attr_icon = "mdi:battery-arrow-down-outline"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "allow_discharging",
                         "Entladen erlauben", "allow_discharging")


class AllowSolarChargingSwitch(CoordinatorAttributeSwitch):
    """Master switch for solar-surplus absorption (zero-export toggle).

    OFF: keine opportunistic Absorption, alles ueberschuessige Solar wird
    exportiert. ON (Default): Charger/Dimmer absorbieren Surplus.
    """
    _attr_icon = "mdi:solar-power"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "allow_solar_charging",
                         "Solarladen erlauben", "allow_solar_charging")

    async def _on_turn_off_extra(self) -> None:
        # Immediately stop active solar absorption.
        await self.coordinator.stop_all()


class UseSolarForecastSwitch(CoordinatorAttributeSwitch):
    """Switch to enable/disable solar-aware planning."""
    _attr_icon = "mdi:solar-power-variant-outline"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "use_solar_forecast",
                         "Solarprognose nutzen", "use_solar_forecast")


class AllowSolarPvGateSwitch(CoordinatorAttributeSwitch):
    """Toggle PV auto-disable at negative grid prices.

    ON (default): konfigurierte PV-Switches werden bei negativem
    Tibber-Preis ausgeschaltet, bei Preis >= 0 wieder ein.
    OFF: keine automatische Steuerung; falls aktuell pausiert,
    werden Switches einmalig wieder eingeschaltet (kein Stranded-Off).
    """
    _attr_icon = "mdi:solar-panel-large"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "allow_solar_pv_gate",
                         "PV-Abschaltung bei Negativpreis",
                         "allow_solar_pv_gate")

    async def async_turn_on(self, **kwargs) -> None:
        await super().async_turn_on()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await super().async_turn_off()
        await self.coordinator.async_request_refresh()


class ForceSolarOffSwitch(CoordinatorAttributeSwitch):
    """Manueller Override: PV-Schalter zwangsweise aus.

    ON: alle konfigurierten PV-Switches werden ausgeschaltet, unabhängig
    vom Strompreis oder vom PV-Gate-Toggle. Speicherplan rechnet mit
    Solar=0 für die gesamte Forecast-Horizont.
    OFF (Default): normale Logik (Negativpreis-Gate falls aktiv).

    Bewusst kein Restore (v2.41.11): manueller Override soll bei jedem
    Restart/Reload auf False starten.
    """
    _attr_icon = "mdi:solar-panel"
    _restore_via_action = False  # kein Restore

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "force_solar_off",
                         "Solaranlagen aus (manuell)", "force_solar_off")

    async def async_turn_on(self, **kwargs) -> None:
        await super().async_turn_on()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await super().async_turn_off()
        await self.coordinator.async_request_refresh()


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
