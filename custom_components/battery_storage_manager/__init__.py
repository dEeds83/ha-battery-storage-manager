"""Battery Storage Manager - Intelligent battery storage management for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import BatteryStorageCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Battery Storage Manager from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = BatteryStorageCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    await _register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: BatteryStorageCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator.stop()

    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)

    return unload_ok


async def _register_services(hass: HomeAssistant) -> None:
    """Register custom services."""

    async def handle_set_strategy(call):
        """Handle set strategy service call."""
        entry_id = call.data.get("entry_id")
        strategy = call.data.get("strategy")
        if entry_id in hass.data.get(DOMAIN, {}):
            coordinator = hass.data[DOMAIN][entry_id]
            coordinator.set_strategy(strategy)

    async def handle_force_charge(call):
        """Handle force charge service call."""
        entry_id = call.data.get("entry_id")
        if entry_id in hass.data.get(DOMAIN, {}):
            coordinator = hass.data[DOMAIN][entry_id]
            await coordinator.force_charge()

    async def handle_force_discharge(call):
        """Handle force discharge service call."""
        entry_id = call.data.get("entry_id")
        if entry_id in hass.data.get(DOMAIN, {}):
            coordinator = hass.data[DOMAIN][entry_id]
            await coordinator.force_discharge()

    async def handle_stop(call):
        """Handle stop service call."""
        entry_id = call.data.get("entry_id")
        if entry_id in hass.data.get(DOMAIN, {}):
            coordinator = hass.data[DOMAIN][entry_id]
            await coordinator.stop_all()

    if not hass.services.has_service(DOMAIN, "set_strategy"):
        hass.services.async_register(DOMAIN, "set_strategy", handle_set_strategy)
        hass.services.async_register(DOMAIN, "force_charge", handle_force_charge)
        hass.services.async_register(DOMAIN, "force_discharge", handle_force_discharge)
        hass.services.async_register(DOMAIN, "stop", handle_stop)
