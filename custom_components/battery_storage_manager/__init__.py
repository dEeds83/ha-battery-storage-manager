"""Battery Storage Manager - Intelligent battery storage management for Home Assistant."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_CHARGERS, DOMAIN, PLATFORMS
from .coordinator import BatteryStorageCoordinator

_LOGGER = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent / "frontend"
FRONTEND_CARDS = [
    "battery-plan-card.js",
    "battery-status-card.js",
]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up Battery Storage Manager integration (register frontend resources)."""
    hass.data.setdefault(DOMAIN, {})

    # Register frontend cards once at integration load time
    static_paths = []
    for card_file in FRONTEND_CARDS:
        url_path = f"/{DOMAIN}/{card_file}"
        file_path = str(FRONTEND_DIR / card_file)
        static_paths.append(
            StaticPathConfig(url_path, file_path, cache_headers=False)
        )
        # Add version query param to bust browser cache on updates
        add_extra_js_url(hass, f"{url_path}?v=1.3.0")
        _LOGGER.debug("Registered frontend card: %s", url_path)
    await hass.http.async_register_static_paths(static_paths)

    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry from old format to new format."""
    if entry.version < 2:
        _LOGGER.info("Migrating config entry from version %s to 2", entry.version)
        new_data = dict(entry.data)

        # Migrate charger_1/charger_2 fields to chargers list
        chargers = []
        for i in (1, 2):
            switch = new_data.pop(f"charger_{i}_switch", "")
            power = new_data.pop(f"charger_{i}_power", 800)
            if switch:
                chargers.append({"switch": switch, "power": int(power)})
        new_data[CONF_CHARGERS] = chargers

        # Also migrate options if present
        new_options = dict(entry.options)
        if any(k.startswith("charger_") for k in new_options):
            opt_chargers = []
            for i in (1, 2):
                switch = new_options.pop(f"charger_{i}_switch", "")
                power = new_options.pop(f"charger_{i}_power", 800)
                if switch:
                    opt_chargers.append({"switch": switch, "power": int(power)})
            new_options[CONF_CHARGERS] = opt_chargers

        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options, version=2
        )
        _LOGGER.info(
            "Migration complete: %d chargers configured", len(chargers)
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Battery Storage Manager from a config entry."""
    hass.data.setdefault(DOMAIN, {})

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
