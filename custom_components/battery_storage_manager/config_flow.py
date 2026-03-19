"""Config flow for Battery Storage Manager."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CHARGER_1_POWER,
    CONF_CHARGER_1_SWITCH,
    CONF_CHARGER_2_POWER,
    CONF_CHARGER_2_SWITCH,
    CONF_HOUSE_CONSUMPTION_W,
    CONF_INVERTER_FEED_ACTUAL_POWER_ENTITY,
    CONF_INVERTER_FEED_POWER,
    CONF_INVERTER_FEED_POWER_ENTITY,
    CONF_INVERTER_FEED_SWITCH,
    CONF_MAX_SOC,
    CONF_MIN_SOC,
    CONF_PRICE_HIGH_THRESHOLD,
    CONF_PRICE_LOW_THRESHOLD,
    CONF_SOLAR_FORECAST_ENTITIES,
    CONF_SOLAR_FORECAST_ENTITY,
    CONF_TIBBER_PRICE_ENTITY,
    CONF_TIBBER_PRICES_ENTITY,
    CONF_TIBBER_PULSE_CONSUMPTION_ENTITY,
    CONF_TIBBER_PULSE_PRODUCTION_ENTITY,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_HOUSE_CONSUMPTION_W,
    DEFAULT_MAX_SOC,
    DEFAULT_MIN_SOC,
    DEFAULT_PRICE_HIGH_THRESHOLD,
    DEFAULT_PRICE_LOW_THRESHOLD,
    DOMAIN,
)

STEP_TIBBER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TIBBER_PRICE_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        vol.Optional(CONF_TIBBER_PRICES_ENTITY, default=""): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        vol.Required(CONF_TIBBER_PULSE_CONSUMPTION_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        vol.Required(CONF_TIBBER_PULSE_PRODUCTION_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        vol.Optional(CONF_SOLAR_FORECAST_ENTITY, default=""): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        vol.Optional(
            CONF_SOLAR_FORECAST_ENTITIES, default=[]
        ): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", multiple=True)
        ),
    }
)

STEP_DEVICES_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CHARGER_1_SWITCH): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch")
        ),
        vol.Optional(CONF_CHARGER_1_POWER, default=800): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=5000, step=100, unit_of_measurement="W"
            )
        ),
        vol.Required(CONF_CHARGER_2_SWITCH): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch")
        ),
        vol.Optional(CONF_CHARGER_2_POWER, default=800): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=5000, step=100, unit_of_measurement="W"
            )
        ),
        vol.Optional(CONF_INVERTER_FEED_SWITCH, default=""): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch")
        ),
        vol.Optional(CONF_INVERTER_FEED_POWER_ENTITY, default=""): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["number", "input_number"])
        ),
        vol.Optional(CONF_INVERTER_FEED_POWER, default=800): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=5000, step=100, unit_of_measurement="W"
            )
        ),
        vol.Optional(CONF_INVERTER_FEED_ACTUAL_POWER_ENTITY, default=""): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
    }
)

STEP_BATTERY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BATTERY_SOC_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        vol.Optional(
            CONF_BATTERY_CAPACITY_KWH, default=DEFAULT_BATTERY_CAPACITY
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.5, max=100, step=0.5, unit_of_measurement="kWh"
            )
        ),
        vol.Optional(CONF_MIN_SOC, default=DEFAULT_MIN_SOC): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=50, step=5, unit_of_measurement="%"
            )
        ),
        vol.Optional(CONF_MAX_SOC, default=DEFAULT_MAX_SOC): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=50, max=100, step=5, unit_of_measurement="%"
            )
        ),
        vol.Optional(
            CONF_PRICE_LOW_THRESHOLD, default=DEFAULT_PRICE_LOW_THRESHOLD
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=50, step=1, unit_of_measurement="ct/kWh"
            )
        ),
        vol.Optional(
            CONF_PRICE_HIGH_THRESHOLD, default=DEFAULT_PRICE_HIGH_THRESHOLD
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=100, step=1, unit_of_measurement="ct/kWh"
            )
        ),
        vol.Optional(
            CONF_HOUSE_CONSUMPTION_W, default=DEFAULT_HOUSE_CONSUMPTION_W
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=100, max=5000, step=50, unit_of_measurement="W"
            )
        ),
    }
)


class BatteryStorageManagerConfigFlow(
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle a config flow for Battery Storage Manager."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict = {}

    async def async_step_user(self, user_input=None):
        """Handle the first step: Tibber entities."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_devices()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_TIBBER_SCHEMA,
            description_placeholders={
                "title": "Tibber Konfiguration",
            },
        )

    async def async_step_devices(self, user_input=None):
        """Handle the second step: Device entities."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_battery()

        return self.async_show_form(
            step_id="devices",
            data_schema=STEP_DEVICES_SCHEMA,
            description_placeholders={
                "title": "Geräte Konfiguration",
            },
        )

    async def async_step_battery(self, user_input=None):
        """Handle the third step: Battery settings."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(
                title="Battery Storage Manager",
                data=self._data,
            )

        return self.async_show_form(
            step_id="battery",
            data_schema=STEP_BATTERY_SCHEMA,
            description_placeholders={
                "title": "Speicher Konfiguration",
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow."""
        return BatteryStorageOptionsFlow(config_entry)


class BatteryStorageOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Battery Storage Manager.

    All settings from the initial setup can be changed here,
    split into the same 3 steps: Tibber, Devices, Battery.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._data: dict = {}

    def _current(self, key: str, default=None):
        """Get current value from options (if previously changed) or original data."""
        return self._config_entry.options.get(
            key, self._config_entry.data.get(key, default)
        )

    async def async_step_init(self, user_input=None):
        """Step 1: Tibber & Solar entities."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_devices()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TIBBER_PRICE_ENTITY,
                        default=self._current(CONF_TIBBER_PRICE_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Optional(
                        CONF_TIBBER_PRICES_ENTITY,
                        default=self._current(CONF_TIBBER_PRICES_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_TIBBER_PULSE_CONSUMPTION_ENTITY,
                        default=self._current(CONF_TIBBER_PULSE_CONSUMPTION_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_TIBBER_PULSE_PRODUCTION_ENTITY,
                        default=self._current(CONF_TIBBER_PULSE_PRODUCTION_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Optional(
                        CONF_SOLAR_FORECAST_ENTITY,
                        default=self._current(CONF_SOLAR_FORECAST_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Optional(
                        CONF_SOLAR_FORECAST_ENTITIES,
                        default=self._current(CONF_SOLAR_FORECAST_ENTITIES, []),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor", multiple=True)
                    ),
                }
            ),
        )

    async def async_step_devices(self, user_input=None):
        """Step 2: Device entities."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_battery()

        return self.async_show_form(
            step_id="devices",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CHARGER_1_SWITCH,
                        default=self._current(CONF_CHARGER_1_SWITCH, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="switch")
                    ),
                    vol.Optional(
                        CONF_CHARGER_1_POWER,
                        default=self._current(CONF_CHARGER_1_POWER, 800),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=5000, step=100, unit_of_measurement="W"
                        )
                    ),
                    vol.Required(
                        CONF_CHARGER_2_SWITCH,
                        default=self._current(CONF_CHARGER_2_SWITCH, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="switch")
                    ),
                    vol.Optional(
                        CONF_CHARGER_2_POWER,
                        default=self._current(CONF_CHARGER_2_POWER, 800),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=5000, step=100, unit_of_measurement="W"
                        )
                    ),
                    vol.Optional(
                        CONF_INVERTER_FEED_SWITCH,
                        default=self._current(CONF_INVERTER_FEED_SWITCH, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="switch")
                    ),
                    vol.Optional(
                        CONF_INVERTER_FEED_POWER_ENTITY,
                        default=self._current(CONF_INVERTER_FEED_POWER_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=["number", "input_number"])
                    ),
                    vol.Optional(
                        CONF_INVERTER_FEED_POWER,
                        default=self._current(CONF_INVERTER_FEED_POWER, 800),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=5000, step=100, unit_of_measurement="W"
                        )
                    ),
                    vol.Optional(
                        CONF_INVERTER_FEED_ACTUAL_POWER_ENTITY,
                        default=self._current(CONF_INVERTER_FEED_ACTUAL_POWER_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                }
            ),
        )

    async def async_step_battery(self, user_input=None):
        """Step 3: Battery settings."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="", data=self._data)

        return self.async_show_form(
            step_id="battery",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BATTERY_SOC_ENTITY,
                        default=self._current(CONF_BATTERY_SOC_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Optional(
                        CONF_BATTERY_CAPACITY_KWH,
                        default=self._current(
                            CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0.5, max=100, step=0.5, unit_of_measurement="kWh"
                        )
                    ),
                    vol.Optional(
                        CONF_MIN_SOC,
                        default=self._current(CONF_MIN_SOC, DEFAULT_MIN_SOC),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=50, step=5, unit_of_measurement="%"
                        )
                    ),
                    vol.Optional(
                        CONF_MAX_SOC,
                        default=self._current(CONF_MAX_SOC, DEFAULT_MAX_SOC),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=50, max=100, step=5, unit_of_measurement="%"
                        )
                    ),
                    vol.Optional(
                        CONF_PRICE_LOW_THRESHOLD,
                        default=self._current(
                            CONF_PRICE_LOW_THRESHOLD, DEFAULT_PRICE_LOW_THRESHOLD
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=50, step=1, unit_of_measurement="ct/kWh"
                        )
                    ),
                    vol.Optional(
                        CONF_PRICE_HIGH_THRESHOLD,
                        default=self._current(
                            CONF_PRICE_HIGH_THRESHOLD, DEFAULT_PRICE_HIGH_THRESHOLD
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=100, step=1, unit_of_measurement="ct/kWh"
                        )
                    ),
                    vol.Optional(
                        CONF_HOUSE_CONSUMPTION_W,
                        default=self._current(
                            CONF_HOUSE_CONSUMPTION_W, DEFAULT_HOUSE_CONSUMPTION_W
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=100, max=5000, step=50, unit_of_measurement="W"
                        )
                    ),
                }
            ),
        )
