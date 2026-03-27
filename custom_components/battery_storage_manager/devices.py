"""Device control mixin for Battery Storage Manager."""

from __future__ import annotations

import logging

from homeassistant.util import dt as dt_util

from .const import (
    MODE_CHARGING,
    MODE_DISCHARGING,
    MODE_IDLE,
    MODE_SOLAR_CHARGING,
)

_LOGGER = logging.getLogger(__name__)


class DevicesMixin:
    """Mixin providing device control methods for the coordinator."""

    def _sync_device_states(self) -> None:
        """Synchronize internal active flags with actual switch entity states."""
        for i, charger in enumerate(self._chargers):
            if not charger["switch"]:
                continue
            state = self.hass.states.get(charger["switch"])
            if state is None or state.state in ("unknown", "unavailable"):
                continue
            actual_on = state.state == "on"
            if actual_on != charger["active"]:
                _LOGGER.info(
                    "Charger %d (%s): internal=%s, actual=%s -> syncing",
                    i + 1, charger["switch"],
                    "ON" if charger["active"] else "OFF",
                    "ON" if actual_on else "OFF",
                )
                charger["active"] = actual_on

        if self._inverter_switch:
            state = self.hass.states.get(self._inverter_switch)
            if state and state.state not in ("unknown", "unavailable"):
                actual_on = state.state == "on"
                if actual_on != self._inverter_active:
                    _LOGGER.info(
                        "Inverter (%s): internal=%s, actual=%s -> syncing",
                        self._inverter_switch,
                        "ON" if self._inverter_active else "OFF",
                        "ON" if actual_on else "OFF",
                    )
                    self._inverter_active = actual_on

        if self._inverter_power_entity:
            pw_state = self.hass.states.get(self._inverter_power_entity)
            if pw_state and pw_state.state not in ("unknown", "unavailable"):
                try:
                    actual_power = float(pw_state.state)
                except (ValueError, TypeError):
                    actual_power = None
                if actual_power is not None:
                    if self._operating_mode == MODE_IDLE and actual_power > 10:
                        _LOGGER.warning(
                            "Inverter power entity shows %.0fW but mode is IDLE "
                            "-> resetting to 0",
                            actual_power,
                        )
                        self.hass.async_create_task(
                            self._set_inverter_power(0)
                        )
                    elif (self._inverter_active
                          and self._inverter_target_power == 0
                          and actual_power > 10):
                        _LOGGER.info(
                            "Inverter power: adopting actual value %.0fW "
                            "(internal was 0 after restart)",
                            actual_power,
                        )
                        self._inverter_target_power = actual_power

    async def _start_charging(self) -> None:
        """Activate chargers to charge the battery."""
        if self._operating_mode == MODE_CHARGING:
            return

        _LOGGER.info(
            "Starting battery charge (SOC: %.1f%%, Price: %.4f EUR/kWh)",
            self._battery_soc or 0,
            self._current_price or 0,
        )

        for charger in self._chargers:
            if charger["switch"]:
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": charger["switch"]}
                )
                charger["active"] = True

        self._reset_pid()
        if self._inverter_switch:
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": self._inverter_switch}
            )
        await self._set_inverter_power(0)
        self._inverter_active = False

        self._operating_mode = MODE_CHARGING

    async def _apply_charger_states(self, should_be_on: set[int]) -> None:
        """Set each charger on or off, respecting minimum on/off times."""
        now = dt_util.utcnow()
        for i, charger in enumerate(self._chargers):
            if not charger["switch"]:
                continue
            want_on = i in should_be_on
            last_switch = self._charger_last_switch_time.get(i)

            if want_on and not charger["active"]:
                if last_switch and (now - last_switch) < self._charger_min_off_time:
                    _LOGGER.debug(
                        "Charger %d: want ON but min off time not elapsed (%.0fs left)",
                        i + 1,
                        (self._charger_min_off_time - (now - last_switch)).total_seconds(),
                    )
                    continue
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": charger["switch"]}
                )
                charger["active"] = True
                self._charger_last_switch_time[i] = now
            elif not want_on and charger["active"]:
                if last_switch and (now - last_switch) < self._charger_min_on_time:
                    _LOGGER.debug(
                        "Charger %d: want OFF but min on time not elapsed (%.0fs left)",
                        i + 1,
                        (self._charger_min_on_time - (now - last_switch)).total_seconds(),
                    )
                    continue
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": charger["switch"]}
                )
                charger["active"] = False
                self._charger_last_switch_time[i] = now

    async def _start_inverter_deficit(self, deficit_w: float) -> None:
        """Turn on inverter to cover a charger deficit."""
        if self._inverter_switch and not self._inverter_active:
            await self.hass.services.async_call(
                "switch", "turn_on", {"entity_id": self._inverter_switch}
            )
        self._inverter_active = True
        self._inverter_target_power = deficit_w
        domain = self._inverter_power_entity.split(".")[0]
        await self.hass.services.async_call(
            domain,
            "set_value",
            {
                "entity_id": self._inverter_power_entity,
                "value": round(deficit_w),
            },
        )

    async def _start_discharging(self) -> None:
        """Activate inverter to discharge battery into home network."""
        if self._operating_mode != MODE_DISCHARGING:
            _LOGGER.info(
                "Starting battery discharge (SOC: %.1f%%, Price: %.4f EUR/kWh)",
                self._battery_soc or 0,
                self._current_price or 0,
            )

            for charger in self._chargers:
                if charger["switch"]:
                    await self.hass.services.async_call(
                        "switch", "turn_off", {"entity_id": charger["switch"]}
                    )
                    charger["active"] = False

            if self._inverter_switch:
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": self._inverter_switch}
                )

            self._inverter_active = True
            self._operating_mode = MODE_DISCHARGING

        if self._inverter_power_entity:
            await self._regulate_zero_feed()

    async def _regulate_zero_feed(self) -> None:
        """PID-regulated zero-feed control for the inverter."""
        if self._grid_power is None:
            _LOGGER.debug("No grid power data available for zero-feed regulation")
            return

        max_power = self._inverter_power or 800

        if self._grid_power < -10:
            # Exporting to grid — reduce inverter proportionally.
            # Use the PID setpoint approach instead of subtracting raw
            # export, to avoid oscillation and slamming target to 0.
            export_w = abs(self._grid_power)
            # Target should be: current_target minus overshoot, but keep
            # at least 50W so the inverter doesn't shut down completely.
            new_target = self._inverter_target_power - export_w * 0.5
            new_target = max(50, new_target)
            _LOGGER.info(
                "Zero-feed: EXPORT %.0fW -> reducing inverter %.0f -> %.0fW",
                export_w, self._inverter_target_power, new_target,
            )
            # Don't reset PID integral — we need the accumulated history
            # to ramp back up quickly when house consumption increases.
        elif self._grid_power <= 10:
            return
        else:
            setpoint = 25
            error = self._grid_power - setpoint

            if error > 100:
                new_target = self._inverter_target_power + error * 0.9
                _LOGGER.debug(
                    "Zero-feed FAST: import=%.0fW -> inverter=%.0fW",
                    self._grid_power, new_target,
                )
                self._pid_integral = 0.0
                self._pid_last_error = None  # reset D-term to avoid spike
            else:
                p_term = self._pid_kp * error

                self._pid_integral += error
                max_integral = max_power / self._pid_ki if self._pid_ki > 0 else 1000
                self._pid_integral = max(-max_integral, min(max_integral, self._pid_integral))
                i_term = self._pid_ki * self._pid_integral

                d_term = 0.0
                if self._pid_last_error is not None:
                    d_term = self._pid_kd * (error - self._pid_last_error)
                self._pid_last_error = error

                new_target = self._inverter_target_power + p_term + i_term + d_term
                _LOGGER.debug(
                    "Zero-feed PID: grid=%.0fW P=%.0f I=%.0f D=%.0f -> %.0fW",
                    self._grid_power, p_term, i_term, d_term, new_target,
                )

        # During discharge, keep at least 50W to prevent the inverter from
        # effectively shutting down.  The PID will ramp back up on the next
        # cycle when house consumption pulls from the grid again.
        min_target = 50 if self._operating_mode == MODE_DISCHARGING else 0
        new_target = max(min_target, min(max_power, new_target))

        if abs(new_target - self._inverter_target_power) < 10:
            return

        self._inverter_target_power = new_target

        domain = self._inverter_power_entity.split(".")[0]
        await self.hass.services.async_call(
            domain,
            "set_value",
            {
                "entity_id": self._inverter_power_entity,
                "value": round(new_target),
            },
        )

    def _reset_pid(self) -> None:
        """Reset PID state when switching modes."""
        self._pid_integral = 0.0
        self._pid_last_error = None

    async def _set_mode_idle(self) -> None:
        """Set idle mode - turn off all devices."""
        any_device_on = (
            any(c["active"] for c in self._chargers) or self._inverter_active
        )
        if self._operating_mode == MODE_IDLE and not any_device_on:
            return

        _LOGGER.info("Setting battery to idle mode")
        self._reset_pid()
        await self.stop_all()

    async def _set_inverter_power(self, value: float) -> None:
        """Set inverter power entity to a specific value."""
        if not self._inverter_power_entity:
            return

        value = max(0, round(value))
        if value == round(self._inverter_target_power):
            return

        self._inverter_target_power = value
        domain = self._inverter_power_entity.split(".")[0]
        await self.hass.services.async_call(
            domain,
            "set_value",
            {
                "entity_id": self._inverter_power_entity,
                "value": value,
            },
        )

    async def _start_solar_charging(self, surplus_w: float) -> None:
        """Activate chargers proportionally to available solar surplus."""
        if not self._chargers:
            await self._set_mode_idle()
            return

        indexed = [(i, c) for i, c in enumerate(self._chargers) if c["power"] > 0]
        indexed.sort(key=lambda x: x[1]["power"], reverse=True)

        selected: set[int] = set()
        remaining = surplus_w
        for idx, charger in indexed:
            if remaining >= charger["power"] * 0.8:
                selected.add(idx)
                remaining -= charger["power"]

        if not selected:
            above_max = self._battery_soc is not None and self._battery_soc >= self._max_soc
            smallest = min(indexed, key=lambda x: x[1]["power"])
            smallest_idx, smallest_charger = smallest
            deficit_w = smallest_charger["power"] - surplus_w
            if surplus_w >= 100 and self._inverter_power_entity and not above_max:
                _LOGGER.debug(
                    "Solar surplus %.0fW < smallest charger (%dW) - "
                    "using inverter to cover deficit %.0fW",
                    surplus_w, smallest_charger["power"], deficit_w,
                )
                await self._apply_charger_states({smallest_idx})
                await self._start_inverter_deficit(deficit_w)
                self._operating_mode = MODE_SOLAR_CHARGING
                return

            powers_str = ", ".join(
                f"C{i+1}={c['power']}W" for i, c in indexed
            )
            _LOGGER.debug(
                "Solar surplus %.0fW too low for any charger (%s)",
                surplus_w, powers_str,
            )
            await self._set_mode_idle()
            return

        await self._apply_charger_states(selected)

        # Use PID zero-feed regulation to compensate grid import.
        # The chargers may draw more than solar surplus (house consumption
        # adds to the load). The PID sees grid_power > 0 and ramps up
        # the inverter to cover the difference — same as discharge mode.
        if self._inverter_power_entity:
            if not self._inverter_active and self._inverter_switch:
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": self._inverter_switch}
                )
                self._inverter_active = True
            await self._regulate_zero_feed()

        self._operating_mode = MODE_SOLAR_CHARGING
        active_str = ", ".join(
            f"C{i+1}={'ON' if i in selected else 'OFF'}({c['power']}W)"
            for i, c in enumerate(self._chargers)
        )
        _LOGGER.info("Solar charging: surplus=%.0fW, %s", surplus_w, active_str)

    async def _try_solar_opportunistic(self) -> bool:
        """Check for solar surplus and charge opportunistically."""
        true_surplus = self._calculate_true_solar_surplus()
        if true_surplus is None or true_surplus <= 50:
            return False

        above_max_soc = (
            self._battery_soc is not None and self._battery_soc >= self._max_soc
        )

        if above_max_soc:
            min_charger = min(
                (c["power"] for c in self._chargers if c["power"] > 0),
                default=0,
            )
            if true_surplus >= min_charger * 0.8:
                _LOGGER.debug(
                    "Solar charge above max_soc: surplus=%.0fW (pure solar)",
                    true_surplus,
                )
                await self._start_solar_charging(true_surplus)
                return True
            return False

        _LOGGER.debug(
            "Opportunistic solar charge: surplus=%.0fW (grid=%.0fW)",
            true_surplus, self._grid_power or 0,
        )
        await self._start_solar_charging(true_surplus)
        return True

    def _calculate_true_solar_surplus(self) -> float | None:
        """Calculate the true solar surplus available for charging.

        Uses the measured solar power sensor as the primary source.
        Falls back to grid-based inference only when no solar sensor
        is configured and the inverter is NOT active (to avoid
        mistaking inverter overshoot for solar surplus).
        """
        if self._grid_power is None:
            return None

        # Primary: use measured solar power sensor
        if self._solar_power is not None:
            if self._solar_power < 50:
                return 0.0  # No meaningful solar production
            # House consumption = grid import + solar - charger draw
            # The inverter is NOT part of house consumption — it feeds
            # FROM the battery, not from the house.
            # grid_power = house - solar - inverter + chargers
            # → house = grid_power + solar + inverter - chargers
            # → surplus = solar - house = solar - grid - inverter + chargers
            # But simpler: surplus is what's left after the house is fed.
            # If grid < 0 (export), surplus = |grid| + charger_draw
            # If grid > 0 (import), surplus = charger_draw - grid (if > 0)
            # But with inverter active, grid reflects house - solar - inverter.
            # So: house = grid + solar + inverter - chargers
            #     surplus = solar - house = -grid - inverter + chargers
            active_draw = sum(
                c.get("measured_power") or c["power"]
                for c in self._chargers if c["active"]
            )
            inverter_w = 0
            if self._inverter_active:
                inverter_w = self._inverter_actual_power or self._inverter_target_power or 0
            # Solar surplus = solar production that exceeds house consumption.
            # house_consumption = grid_import + solar + inverter - charger_draw
            # solar_surplus = solar - house_consumption
            #               = solar - (grid + solar + inverter - chargers)
            #               = -grid - inverter + chargers
            surplus = -self._grid_power - inverter_w + active_draw
            return max(0, surplus)

        # Fallback: grid-based inference when no solar sensor configured.
        # When inverter is active, we CANNOT reliably distinguish solar
        # from inverter feed. Log a warning so the user knows to configure
        # the solar power sensor.
        if self._inverter_active:
            if self._grid_power < -100:
                # Significant export while inverter active — likely solar
                # but we can't be sure without the sensor.
                _LOGGER.warning(
                    "Grid export %.0fW during discharge but no solar power "
                    "sensor configured — cannot detect solar surplus reliably. "
                    "Please configure the solar power sensor.",
                    abs(self._grid_power),
                )
            return 0.0

        active_draw = sum(
            c.get("measured_power") or c["power"]
            for c in self._chargers if c["active"]
        )
        return max(0, active_draw - self._grid_power)

    async def _run_self_consumption(self) -> None:
        """Self-consumption optimization."""
        if self._battery_soc is None or self._grid_power is None:
            await self._set_mode_idle()
            return

        if self._grid_power > 50 and self._battery_soc > self._min_soc and self._allow_discharging:
            await self._start_discharging()
        elif self._grid_power < -50 and self._battery_soc < self._max_soc:
            await self._start_charging()
        else:
            await self._set_mode_idle()

    async def _execute_plan_action(self, action: str) -> None:
        """Execute the action from the battery plan for the current hour."""
        if action == "charge" and self._battery_soc < self._max_soc:
            if not self._allow_grid_charging:
                _LOGGER.debug("Plan action: CHARGE skipped (grid charging disabled)")
                await self._set_mode_idle()
                return
            _LOGGER.debug("Plan action: CHARGE (grid)")
            await self._start_charging()
        elif action == "discharge" and self._battery_soc > self._min_soc:
            if not self._allow_discharging:
                _LOGGER.debug("Plan action: DISCHARGE skipped (discharging disabled)")
                await self._set_mode_idle()
                return
            # Solar surplus check is handled globally by the coordinator
            # calling _try_solar_opportunistic() after each action, using
            # the solar power sensor for accurate surplus detection.
            _LOGGER.debug("Plan action: DISCHARGE")
            await self._start_discharging()
        elif action == "solar_charge":
            true_surplus = self._calculate_true_solar_surplus()
            if (
                true_surplus is not None
                and true_surplus > 50
            ):
                _LOGGER.debug(
                    "Plan action: SOLAR_CHARGE - charging from solar surplus "
                    "(grid_power=%.0fW, true_surplus=%.0fW)",
                    self._grid_power, true_surplus,
                )
                await self._start_solar_charging(true_surplus)
            elif (
                self._allow_discharging
                and self._grid_power is not None
                and self._grid_power > 50
                and not any(c["active"] for c in self._chargers)
                and self._battery_soc > self._min_soc
            ):
                _LOGGER.debug("Plan action: SOLAR_CHARGE - discharging to cover grid import")
                await self._start_discharging()
            else:
                _LOGGER.debug("Plan action: SOLAR_CHARGE - idle (no surplus/full)")
                await self._set_mode_idle()
        elif action in ("hold", "idle"):
            if await self._try_solar_opportunistic():
                _LOGGER.debug(
                    "Plan action: %s - but charging from solar surplus",
                    action.upper(),
                )
            else:
                _LOGGER.debug("Plan action: %s", action.upper())
                await self._set_mode_idle()
        else:
            if not await self._try_solar_opportunistic():
                _LOGGER.debug("Plan action: IDLE (unknown action: %s)", action)
                await self._set_mode_idle()

    async def _run_price_optimization(self) -> None:
        """Main price optimization logic using the battery plan."""
        if self._battery_soc is None:
            _LOGGER.debug("Missing SOC data, staying idle")
            await self._set_mode_idle()
            return

        planned_action = self._get_current_plan_action()
        if planned_action:
            await self._execute_plan_action(planned_action)
            return

        if self._current_price is None:
            _LOGGER.debug("No plan and no price data, staying idle")
            await self._set_mode_idle()
            return

        is_cheap = self._is_in_cheap_window()
        is_expensive = self._is_in_expensive_window()

        if is_cheap and self._battery_soc < self._max_soc and self._allow_grid_charging:
            await self._start_charging()
        elif is_expensive and self._battery_soc > self._min_soc and self._allow_discharging:
            await self._start_discharging()
        else:
            await self._set_mode_idle()
