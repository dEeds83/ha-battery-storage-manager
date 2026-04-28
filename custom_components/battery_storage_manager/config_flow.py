"""Config flow for Battery Storage Manager."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CHARGER_TYPE_DIMMER,
    CHARGER_TYPE_SWITCH,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CURRENT_ENTITY,
    CONF_BATTERY_CYCLE_COST,
    CONF_BATTERY_EFFICIENCY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_VOLTAGE_ENTITY,
    CONF_CHARGER_ENTITIES,
    CONF_CHARGER_POWER_DEFAULT,
    CONF_CHARGER_POWER_ENTITIES,
    CONF_CHARGER_TYPE,
    CONF_CHARGERS,
    CONF_DIMMER_ACTUAL_POWER_ENTITY,
    CONF_DIMMER_ENABLE_SWITCH,
    CONF_DIMMER_MAX_POWER,
    CONF_DIMMER_MIN_POWER,
    CONF_DIMMER_POWER_ENTITY,
    CONF_EPEX_PREDICTOR_ENABLED,
    CONF_EPEX_PREDICTOR_REGION,
    CONF_HOUSE_CONSUMPTION_W,
    CONF_OUTSIDE_TEMPERATURE_ENTITY,
    DEFAULT_EPEX_PREDICTOR_REGION,
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
    CONF_SOLAR_ENERGY_TODAY_ENTITY,
    CONF_SOLAR_POWER_ENTITY,
    CONF_TIBBER_PRICE_ENTITY,
    CONF_TIBBER_PRICES_ENTITY,
    CONF_TIBBER_PULSE_CONSUMPTION_ENTITY,
    CONF_TIBBER_PULSE_PRODUCTION_ENTITY,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_BATTERY_CYCLE_COST,
    DEFAULT_BATTERY_EFFICIENCY,
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
        vol.Optional(CONF_SOLAR_POWER_ENTITY, default=""): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        vol.Optional(CONF_SOLAR_ENERGY_TODAY_ENTITY, default=""): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        vol.Optional(CONF_OUTSIDE_TEMPERATURE_ENTITY, default=""): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
        ),
        vol.Optional(CONF_EPEX_PREDICTOR_ENABLED, default=False): selector.BooleanSelector(),
        vol.Optional(
            CONF_EPEX_PREDICTOR_REGION, default=DEFAULT_EPEX_PREDICTOR_REGION
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["DE", "AT", "BE", "NL", "SE1", "SE2", "SE3", "SE4", "DK1", "DK2"],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
    }
)

_CHARGER_TYPE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[CHARGER_TYPE_SWITCH, CHARGER_TYPE_DIMMER],
        mode=selector.SelectSelectorMode.DROPDOWN,
        translation_key="charger_type",
    )
)


STEP_DEVICES_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_CHARGER_TYPE, default=CHARGER_TYPE_SWITCH): _CHARGER_TYPE_SELECTOR,
        # Switch-mode fields (multiple static chargers)
        vol.Optional(CONF_CHARGER_ENTITIES, default=[]): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch", multiple=True)
        ),
        vol.Optional(CONF_CHARGER_POWER_DEFAULT, default=800): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=5000, step=100, unit_of_measurement="W"
            )
        ),
        vol.Optional(CONF_CHARGER_POWER_ENTITIES, default=[]): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", multiple=True)
        ),
        # Dimmer-mode fields (single dimmable charger).
        # Optional EntitySelectors WITHOUT default — empty default="" fails
        # entity_id validation in HA frontend ("neither valid entity ID nor
        # UUID" error persists even after picking a valid entity).
        vol.Optional(CONF_DIMMER_POWER_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["number", "input_number"])
        ),
        vol.Optional(CONF_DIMMER_ENABLE_SWITCH): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch")
        ),
        vol.Optional(CONF_DIMMER_MAX_POWER, default=1000): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=20000, step=1, unit_of_measurement="W",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(CONF_DIMMER_MIN_POWER, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=20000, step=1, unit_of_measurement="W",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(CONF_DIMMER_ACTUAL_POWER_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        # Inverter (used for discharge in both modes)
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
        vol.Optional(
            CONF_BATTERY_CYCLE_COST, default=DEFAULT_BATTERY_CYCLE_COST
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=50, step=0.5, unit_of_measurement="ct/kWh"
            )
        ),
        vol.Optional(
            CONF_BATTERY_EFFICIENCY, default=DEFAULT_BATTERY_EFFICIENCY
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=50, max=100, step=1, unit_of_measurement="%"
            )
        ),
        vol.Optional(CONF_BATTERY_VOLTAGE_ENTITY, default=""): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        vol.Optional(CONF_BATTERY_CURRENT_ENTITY, default=""): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
    }
)


def _build_chargers_list(
    entities: list[str],
    default_power: int,
    existing_chargers: list[dict] | None = None,
    power_entities: list[str] | None = None,
) -> list[dict]:
    """Build switch-type chargers list, preserving per-charger power for known entities."""
    existing_map = {}
    if existing_chargers:
        existing_map = {c["switch"]: c for c in existing_chargers}

    power_ents = power_entities or []
    result = []
    for i, eid in enumerate(entities):
        if not eid:
            continue
        existing = existing_map.get(eid, {})
        power_entity = power_ents[i] if i < len(power_ents) else existing.get("power_entity", "")
        result.append({
            "switch": eid,
            "power": existing.get("power", int(default_power)),
            "power_entity": power_entity,
            "type": CHARGER_TYPE_SWITCH,
            "min_power": 0,
        })
    return result


def _build_dimmer_charger(
    power_entity: str,
    max_power: int,
    min_power: int = 0,
    enable_switch: str = "",
    actual_power_entity: str = "",
) -> list[dict]:
    """Build a single-entry chargers list for dimmer mode."""
    if not power_entity:
        return []
    return [{
        "switch": enable_switch or "",
        "power": int(max_power),
        "power_entity": power_entity,
        "actual_power_entity": actual_power_entity or "",
        "type": CHARGER_TYPE_DIMMER,
        "min_power": int(min_power),
    }]


def _extract_chargers_from_input(
    user_input: dict,
    existing_chargers: list[dict] | None = None,
) -> list[dict]:
    """Build CONF_CHARGERS list from a config-flow user_input dict.

    Pops both switch-mode and dimmer-mode keys from user_input. Returns
    the chargers list according to charger_type. Validates min/max for
    dimmer.
    """
    charger_type = user_input.pop(CONF_CHARGER_TYPE, CHARGER_TYPE_SWITCH)
    charger_entities = user_input.pop(CONF_CHARGER_ENTITIES, [])
    default_power = user_input.pop(CONF_CHARGER_POWER_DEFAULT, 800)
    power_entities = user_input.pop(CONF_CHARGER_POWER_ENTITIES, [])
    dimmer_power_entity = user_input.pop(CONF_DIMMER_POWER_ENTITY, "")
    dimmer_enable_switch = user_input.pop(CONF_DIMMER_ENABLE_SWITCH, "")
    dimmer_max_power = user_input.pop(CONF_DIMMER_MAX_POWER, 1000)
    dimmer_min_power = user_input.pop(CONF_DIMMER_MIN_POWER, 0)
    dimmer_actual = user_input.pop(CONF_DIMMER_ACTUAL_POWER_ENTITY, "")

    if charger_type == CHARGER_TYPE_DIMMER:
        if dimmer_min_power > dimmer_max_power:
            raise vol.Invalid("dimmer_min_power must be <= dimmer_max_power")
        return _build_dimmer_charger(
            dimmer_power_entity,
            int(dimmer_max_power),
            int(dimmer_min_power),
            dimmer_enable_switch,
            dimmer_actual,
        )
    return _build_chargers_list(
        charger_entities, default_power, existing_chargers,
        power_entities=power_entities,
    )


class BatteryStorageManagerConfigFlow(
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle a config flow for Battery Storage Manager."""

    VERSION = 3

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
            self._data[CONF_CHARGERS] = _extract_chargers_from_input(user_input)
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
    """Handle options flow for Battery Storage Manager."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._data: dict = {}

    def _current(self, key: str, default=None):
        """Get current value from options (if previously changed) or original data."""
        return self._config_entry.options.get(
            key, self._config_entry.data.get(key, default)
        )

    def _current_chargers(self) -> list[dict]:
        """Get current chargers list."""
        return self._current(CONF_CHARGERS, [])

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
                    vol.Optional(
                        CONF_SOLAR_POWER_ENTITY,
                        default=self._current(CONF_SOLAR_POWER_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Optional(
                        CONF_SOLAR_ENERGY_TODAY_ENTITY,
                        default=self._current(CONF_SOLAR_ENERGY_TODAY_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Optional(
                        CONF_OUTSIDE_TEMPERATURE_ENTITY,
                        default=self._current(CONF_OUTSIDE_TEMPERATURE_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                    ),
                    vol.Optional(
                        CONF_EPEX_PREDICTOR_ENABLED,
                        default=self._current(CONF_EPEX_PREDICTOR_ENABLED, False),
                    ): selector.BooleanSelector(),
                    vol.Optional(
                        CONF_EPEX_PREDICTOR_REGION,
                        default=self._current(CONF_EPEX_PREDICTOR_REGION, DEFAULT_EPEX_PREDICTOR_REGION),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["DE", "AT", "BE", "NL", "SE1", "SE2", "SE3", "SE4", "DK1", "DK2"],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_devices(self, user_input=None):
        """Step 2: Device entities."""
        if user_input is not None:
            self._data[CONF_CHARGERS] = _extract_chargers_from_input(
                user_input, self._current_chargers()
            )
            self._data.update(user_input)
            return await self.async_step_battery()

        def _opt_entity(key: str, current: str):
            """vol.Optional with default only if current is a real entity_id."""
            if current:
                return vol.Optional(key, default=current)
            return vol.Optional(key)

        current_chargers = self._current_chargers()
        switch_chargers = [c for c in current_chargers if c.get("type", CHARGER_TYPE_SWITCH) == CHARGER_TYPE_SWITCH]
        dimmer_charger = next(
            (c for c in current_chargers if c.get("type") == CHARGER_TYPE_DIMMER), None
        )
        current_charger_type = (
            CHARGER_TYPE_DIMMER if dimmer_charger else CHARGER_TYPE_SWITCH
        )
        current_entities = [c["switch"] for c in switch_chargers]
        current_powers = [c["power"] for c in switch_chargers]
        avg_power = (
            int(sum(current_powers) / len(current_powers))
            if current_powers
            else 800
        )

        return self.async_show_form(
            step_id="devices",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_CHARGER_TYPE,
                        default=current_charger_type,
                    ): _CHARGER_TYPE_SELECTOR,
                    vol.Optional(
                        CONF_CHARGER_ENTITIES,
                        default=current_entities,
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="switch", multiple=True)
                    ),
                    vol.Optional(
                        CONF_CHARGER_POWER_DEFAULT,
                        default=avg_power,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=5000, step=100, unit_of_measurement="W"
                        )
                    ),
                    vol.Optional(
                        CONF_CHARGER_POWER_ENTITIES,
                        default=[c.get("power_entity", "") for c in switch_chargers],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor", multiple=True)
                    ),
                    _opt_entity(
                        CONF_DIMMER_POWER_ENTITY,
                        dimmer_charger.get("power_entity", "") if dimmer_charger else "",
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=["number", "input_number"])
                    ),
                    _opt_entity(
                        CONF_DIMMER_ENABLE_SWITCH,
                        dimmer_charger.get("switch", "") if dimmer_charger else "",
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="switch")
                    ),
                    vol.Optional(
                        CONF_DIMMER_MAX_POWER,
                        default=dimmer_charger.get("power", 1000) if dimmer_charger else 1000,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=20000, step=1, unit_of_measurement="W",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_DIMMER_MIN_POWER,
                        default=dimmer_charger.get("min_power", 0) if dimmer_charger else 0,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=20000, step=1, unit_of_measurement="W",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    _opt_entity(
                        CONF_DIMMER_ACTUAL_POWER_ENTITY,
                        dimmer_charger.get("actual_power_entity", "") if dimmer_charger else "",
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
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
                    vol.Optional(
                        CONF_BATTERY_CYCLE_COST,
                        default=self._current(
                            CONF_BATTERY_CYCLE_COST, DEFAULT_BATTERY_CYCLE_COST
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=50, step=0.5, unit_of_measurement="ct/kWh"
                        )
                    ),
                    vol.Optional(
                        CONF_BATTERY_EFFICIENCY,
                        default=self._current(
                            CONF_BATTERY_EFFICIENCY, DEFAULT_BATTERY_EFFICIENCY
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=50, max=100, step=1, unit_of_measurement="%"
                        )
                    ),
                    vol.Optional(
                        CONF_BATTERY_VOLTAGE_ENTITY,
                        default=self._current(CONF_BATTERY_VOLTAGE_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Optional(
                        CONF_BATTERY_CURRENT_ENTITY,
                        default=self._current(CONF_BATTERY_CURRENT_ENTITY, ""),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                }
            ),
        )
