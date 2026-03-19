"""Battery Storage Manager - Intelligent battery storage management for Home Assistant."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import BatteryStorageCoordinator

_LOGGER = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent / "frontend"
FRONTEND_CARDS = [
    "battery-plan-card.js",
    "battery-status-card.js",
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Battery Storage Manager from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Register frontend cards (once per HA instance)
    if f"{DOMAIN}_frontend_registered" not in hass.data:
        static_paths = []
        for card_file in FRONTEND_CARDS:
            url_path = f"/{DOMAIN}/{card_file}"
            file_path = str(FRONTEND_DIR / card_file)
            static_paths.append(
                StaticPathConfig(url_path, file_path, cache_headers=False)
            )
            add_extra_js_url(hass, url_path)
            _LOGGER.debug("Registered frontend card: %s", url_path)
        await hass.http.async_register_static_paths(static_paths)
        hass.data[f"{DOMAIN}_frontend_registered"] = True

    coordinator = BatteryStorageCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for options updates and apply them live
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # Register services
    await _register_services(hass)

    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update - apply new config to coordinator without restart."""
    coordinator: BatteryStorageCoordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.apply_options(entry.options)
    _LOGGER.info("Options updated, applied to coordinator")


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
