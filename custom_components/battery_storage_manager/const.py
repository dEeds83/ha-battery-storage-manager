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
CONF_SOLAR_ENERGY_TODAY_ENTITY = "solar_energy_today_entity"  # daily solar kWh for forecast calibration
CONF_SOLAR_SWITCHES = "solar_switches"  # switches to disable PV at negative grid prices
STORAGE_KEY_SOLAR_CALIBRATION = "solar_calibration"
STORAGE_VERSION_SOLAR_CALIBRATION = 1
SOLAR_CALIBRATION_ROLLING_DAYS = 14
CONF_HOUSE_CONSUMPTION_W = "house_consumption_w"
DEFAULT_HOUSE_CONSUMPTION_W = 500  # average house consumption in watts
CONF_EPEX_PREDICTOR_ENABLED = "epex_predictor_enabled"  # enable EPEX long-term forecast
CONF_EPEX_PREDICTOR_REGION = "epex_predictor_region"  # EPEX region (DE, AT, etc.)
DEFAULT_EPEX_PREDICTOR_REGION = "DE"
EPEX_PREDICTOR_BASE_URL = "https://epexpredictor.batzill.com"
CONF_BATTERY_CYCLE_COST = "battery_cycle_cost"  # ct/kWh cost per charge/discharge cycle
DEFAULT_BATTERY_CYCLE_COST = 10.0  # ct/kWh (typical LFP: ~3000€ / 6000 cycles / 5 kWh)
CONF_BATTERY_EFFICIENCY = "battery_efficiency"  # round-trip efficiency in percent
DEFAULT_BATTERY_EFFICIENCY = 90  # percent
CONF_OUTSIDE_TEMPERATURE_ENTITY = "outside_temperature_entity"  # outdoor temp for consumption model
CONF_CHARGER_POWER_ENTITIES = "charger_power_entities"  # measured power sensors per charger

# Charger types (per-entry "type" in CONF_CHARGERS list)
CHARGER_TYPE_SWITCH = "switch"
CHARGER_TYPE_DIMMER = "dimmer"
# Top-level select in config_flow: which kind of charger setup.
# "hybrid" = Dimmer (Solar) + Switch-Charger (nur Netz-Laden).
# Pro Charger-Eintrag bleibt "type" entweder "switch" oder "dimmer";
# "hybrid" ist nur die Setup-Variante.
CHARGER_TYPE_HYBRID = "hybrid"
CONF_CHARGER_TYPE = "charger_type"
# Dimmer-specific config keys (used when CONF_CHARGER_TYPE == "dimmer")
CONF_DIMMER_POWER_ENTITY = "dimmer_power_entity"  # writable number entity (setpoint)
CONF_DIMMER_ENABLE_SWITCH = "dimmer_enable_switch"  # optional on/off switch
CONF_DIMMER_MAX_POWER = "dimmer_max_power"  # max W (clamp)
CONF_DIMMER_MIN_POWER = "dimmer_min_power"  # below this → 0
CONF_DIMMER_ACTUAL_POWER_ENTITY = "dimmer_actual_power_entity"  # optional readback sensor
CONF_BATTERY_VOLTAGE_ENTITY = "battery_voltage_entity"  # Smartshunt voltage
CONF_BATTERY_CURRENT_ENTITY = "battery_current_entity"  # Smartshunt current

# Defaults
DEFAULT_MIN_SOC = 10  # percent
DEFAULT_MAX_SOC = 95  # percent
DEFAULT_BATTERY_CAPACITY = 5.0  # kWh
DEFAULT_PRICE_LOW_THRESHOLD = 15.0  # ct/kWh
DEFAULT_PRICE_HIGH_THRESHOLD = 30.0  # ct/kWh
DEFAULT_SCAN_INTERVAL = 15  # seconds
CONSUMPTION_STATS_ROLLING_DAYS = 14  # days of history per hour slot
STORAGE_KEY_CONSUMPTION = "consumption_stats"
STORAGE_VERSION_CONSUMPTION = 1  # format migration handled in _load_consumption_stats

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
