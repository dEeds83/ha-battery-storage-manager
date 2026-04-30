"""Device control mixin for Battery Storage Manager."""

from __future__ import annotations

import logging

from homeassistant.util import dt as dt_util

from .const import (
    CHARGER_TYPE_DIMMER,
    CHARGER_TYPE_SWITCH,
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
            ctype = charger.get("type", CHARGER_TYPE_SWITCH)
            if ctype == CHARGER_TYPE_DIMMER:
                # Read setpoint from number; active = setpoint > 0.
                power_entity = charger.get("power_entity", "")
                if power_entity:
                    pe_state = self.hass.states.get(power_entity)
                    if pe_state and pe_state.state not in ("unknown", "unavailable"):
                        try:
                            charger["target_power"] = float(pe_state.state)
                            charger["active"] = charger["target_power"] > 0
                        except (ValueError, TypeError):
                            pass
                # Optional enable-switch sync.
                enable_switch = charger.get("switch", "")
                if enable_switch:
                    sw_state = self.hass.states.get(enable_switch)
                    if sw_state and sw_state.state not in ("unknown", "unavailable"):
                        sw_on = sw_state.state == "on"
                        # If disabled externally, mark inactive even with non-zero target.
                        if not sw_on:
                            charger["active"] = False
                continue

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

    async def _apply_solar_price_gate(self) -> None:
        """Schalte PV-Anlagen ab solange Strompreis negativ ist.

        Negativer Preis = Einspeisen kostet Geld und Netz-Ladung
        (force_charge / Plan-Charge) wird durch Eigen-PV verdünnt.
        Bei Preis ≥ 0 oder unbekanntem Preis bleiben/sind die
        Schalter wieder aktiv. Idempotent: jede Iteration vergleicht
        Soll mit Ist-State und sendet nur bei Abweichung den Service.
        """
        if not self._solar_switches:
            return

        # Runtime-Toggle aus: falls vorher pausiert, wieder einschalten,
        # damit kein PV-Switch dauerhaft "stranded off" bleibt.
        if not self._allow_solar_pv_gate:
            if self._solar_switches_paused:
                for entity_id in self._solar_switches:
                    state = self.hass.states.get(entity_id)
                    if state is None or state.state in ("unknown", "unavailable"):
                        continue
                    if state.state == "on":
                        continue
                    await self.hass.services.async_call(
                        "switch", "turn_on", {"entity_id": entity_id}
                    )
                self._solar_switches_paused = False
            return

        if self._current_price is None:
            return

        desired_off = self._current_price < 0
        target_state = "off" if desired_off else "on"
        service = "turn_off" if desired_off else "turn_on"

        for entity_id in self._solar_switches:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            if state.state in ("unknown", "unavailable"):
                continue
            if state.state == target_state:
                continue
            await self.hass.services.async_call(
                "switch", service, {"entity_id": entity_id}
            )
            _LOGGER.info(
                "Solar-Schalter %s -> %s (Preis %.4f EUR/kWh)",
                entity_id, target_state, self._current_price,
            )

        if desired_off != self._solar_switches_paused:
            self._solar_switches_paused = desired_off

    async def _start_charging(self) -> None:
        """Activate chargers to charge the battery."""
        if self._operating_mode == MODE_CHARGING:
            return

        _LOGGER.info(
            "Starting battery charge (SOC: %.1f%%, Price: %.4f EUR/kWh)",
            self._battery_soc or 0,
            self._current_price or 0,
        )

        for i, charger in enumerate(self._chargers):
            await self._set_charger(i, charger.get("power", 0), on=True)

        self._reset_pid()
        if self._inverter_switch:
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": self._inverter_switch}
            )
        await self._set_inverter_power(0)
        self._inverter_active = False

        self._operating_mode = MODE_CHARGING

    async def _apply_charger_states(
        self,
        should_be_on: set[int],
        dimmer_targets: dict[int, float] | None = None,
    ) -> None:
        """Apply per-charger on/off (switch) or target power (dimmer).

        Switch-type respects min on/off hysteresis. Dimmer-type uses
        continuous setpoints — no hysteresis (no relay wear). When
        ``dimmer_targets`` is None, dimmers default to max power if in
        ``should_be_on``, otherwise 0.
        """
        now = dt_util.utcnow()
        dimmer_targets = dimmer_targets or {}
        for i, charger in enumerate(self._chargers):
            ctype = charger.get("type", CHARGER_TYPE_SWITCH)
            want_on = i in should_be_on

            if ctype == CHARGER_TYPE_DIMMER:
                target = dimmer_targets.get(
                    i, charger.get("power", 0) if want_on else 0
                )
                await self._set_dimmer_power(i, target if want_on else 0)
                continue

            if not charger.get("switch"):
                continue
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

            if self._inverter_switch:
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": self._inverter_switch}
                )

            self._inverter_active = True
            self._operating_mode = MODE_DISCHARGING

        # Retry charger-off every tick — min_on_time hysteresis may have
        # blocked the initial off attempt.
        if any(c["active"] for c in self._chargers):
            await self._apply_charger_states(set())

        if self._inverter_power_entity:
            await self._regulate_zero_feed()

    async def _regulate_zero_feed(self) -> None:
        """PID-regulated zero-feed control for the inverter.

        Uses EMA-smoothed grid power to dampen oscillation from
        toggle devices (fridge, heat pump, etc.).
        """
        # Use smoothed grid power for PID to avoid chasing toggle devices
        grid = self._grid_power_ema if self._grid_power_ema is not None else self._grid_power
        if grid is None:
            _LOGGER.debug("No grid power data available for zero-feed regulation")
            return

        # Settle-Throttle: Nach einem Write warten, bis der Wechselrichter
        # den Sollwert physisch umgesetzt hat. Asymmetrisch — Reduktionen
        # (Netz-Export) dürfen schon nach halber Settle-Zeit, weil
        # ungeförderte Einspeisung Verlust ist und schnell weg muss.
        # Hochregeln bekommt die volle Settle-Zeit, weil Überschwinger
        # dort weniger schmerzen als Oszillation.
        now_ts = dt_util.utcnow().timestamp()
        if self._inverter_last_write_ts is not None:
            elapsed = now_ts - self._inverter_last_write_ts
            is_reduction = grid < -10
            min_wait = (
                self._inverter_settle_seconds / 2
                if is_reduction
                else self._inverter_settle_seconds
            )
            if elapsed < min_wait:
                return

        max_power = self._inverter_power or 800

        if grid < -10:
            # Exporting to grid — reduce inverter proportionally.
            # Use the PID setpoint approach instead of subtracting raw
            # export, to avoid oscillation and slamming target to 0.
            export_w = abs(grid)
            new_target = self._inverter_target_power - export_w * 0.5
            # Kein Min-Export erzwingen: Einspeisung ohne Vergütung ist Verlust.
            new_target = max(0, new_target)
            _LOGGER.info(
                "Zero-feed: EXPORT %.0fW (raw %.0fW) -> reducing inverter %.0f -> %.0fW",
                export_w, abs(self._grid_power or 0),
                self._inverter_target_power, new_target,
            )
        elif grid <= 10:
            return
        else:
            setpoint = 25
            error = grid - setpoint

            if error > 100:
                new_target = self._inverter_target_power + error * 0.9
                _LOGGER.debug(
                    "Zero-feed FAST: import=%.0fW (raw %.0fW) -> inverter=%.0fW",
                    grid, self._grid_power or 0, new_target,
                )
                self._pid_integral = 0.0
                self._pid_last_error = None
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
                    "Zero-feed PID: grid=%.0fW (raw %.0fW) P=%.0f I=%.0f D=%.0f -> %.0fW",
                    grid, self._grid_power or 0, p_term, i_term, d_term, new_target,
                )

        # During discharge, keep at least 50W to prevent the inverter from
        # effectively shutting down.  The PID will ramp back up on the next
        # cycle when house consumption pulls from the grid again.
        min_target = 0  # kein Min-Export erzwingen (Einspeisung wertlos)
        new_target = max(min_target, min(max_power, new_target))

        if abs(new_target - self._inverter_target_power) < 10:
            return

        self._inverter_target_power = new_target
        self._inverter_last_write_ts = now_ts

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
        self._inverter_last_write_ts = dt_util.utcnow().timestamp()
        domain = self._inverter_power_entity.split(".")[0]
        await self.hass.services.async_call(
            domain,
            "set_value",
            {
                "entity_id": self._inverter_power_entity,
                "value": value,
            },
        )

    async def _set_dimmer_power(self, idx: int, value_w: float) -> None:
        """Set a dimmer-type charger to a target power.

        Mirrors `_set_inverter_power`: clamps to [0, max_power], applies
        min_power cutoff, deadband, and writes via number `set_value`.
        Optional enable-switch wird einmalig eingeschaltet, aber NICHT bei
        target=0 wieder ausgeschaltet — Netzteil bleibt an und wird auf 0
        geregelt (kein ständiges On/Off-Toggling). Komplettes Aus passiert
        nur via stop_all().
        """
        if idx < 0 or idx >= len(self._chargers):
            return
        c = self._chargers[idx]
        if c.get("type") != CHARGER_TYPE_DIMMER:
            return
        power_entity = c.get("power_entity", "")
        if not power_entity:
            return

        max_p = c.get("power", 0) or 0
        min_p = c.get("min_power", 0) or 0
        target = max(0, min(max_p, round(value_w)))
        if 0 < target < min_p:
            target = 0

        # Optional enable-switch nur einschalten wenn nötig, nie ausschalten.
        # Auch bei state="unavailable" turn_on senden — Steckdose könnte
        # offline sein und reagiert dann sobald erreichbar.
        enable_switch = c.get("switch", "")
        want_on = target > 0
        if enable_switch and want_on:
            sw_state = self.hass.states.get(enable_switch)
            if sw_state is None or sw_state.state != "on":
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": enable_switch},
                )

        # Deadband: skip tiny changes.
        if abs(target - (c.get("target_power") or 0)) < 10:
            c["active"] = want_on
            return

        domain = power_entity.split(".")[0]
        await self.hass.services.async_call(
            domain,
            "set_value",
            {
                "entity_id": power_entity,
                "value": target,
            },
        )
        c["target_power"] = float(target)
        c["active"] = want_on

    async def _set_charger(self, idx: int, target_w: float, on: bool) -> None:
        """Dispatch a charger update by type.

        - switch-type: turn the relay on/off via service call (no power).
        - dimmer-type: set continuous power; 0 W when ``on`` is False.
        """
        if idx < 0 or idx >= len(self._chargers):
            return
        c = self._chargers[idx]
        if c.get("type") == CHARGER_TYPE_DIMMER:
            await self._set_dimmer_power(idx, target_w if on else 0)
        else:
            if not c.get("switch"):
                return
            await self.hass.services.async_call(
                "switch",
                "turn_on" if on else "turn_off",
                {"entity_id": c["switch"]},
            )
            c["active"] = on

    async def _regulate_dimmer_zero_feed(self) -> None:
        """One-step adjust the dimmer setpoint to zero out grid power.

        Uses the existing EMA-smoothed grid power. No PID needed because the
        dimmer is a load that consumes exactly what it's told.
        Positive grid (import) → reduce dimmer; negative (export) → raise.

        WR wird in diesem Modus garantiert ausgeschaltet — Dimmer absorbiert
        Solar exakt, keine WR-Kompensation nötig (kein Round-Trip).
        """
        # WR muss aus sein. Schaltet ihn ab falls aus früherer Phase noch an.
        if self._inverter_active or (self._inverter_target_power or 0) > 0:
            if self._inverter_switch:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": self._inverter_switch}
                )
            await self._set_inverter_power(0)
            self._inverter_active = False

        # Find the (single) dimmer.
        idx = next(
            (i for i, c in enumerate(self._chargers)
             if c.get("type") == CHARGER_TYPE_DIMMER),
            -1,
        )
        if idx < 0:
            return
        grid = (
            self._grid_power_ema if self._grid_power_ema is not None
            else self._grid_power
        )
        if grid is None:
            return
        c = self._chargers[idx]
        current = c.get("target_power") or 0.0
        new_target = self._dimmer_zero_feed_step(current, grid)
        if new_target is None:
            return
        await self._set_dimmer_power(idx, new_target)

    @staticmethod
    def _dimmer_zero_feed_step(current: float, grid: float) -> float | None:
        """Compute next dimmer setpoint to converge on grid in 0..25 W.

        Konservativ ausgelegt um Oszillation zu vermeiden:
        - Deadband 0..25 W → keine Änderung (Toleranzfenster).
        - Setpoint 12 W (Mitte). Gain 0.5 (vorher 0.8 → Überschwingen).
        - Slew-Rate-Limit: max ±200 W pro Schritt.
        Returns None wenn keine Änderung nötig.
        """
        if 0 <= grid <= 25:
            return None
        setpoint = 12
        delta = (setpoint - grid) * 0.5
        # Slew-Rate
        if delta > 200:
            delta = 200
        elif delta < -200:
            delta = -200
        return current + delta

    async def _start_solar_charging(self, surplus_w: float) -> None:
        """Activate chargers proportionally to available solar surplus."""
        if not getattr(self, "_allow_solar_charging", True):
            await self._set_mode_idle()
            return
        # Dimmer mode: single continuous load, take min(surplus, max).
        if any(c.get("type") == CHARGER_TYPE_DIMMER for c in self._chargers):
            idx = next(
                i for i, c in enumerate(self._chargers)
                if c.get("type") == CHARGER_TYPE_DIMMER
            )
            c = self._chargers[idx]
            target = max(0, min(c.get("power", 0), int(surplus_w)))
            await self._set_dimmer_power(idx, target)
            # WR aus / 0 — Dimmer absorbiert exakt, kein Round-Trip.
            if self._inverter_switch and self._inverter_active:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": self._inverter_switch}
                )
                self._inverter_active = False
            await self._set_inverter_power(0)
            self._operating_mode = MODE_SOLAR_CHARGING
            _LOGGER.info(
                "Solar charging (dimmer): surplus=%.0fW → target=%dW",
                surplus_w, target,
            )
            return

        if not self._chargers:
            await self._set_mode_idle()
            return

        indexed = [(i, c) for i, c in enumerate(self._chargers) if c["power"] > 0]
        indexed.sort(key=lambda x: x[1]["power"], reverse=True)

        # SOC-Limit gilt nur für Netzladen (DP-Plan via grid_max_soc), nicht
        # für Solar-Überschuss. WR-Hilfe auch oberhalb max_soc erlaubt.
        has_inverter = bool(self._inverter_power_entity)
        max_inverter = self._inverter_power or 800

        # Select chargers: add if surplus covers >= 80% of charger power,
        # OR if inverter can cover the deficit without exceeding charger power
        # (otherwise we'd be pointlessly cycling energy through the battery).
        selected: set[int] = set()
        remaining = surplus_w
        for idx, charger in indexed:
            power = charger["power"]
            if remaining >= power * 0.8:
                # Surplus covers most of this charger
                selected.add(idx)
                remaining -= power
            elif has_inverter and remaining >= 50:
                # Add charger if the net energy gain (after inverter round-trip
                # losses) is positive:  remaining > deficit × (1 - efficiency).
                # This simplifies to: remaining > power × (1-eff) / (2-eff).
                eff = getattr(self, "_battery_efficiency", 0.85) or 0.85
                min_remaining = power * (1 - eff) / (2 - eff)
                total_draw = sum(self._chargers[i]["power"] for i in selected) + power
                deficit = total_draw - surplus_w
                if remaining >= min_remaining and deficit <= max_inverter:
                    selected.add(idx)
                    remaining -= power

        if not selected:
            if surplus_w >= 50 and has_inverter:
                # Even the smallest charger needs inverter help.
                # Only if net gain is positive after round-trip losses.
                smallest = min(indexed, key=lambda x: x[1]["power"])
                smallest_idx, smallest_charger = smallest
                eff = getattr(self, "_battery_efficiency", 0.85) or 0.85
                min_surplus = smallest_charger["power"] * (1 - eff) / (2 - eff)
                deficit = smallest_charger["power"] - surplus_w
                if surplus_w >= min_surplus and deficit <= max_inverter:
                    selected = {smallest_idx}
                    _LOGGER.debug(
                        "Solar surplus %.0fW < smallest charger (%dW) - "
                        "PID will compensate deficit %.0fW",
                        surplus_w, smallest_charger["power"], deficit,
                    )
            if not selected:
                _LOGGER.debug(
                    "Solar surplus %.0fW too low for any charger",
                    surplus_w,
                )
                await self._set_mode_idle()
                return

        await self._apply_charger_states(selected)

        # PID zero-feed regulation compensates grid import.
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
        """Prevent grid export by turning on chargers and adjusting inverter.

        Simple rule: if we're exporting to the grid, use that energy.
        1. If grid < -100W and a charger is off → turn it on
        2. If grid > 200W and a charger is on → turn it off
        3. PID handles inverter fine-tuning for zero-feed

        No complex surplus calculation — just react to actual grid state.
        """
        if self._grid_power is None or self._battery_soc is None:
            return False

        # Master switch: solar charging globally disabled → ensure all
        # chargers are off and exit. Excess solar is exported.
        if not getattr(self, "_allow_solar_charging", True):
            if any(c["active"] for c in self._chargers) or self._inverter_active:
                await self._set_mode_idle()
            return False

        # Dimmer mode: continuous absorb via single number-entity. No PID,
        # no greedy switch logic, no WR-roundtrip.
        if any(c.get("type") == CHARGER_TYPE_DIMMER for c in self._chargers):
            idx = next(
                (i for i, c in enumerate(self._chargers)
                 if c.get("type") == CHARGER_TYPE_DIMMER),
                -1,
            )
            # Discharge-Mode: WR liefert via PID, Dimmer auf 0 — ausser
            # Solar-Export besteht trotz WR=0. Dann absorbiert Dimmer den
            # Rest (unabhängig vom SOC, da Einspeisung wertlos ist).
            if self._operating_mode == MODE_DISCHARGING:
                if idx < 0:
                    return False
                grid = (
                    self._grid_power_ema if self._grid_power_ema is not None
                    else self._grid_power
                )
                inverter_target = self._inverter_target_power or 0
                # WR ist effektiv auf 0 UND es geht trotzdem Strom raus →
                # Dimmer aufdrehen statt verschenken. WR komplett aus,
                # spart Standby-Verbrauch.
                if grid is not None and grid < -50 and inverter_target <= 5:
                    current = self._chargers[idx].get("target_power") or 0.0
                    nt = self._dimmer_zero_feed_step(current, grid)
                    if nt is not None:
                        await self._set_dimmer_power(idx, nt)
                    if self._inverter_active:
                        if self._inverter_switch:
                            await self.hass.services.async_call(
                                "switch", "turn_off",
                                {"entity_id": self._inverter_switch},
                            )
                        await self._set_inverter_power(0)
                        self._inverter_active = False
                else:
                    if (self._chargers[idx].get("target_power") or 0) > 0:
                        await self._set_dimmer_power(idx, 0)
                    # WR wieder einschalten falls Solar nicht mehr reicht.
                    if self._inverter_switch and not self._inverter_active:
                        await self.hass.services.async_call(
                            "switch", "turn_on",
                            {"entity_id": self._inverter_switch},
                        )
                        self._inverter_active = True
                return False
            await self._regulate_dimmer_zero_feed()
            # Mode reflects whether dimmer is actively absorbing.
            if any(c.get("active") for c in self._chargers):
                self._operating_mode = MODE_SOLAR_CHARGING
            return True

        active_chargers = [i for i, c in enumerate(self._chargers) if c["active"]]
        inactive_chargers = [
            i for i, c in enumerate(self._chargers)
            if not c["active"] and c["power"] > 0
        ]

        # Only turn on chargers if solar is actually producing enough
        # to justify it. Without this check, inverter PID overshoot
        # (grid < -100) would trigger charger activation at peak prices.
        solar_w = self._solar_power or 0
        min_charger_power = min(
            (c["power"] for c in self._chargers if c["power"] > 0),
            default=440,
        )
        solar_sufficient = solar_w > min_charger_power * 0.3

        # Allow solar charging above max_soc — free energy should never
        # be wasted. SOC-Limit gilt nur für Netzladen (DP-Plan via
        # grid_max_soc), nicht für Solar-Überschuss.
        max_inverter = self._inverter_power or 800
        has_inverter = bool(self._inverter_power_entity)
        next_charger_power = (
            self._chargers[inactive_chargers[0]]["power"]
            if inactive_chargers else 0
        )
        eff = getattr(self, "_battery_efficiency", 0.85) or 0.85
        # Net-gain threshold: export > power × (1-eff)/(2-eff).
        # With eff=0.85: ~13% of charger power. Below that, WR-Hilfe kostet
        # mehr als sie einbringt.
        min_export_netgain = int(next_charger_power * (1 - eff) / (2 - eff))
        export_w = max(0, -self._grid_power)

        # Enable turn-on when:
        #  a) Export ≥ 80% charger → direct solar charging, or
        #  b) Export ≥ net-gain threshold AND WR available AND deficit ≤ WR max
        #     → round-trip via WR (battery gains net energy)
        direct_ok = export_w >= next_charger_power * 0.8
        deficit = next_charger_power - export_w
        wr_ok = (
            has_inverter and export_w >= min_export_netgain
            and deficit <= max_inverter
        )
        if (inactive_chargers and solar_sufficient and (direct_ok or wr_ok)):
            next_idx = inactive_chargers[0]
            reason = "direct" if direct_ok else f"WR-assist (deficit {deficit:.0f}W)"
            _LOGGER.info(
                "Grid export %.0fW → turning on charger C%d (%dW) [%s]",
                export_w, next_idx + 1, next_charger_power, reason,
            )
            await self._apply_charger_states(
                {i for i, c in enumerate(self._chargers) if c["active"]}
                | {next_idx}
            )

            # Only set SOLAR_CHARGING + WR on if charger actually turned on
            # (hysteresis may have blocked). Otherwise leave state untouched
            # so caller can retry next tick.
            if not self._chargers[next_idx]["active"]:
                _LOGGER.debug(
                    "Charger C%d turn-on blocked (hysteresis) — skip mode change",
                    next_idx + 1,
                )
                return False

            # Ensure inverter is on for PID zero-feed (only needed for WR-assist)
            if wr_ok and self._inverter_power_entity and not self._inverter_active:
                if self._inverter_switch:
                    await self.hass.services.async_call(
                        "switch", "turn_on", {"entity_id": self._inverter_switch}
                    )
                self._inverter_active = True

            self._operating_mode = MODE_SOLAR_CHARGING
            return True

        if active_chargers and self._operating_mode == MODE_SOLAR_CHARGING:
            if not solar_sufficient:
                # Solar dropped below threshold → turn off all chargers
                _LOGGER.info(
                    "Solar %.0fW too low for chargers → turning off all",
                    solar_w,
                )
                await self._apply_charger_states(set())
                if not any(c["active"] for c in self._chargers):
                    await self._set_mode_idle()
                return True

            # Turn off last charger if inverter compensation exceeds its
            # power draw. PID keeps grid≈0, so grid_power is not useful as
            # signal. Instead: if inverter discharges more than the last
            # charger consumes, we're round-tripping battery energy through
            # the house (with efficiency losses) — turn charger off.
            last_idx = active_chargers[-1]
            last_power = self._chargers[last_idx]["power"]
            inverter_tp = self._inverter_target_power or 0
            # Hysteresis margin (50W) to prevent toggling at boundary.
            if inverter_tp > last_power + 50:
                _LOGGER.info(
                    "Inverter %.0fW > C%d draw %dW+50 → round-trip loss, "
                    "turning off C%d",
                    inverter_tp, last_idx + 1, last_power, last_idx + 1,
                )
                await self._apply_charger_states(
                    set(active_chargers) - {last_idx}
                )
                if not any(c["active"] for c in self._chargers):
                    await self._set_mode_idle()
                return True

            # Sustained grid-import while WR is also discharging from battery
            # → Round-Trip-Verlust: WR pumpt aus Batterie, Charger zahlt Netz,
            # Solar reicht nicht für Charger + Haus. Letzten Charger weg.
            inverter_actual = self._inverter_actual_power or 0
            if self._grid_power > 100 and inverter_actual > 50:
                _LOGGER.info(
                    "Grid import %.0fW + inverter %.0fW (battery discharge) "
                    "→ round-trip loss, turning off C%d",
                    self._grid_power, inverter_actual, last_idx + 1,
                )
                await self._apply_charger_states(
                    set(active_chargers) - {last_idx}
                )
                if not any(c["active"] for c in self._chargers):
                    await self._set_mode_idle()
                return True

            # Fallback: hard grid import (PID saturated) → also turn off.
            if self._grid_power > max_inverter + 200:
                _LOGGER.info(
                    "Grid import %.0fW > inverter max %dW+200 → turning off C%d",
                    self._grid_power, max_inverter, last_idx + 1,
                )
                await self._apply_charger_states(
                    set(active_chargers) - {last_idx}
                )
                if not any(c["active"] for c in self._chargers):
                    await self._set_mode_idle()
                return True

            # PID zero-feed while solar charging
            if self._inverter_power_entity:
                await self._regulate_zero_feed()
            return True

        return False

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

            # When PID inverter is active, the grid-based formula
            # (-grid - inverter + chargers) degenerates because PID
            # keeps grid≈0, making it just (chargers - inverter).
            # This causes feedback oscillation.
            #
            # Instead, use grid_power directly as surplus indicator:
            # - No chargers/inverter active: surplus = max(0, -grid_power)
            # - Chargers active: surplus = active_draw + max(0, -grid_power)
            #   (what chargers already consume + what's still exported)
            # - Inverter active (PID): same logic, inverter compensates
            #   house consumption so grid reflects solar vs (house+chargers)
            active_draw = sum(
                self._charger_active_draw_w(c)
                for c in self._chargers if c["active"]
            )
            # What the chargers already capture + what's still exported
            export = max(0, -self._grid_power)
            surplus = active_draw + export
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
        if action == "charge":
            # Plan will Netzladen — gilt nur unterhalb max_soc.
            if self._battery_soc >= self._max_soc:
                # max_soc gilt nur für Netzladen. Solar-Absorption über
                # max_soc weiterhin erlaubt (z.B. bei Dimmer).
                _LOGGER.debug(
                    "Plan action: CHARGE — SOC≥max_soc, fall back to solar-absorb"
                )
                if not await self._try_solar_opportunistic():
                    await self._set_mode_idle()
                return
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
            # Plan-Discharge gewinnt: kein Round-Trip via Dimmer-Absorbing.
            # PID-Zero-Feed regelt WR auf grid≈0; bei Solar-Überschuss geht
            # WR auf 0 (min_target=0, kein erzwungener Export).
            _LOGGER.debug("Plan action: DISCHARGE")
            await self._start_discharging()
        elif action == "solar_charge":
            # Solar charge action: use the same grid-based logic
            if not await self._try_solar_opportunistic():
                if (
                    self._allow_discharging
                    and self._grid_power is not None
                    and self._grid_power > 50
                    and not any(c["active"] for c in self._chargers)
                    and self._battery_soc > self._min_soc
                ):
                    _LOGGER.debug("Plan action: SOLAR_CHARGE - discharging to cover grid import")
                    await self._start_discharging()
                else:
                    _LOGGER.debug("Plan action: SOLAR_CHARGE - idle")
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
