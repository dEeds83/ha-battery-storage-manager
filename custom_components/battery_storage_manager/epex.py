"""EPEX Predictor mixin for Battery Storage Manager."""

from __future__ import annotations

import logging
from datetime import datetime

import aiohttp

from homeassistant.util import dt as dt_util

from .const import (
    EPEX_PREDICTOR_BASE_URL,
)

_LOGGER = logging.getLogger(__name__)


class EpexMixin:
    """Mixin providing EPEX price prediction methods for the coordinator."""

    async def _extend_prices_with_epex(self) -> None:
        """Use EPEX Predictor to set DP terminal value and provide visualization data."""
        if not self._epex_enabled or not self._price_forecast:
            self._epex_terminal_value_per_kwh = 0.0
            return

        now = dt_util.now()
        if (self._epex_cache_time is None
                or now - self._epex_cache_time > self._epex_cache_ttl):
            await self._fetch_epex_prices()

        if not self._epex_cache:
            self._epex_terminal_value_per_kwh = 0.0
            return

        tibber_end = None
        for p in reversed(self._price_forecast):
            try:
                tibber_end = datetime.fromisoformat(p["start"])
                if tibber_end.tzinfo is not None:
                    tibber_end = dt_util.as_local(tibber_end)
                break
            except (ValueError, TypeError):
                continue

        if tibber_end is None:
            self._epex_terminal_value_per_kwh = 0.0
            return

        tibber_by_hour: dict[str, list[float]] = {}
        for p in self._price_forecast:
            try:
                start = datetime.fromisoformat(p["start"])
                if start.tzinfo is not None:
                    start = dt_util.as_local(start)
                hk = start.strftime("%Y-%m-%dT%H")
                tibber_by_hour.setdefault(hk, []).append(p["total"])
            except (ValueError, TypeError):
                continue

        epex_by_hour: dict[str, list[float]] = {}
        epex_future: list[dict] = []
        for p in self._epex_cache:
            try:
                start = datetime.fromisoformat(p["start"])
                if start.tzinfo is not None:
                    start = dt_util.as_local(start)
                hk = start.strftime("%Y-%m-%dT%H")
                epex_by_hour.setdefault(hk, []).append(p["total"])
                if start > tibber_end:
                    epex_future.append(p)
            except (ValueError, TypeError):
                continue

        if not epex_future:
            self._epex_terminal_value_per_kwh = 0.0
            return

        pairs: list[tuple[float, float]] = []
        for hk in tibber_by_hour:
            if hk in epex_by_hour:
                t_avg = sum(tibber_by_hour[hk]) / len(tibber_by_hour[hk])
                e_avg = sum(epex_by_hour[hk]) / len(epex_by_hour[hk])
                pairs.append((e_avg, t_avg))

        if len(pairs) < 3:
            self._epex_terminal_value_per_kwh = 0.0
            return

        n_pairs = len(pairs)
        x_vals = [p[0] for p in pairs]
        y_vals = [p[1] for p in pairs]
        x_mean = sum(x_vals) / n_pairs
        y_mean = sum(y_vals) / n_pairs

        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, y_vals))
        den = sum((x - x_mean) ** 2 for x in x_vals)

        if abs(den) < 1e-10:
            b = 1.0
            a = y_mean - x_mean
        else:
            b = num / den
            a = y_mean - b * x_mean

        b = max(0.5, min(3.0, b))
        a = max(-0.05, min(0.50, a))

        self._epex_markup = {"a": round(a, 4), "b": round(b, 4)}

        self._epex_visualization = []
        for p in epex_future:
            predicted = a + b * p["total"]
            self._epex_visualization.append({
                "start": p["start"],
                "total": round(max(0, predicted), 4),
                "source": "epex_predictor",
                "epex_spot": round(p["total"], 4),
            })

        predicted_prices = sorted(max(0, a + b * p["total"]) for p in epex_future)
        median_future = predicted_prices[len(predicted_prices) // 2]
        efficiency = self._battery_efficiency
        half_cycle = self._cycle_cost / 100 / 2
        uncertainty_discount = 0.8
        self._epex_terminal_value_per_kwh = max(
            0.0,
            median_future * efficiency * uncertainty_discount - half_cycle
        )

        epex_sig = f"{len(epex_future)}:{a:.4f}:{b:.4f}:{self._epex_terminal_value_per_kwh:.4f}"
        if epex_sig != getattr(self, "_last_epex_signature", ""):
            self._last_epex_signature = epex_sig
            tv_ct = self._epex_terminal_value_per_kwh * 100
            msg = (
                f"EPEX Terminal-Value: {self._fmt_ct(tv_ct)} ct/kWh "
                f"(Median Zukunft {self._fmt_ct(median_future * 100)} ct, "
                f"Unsicherheit 20%, "
                f"Regression: {self._fmt_ct(a * 100)} ct + {b:.2f}\u00d7EPEX)"
            )
            _LOGGER.info(msg)
            self._log_optimization(msg)

    async def _fetch_epex_prices(self) -> None:
        """Fetch price predictions from EPEX Predictor API."""
        now = dt_util.now()
        url = (
            f"{EPEX_PREDICTOR_BASE_URL}/prices"
            f"?region={self._epex_region}"
            f"&unit=EUR_PER_KWH"
            f"&timezone=Europe/Berlin"
            f"&hours=96"
        )

        try:
            session = aiohttp.ClientSession()
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        _LOGGER.warning("EPEX API returned status %d", resp.status)
                        return
                    data = await resp.json()
            finally:
                await session.close()

            prices = data.get("prices", [])
            if not prices:
                _LOGGER.debug("EPEX API returned no prices")
                return

            self._epex_cache = []
            for entry in prices:
                if "startsAt" in entry and "total" in entry:
                    self._epex_cache.append({
                        "start": entry["startsAt"],
                        "total": entry["total"],
                    })

            self._epex_cache_time = now
            _LOGGER.debug(
                "EPEX cache refreshed: %d entries for region %s",
                len(self._epex_cache), self._epex_region,
            )

        except aiohttp.ClientError as err:
            _LOGGER.warning("EPEX API request failed: %s", err)
        except Exception:
            _LOGGER.warning("EPEX API error", exc_info=True)

        _LOGGER.debug("Price forecast built with %d entries", len(self._price_forecast))
