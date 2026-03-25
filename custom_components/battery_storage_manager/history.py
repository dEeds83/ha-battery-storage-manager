"""Action history and optimization log mixin for Battery Storage Manager."""

from __future__ import annotations

import logging

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


class HistoryMixin:
    """Mixin providing action history and optimization log methods."""

    async def _record_action_history(self) -> None:
        """Record current state to action history (1 per 10 min, 48h, persistent)."""
        if not self._action_history_loaded:
            stored = await self._action_history_store.async_load()
            if stored and isinstance(stored, list):
                self._action_history = stored
            self._action_history_loaded = True

        now = dt_util.now()
        rounded_min = (now.minute // 10) * 10
        interval_key = now.strftime(f"%Y-%m-%dT%H:{rounded_min:02d}")

        if interval_key == self._action_history_last_key:
            return

        self._action_history_last_key = interval_key

        planned = self._get_current_plan_action() or "none"
        entry = {
            "time": interval_key,
            "mode": self._operating_mode,
            "planned": planned,
            "soc": round(self._battery_soc, 1) if self._battery_soc else None,
            "price": round(self._current_price * 100, 1) if self._current_price else None,
            "grid_w": round(self._grid_power) if self._grid_power is not None else None,
            "solar_w": round(self._solar_power) if self._solar_power is not None else None,
            "version": self._version,
        }
        self._action_history.append(entry)

        max_entries = 288
        if len(self._action_history) > max_entries:
            self._action_history = self._action_history[-max_entries:]

        await self._action_history_store.async_save(self._action_history)

    def _log_optimization(self, message: str) -> None:
        """Add an entry to the optimization log (visible in UI)."""
        now = dt_util.now()
        entry = f"{now.strftime('%H:%M:%S')} {message}"
        self._optimization_log.append(entry)
        if len(self._optimization_log) > self._max_log_entries:
            self._optimization_log = self._optimization_log[-self._max_log_entries:]
