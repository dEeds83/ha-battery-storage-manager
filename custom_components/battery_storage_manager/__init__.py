"""Battery Storage Manager - Intelligent battery storage management for Home Assistant."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant

from .const import CONF_CHARGERS, DOMAIN, PLATFORMS
from .coordinator import BatteryStorageCoordinator

_LOGGER = logging.getLogger(__name__)

CARD_VERSION = "2.0.0"
FRONTEND_DIR = Path(__file__).parent / "frontend"
FRONTEND_CARDS = [
    "battery-plan-card.js",
    "battery-status-card.js",
]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up Battery Storage Manager integration (register frontend resources)."""
    hass.data.setdefault(DOMAIN, {})

    # Step 1: Register static paths so HA can serve the JS files
    static_paths = []
    for card_file in FRONTEND_CARDS:
        url_path = f"/{DOMAIN}/{card_file}"
        file_path = str(FRONTEND_DIR / card_file)
        static_paths.append(
            StaticPathConfig(url_path, file_path, cache_headers=False)
        )

    await hass.http.async_register_static_paths(static_paths)
    _LOGGER.debug("Registered static paths for %d frontend cards", len(static_paths))

    # Step 2: Register as Lovelace resources
    # Must be deferred until HA is fully started (resources may not be loaded yet)
    async def _register_frontend(_event=None):
        await _register_lovelace_resources(hass)

    if hass.state == CoreState.running:
        await _register_frontend()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_frontend)

    return True


async def _register_lovelace_resources(hass: HomeAssistant) -> None:
    """Register frontend cards as Lovelace resources.

    Uses the Lovelace resource store API (storage mode) or falls back
    to add_extra_js_url for YAML mode dashboards.
    """
    lovelace = hass.data.get("lovelace")
    if lovelace is None:
        _LOGGER.warning("Lovelace data not available")
        return

    # LovelaceData uses .resource_mode (NOT .mode)
    resource_mode = getattr(lovelace, "resource_mode", None)
    resources = getattr(lovelace, "resources", None)

    _LOGGER.debug(
        "Lovelace: resource_mode=%s, resources=%s, type=%s",
        resource_mode, resources is not None, type(lovelace).__name__,
    )

    if resource_mode != "storage" or resources is None:
        # YAML mode → use legacy add_extra_js_url
        try:
            from homeassistant.components.frontend import add_extra_js_url
            for card_file in FRONTEND_CARDS:
                add_extra_js_url(hass, f"/{DOMAIN}/{card_file}")
            _LOGGER.info(
                "Registered cards via add_extra_js_url (resource_mode=%s)",
                resource_mode,
            )
        except Exception:
            _LOGGER.warning(
                "Could not auto-register cards. Add manually: "
                "Dashboard → ⋮ → Resources → Add as JavaScript Module:\n%s",
                "\n".join(f"  /{DOMAIN}/{f}" for f in FRONTEND_CARDS),
                exc_info=True,
            )
        return

    # Storage mode → use resource store API
    try:
        if not resources.loaded:
            await resources.async_load()

        existing = resources.async_items() or []

        for card_file in FRONTEND_CARDS:
            url_path = f"/{DOMAIN}/{card_file}"
            url_versioned = f"{url_path}?v={CARD_VERSION}"

            # Find existing resource by base URL
            found = None
            for item in existing:
                item_url = item.get("url", "") if isinstance(item, dict) else getattr(item, "url", "")
                if item_url.split("?")[0] == url_path:
                    found = item
                    break

            if found is None:
                # Create new resource (use "res_type" for create/update API)
                await resources.async_create_item(
                    {"res_type": "module", "url": url_versioned}
                )
                _LOGGER.info("Created Lovelace resource: %s", url_versioned)
            else:
                # Check if version needs updating
                found_url = found.get("url", "") if isinstance(found, dict) else getattr(found, "url", "")
                found_id = found.get("id") if isinstance(found, dict) else getattr(found, "id", None)
                if found_url != url_versioned and found_id:
                    await resources.async_update_item(
                        found_id,
                        {"res_type": "module", "url": url_versioned},
                    )
                    _LOGGER.info("Updated Lovelace resource: %s", url_versioned)
                else:
                    _LOGGER.debug("Lovelace resource current: %s", url_versioned)

    except Exception:
        _LOGGER.warning(
            "Failed to register Lovelace resources via store. "
            "Add manually: Dashboard → ⋮ → Resources → JavaScript Module:\n%s",
            "\n".join(f"  /{DOMAIN}/{f}" for f in FRONTEND_CARDS),
            exc_info=True,
        )


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
