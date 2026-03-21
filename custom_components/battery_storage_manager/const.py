"""Constants for Battery Storage Manager."""

DOMAIN = "battery_storage_manager"

# Configuration keys
CONF_TIBBER_PRICE_ENTITY = "tibber_price_entity"
CONF_TIBBER_PULSE_CONSUMPTION_ENTITY = "tibber_pulse_consumption_entity"
CONF_TIBBER_PULSE_PRODUCTION_ENTITY = "tibber_pulse_production_entity"
CONF_CHARGERS = "chargers"  # list of {"switch": entity_id, "power": int}
CONF_CHARGER_ENTITIES = "charger_entities"  # UI helper: multi-select entity list
CONF_CHARGER_POWER_DEFAULT = "charger_power_default"  # UI helper: default power for new chargers
CONF_INVERTER_FEED_SWITCH = "inverter_feed_switch"
CONF_INVERTER_FEED_POWER = "inverter_feed_power"
CONF_INVERTER_FEED_POWER_ENTITY = "inverter_feed_power_entity"
CONF_INVERTER_FEED_ACTUAL_POWER_ENTITY = "inverter_feed_actual_power_entity"
CONF_BATTERY_SOC_ENTITY = "battery_soc_entity"
CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"
CONF_MIN_SOC = "min_soc"
CONF_MAX_SOC = "max_soc"
CONF_PRICE_LOW_THRESHOLD = "price_low_threshold"
CONF_PRICE_HIGH_THRESHOLD = "price_high_threshold"
CONF_TIBBER_PRICES_ENTITY = "tibber_prices_entity"
CONF_SOLAR_FORECAST_ENTITY = "solar_forecast_entity"
CONF_SOLAR_FORECAST_ENTITIES = "solar_forecast_entities"
CONF_SOLAR_POWER_ENTITY = "solar_power_entity"  # current solar production in W
CONF_HOUSE_CONSUMPTION_W = "house_consumption_w"
DEFAULT_HOUSE_CONSUMPTION_W = 500  # average house consumption in watts

# Defaults
DEFAULT_MIN_SOC = 10  # percent
DEFAULT_MAX_SOC = 95  # percent
DEFAULT_BATTERY_CAPACITY = 5.0  # kWh
DEFAULT_PRICE_LOW_THRESHOLD = 15.0  # ct/kWh
DEFAULT_PRICE_HIGH_THRESHOLD = 30.0  # ct/kWh
DEFAULT_SCAN_INTERVAL = 15  # seconds
CONSUMPTION_STATS_ROLLING_DAYS = 14  # days of history per hour slot
STORAGE_KEY_CONSUMPTION = "consumption_stats"
STORAGE_VERSION_CONSUMPTION = 1

# Operating modes
MODE_IDLE = "idle"
MODE_CHARGING = "charging"
MODE_SOLAR_CHARGING = "solar_charging"
MODE_DISCHARGING = "discharging"
MODE_AUTO = "auto"

# Strategy
STRATEGY_PRICE_OPTIMIZED = "price_optimized"
STRATEGY_SELF_CONSUMPTION = "self_consumption"
STRATEGY_MANUAL = "manual"

# Platforms
PLATFORMS = ["sensor", "switch", "number"]

# Attributes
ATTR_CURRENT_PRICE = "current_price"
ATTR_NEXT_CHEAP_WINDOW = "next_cheap_window"
ATTR_NEXT_EXPENSIVE_WINDOW = "next_expensive_window"
ATTR_ESTIMATED_SAVINGS = "estimated_savings"
ATTR_STRATEGY = "strategy"
ATTR_OPERATING_MODE = "operating_mode"
ATTR_BATTERY_PLAN = "battery_plan"
ATTR_PLAN_SUMMARY = "plan_summary"
ATTR_EXPECTED_SOLAR_KWH = "expected_solar_kwh"
