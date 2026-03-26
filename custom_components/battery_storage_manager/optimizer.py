"""Dynamic-programming optimizer and post-processing smoothing pipeline.

This module contains the core scheduling logic for the Battery Storage Manager
integration.  It exposes two pure functions:

* ``solve_dp`` -- builds a backward-induction DP table over discretised SOC
  levels and extracts the profit-maximising charge/discharge/idle plan.
* ``smooth_plan`` -- applies a six-pass heuristic pipeline that cleans up DP
  artefacts (single-slot enclaves, rapid alternation, sub-optimal discharge
  placement, fragmented charge blocks, and timing of charge slots).

Both functions are side-effect-free and operate solely on the data passed in.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


def solve_dp(
    hourly_data: list[dict],
    n: int,
    current_soc: float,
    charge_kwh_slot: float,
    discharge_kwh_slot: float,
    cap: float,
    efficiency: float,
    cycle_cost_eur: float,
    slot_h: float,
    min_soc: float,
    max_soc: float,
    epex_terminal_value_per_kwh: float = 0.0,
    battery_efficiency: float = 0.9,
) -> tuple[list[str], float]:
    """Run the core dynamic-programming optimisation for battery scheduling.

    The algorithm works in three phases:

    1. **SOC discretisation** -- the continuous SOC range ``[min_soc, max_soc]``
       is quantised into evenly-spaced levels whose step size is derived from the
       per-slot charge/discharge energy.
    2. **Backward pass** -- starting from terminal values (residual energy
       valued at a blend of median-price and EPEX forward price), the DP table
       ``dp[t][soc_idx]`` is filled from the last slot back to slot 0.  At each
       ``(t, soc)`` the best action (charge, discharge, idle) is recorded.
       Charging ties at break-even are broken in favour of charging (``>=``).
    3. **Forward pass** -- the optimal action sequence is read out by walking
       forward from the current SOC through the recorded actions.

    Args:
        hourly_data: Per-slot dicts with at least ``"price"`` (EUR/kWh),
            ``"grid_fraction"`` and optionally ``"_scn_grid_frac"`` keys.
        n: Number of time slots in the planning horizon.
        current_soc: Current battery state-of-charge in percent.
        charge_kwh_slot: Maximum energy charged per slot (kWh).
        discharge_kwh_slot: Maximum energy discharged per slot (kWh).
        cap: Usable battery capacity (kWh).
        efficiency: Round-trip discharge efficiency (0-1).
        cycle_cost_eur: Estimated degradation cost per full cycle (EUR).
        slot_h: Duration of one slot in hours.
        min_soc: Minimum allowed SOC in percent.
        max_soc: Maximum allowed SOC in percent.
        epex_terminal_value_per_kwh: EPEX-based terminal value (EUR/kWh) for
            energy remaining in the battery at the end of the horizon.
        battery_efficiency: One-way battery efficiency (default 0.9).

    Returns:
        A tuple ``(actions, profit)`` where *actions* is a list of ``n``
        strings (each ``"charge"``, ``"discharge"``, or ``"idle"``) and
        *profit* is the estimated EUR profit of the plan (excluding the
        terminal value of the starting energy).
    """
    # SOC discretization
    charge_soc_pct = charge_kwh_slot / cap * 100 if cap > 0 else 5
    discharge_soc_pct = discharge_kwh_slot / cap * 100 if cap > 0 else 5
    min_delta_pct = min(charge_soc_pct, discharge_soc_pct) if charge_soc_pct > 0 else discharge_soc_pct
    soc_step = max(0.5, min(3.0, min_delta_pct * 0.45))
    soc_step = round(soc_step, 1) or 0.5

    soc_levels: list[float] = []
    s = float(min_soc)
    while s <= max_soc + 0.01:
        soc_levels.append(round(s, 1))
        s += soc_step
    num_soc = len(soc_levels)

    def soc_to_idx(soc: float) -> int:
        """Map a continuous SOC percentage to the nearest discretised index."""
        idx = round((soc - min_soc) / soc_step)
        return max(0, min(num_soc - 1, idx))

    half_cycle_eur = cycle_cost_eur / 2

    # Terminal value
    all_prices = sorted(h["price"] for h in hourly_data)
    median_price = all_prices[len(all_prices) // 2] if all_prices else 0.25
    uncertainty_discount = 0.7
    base_tv = max(0.0, median_price * efficiency * uncertainty_discount - half_cycle_eur)
    epex_tv = epex_terminal_value_per_kwh
    tv_per_kwh = max(base_tv, epex_tv)

    INF = float("-inf")
    dp = [[INF] * num_soc for _ in range(n + 1)]
    action_dp = [["idle"] * num_soc for _ in range(n)]

    for s_idx in range(num_soc):
        stored_kwh = (soc_levels[s_idx] - min_soc) / 100 * cap
        dp[n][s_idx] = stored_kwh * tv_per_kwh

    # Backward pass
    for t in range(n - 1, -1, -1):
        h = hourly_data[t]
        price = h["price"]
        grid_frac = h.get("_scn_grid_frac", h["grid_fraction"])

        for si in range(num_soc):
            soc = soc_levels[si]
            best_val = INF
            best_act = "idle"

            val = dp[t + 1][si]
            if val > best_val:
                best_val = val
                best_act = "idle"

            # Charge: use >= so that break-even ties prefer charging.
            if soc < max_soc and charge_kwh_slot > 0:
                delta = min(charge_kwh_slot, (max_soc - soc) / 100 * cap)
                new_soc = soc + delta / cap * 100
                new_si = soc_to_idx(new_soc)
                if new_si > si:
                    cost = delta * grid_frac * price + delta * half_cycle_eur
                    val = -cost + dp[t + 1][new_si]
                    if val >= best_val:
                        best_val = val
                        best_act = "charge"

            # Discharge: keep strict >
            if soc > min_soc and discharge_kwh_slot > 0:
                delta = min(discharge_kwh_slot, (soc - min_soc) / 100 * cap)
                delivered = delta * efficiency
                new_soc = soc - delta / cap * 100
                new_si = soc_to_idx(new_soc)
                if new_si < si:
                    revenue = delivered * price - delta * half_cycle_eur
                    val = revenue + dp[t + 1][new_si]
                    if val > best_val:
                        best_val = val
                        best_act = "discharge"

            dp[t][si] = best_val
            action_dp[t][si] = best_act

    # Forward pass
    start_si = soc_to_idx(current_soc)
    actions: list[str] = []
    current_si = start_si
    for t in range(n):
        act = action_dp[t][current_si]
        actions.append(act)
        soc = soc_levels[current_si]

        if act == "charge":
            delta = min(charge_kwh_slot, (max_soc - soc) / 100 * cap)
            new_soc = soc + delta / cap * 100
        elif act == "discharge":
            delta = min(discharge_kwh_slot, (soc - min_soc) / 100 * cap)
            new_soc = soc - delta / cap * 100
        else:
            new_soc = soc
        current_si = soc_to_idx(new_soc)

    # Profit = DP value - terminal value of starting energy
    start_stored_kwh = (current_soc - min_soc) / 100 * cap
    profit = dp[0][soc_to_idx(current_soc)] - start_stored_kwh * tv_per_kwh

    return actions, profit


def smooth_plan(
    actions: list[str],
    hourly_data: list[dict],
    n: int,
    efficiency: float,
    cycle_cost_eur: float,
    charge_kwh_slot: float,
    discharge_kwh_slot: float,
    cap: float,
    current_soc: float,
    min_soc: float,
    max_soc: float,
    slot_h: float,
) -> tuple[list[str], int]:
    """Apply a six-pass heuristic smoothing pipeline to raw DP actions.

    The DP solution is mathematically optimal on its discretised grid but can
    produce plans with artefacts that look erratic or waste switching cycles.
    This function cleans them up in six sequential passes (execution order):

    1. **Enclave removal:** Single-slot charge or discharge actions
       surrounded by different actions are replaced with idle.
    2. **Alternation dampening:** Back-to-back charge/discharge pairs
       whose price spread is below break-even are collapsed to idle.
    3. **Discharge slot swap:** Iteratively swaps the cheapest discharge
       slot with a more expensive idle slot later in time.
    4. **Charge-block merging:** Small satellite charge blocks at the
       same price are merged into the main (largest) block. Isolated
       blocks after the main block are removed.
    5. **Late-shift of charge blocks:** Charge slots are shifted to the
       latest available idle/hold slots at the same price, so charging
       happens as late as possible before discharge (room for solar).
    6. **Target-based backward fill (runs last):** For every discharge
       block whose entry SOC is below ``max_soc``, the cheapest idle
       slots before that block are converted to charge so the battery
       is full when discharge begins. Runs last so no subsequent pass
       can remove its additions.

    Args:
        actions: Mutable list of ``n`` action strings produced by ``solve_dp``.
            Modified in place **and** returned.
        hourly_data: Per-slot dicts with at least a ``"price"`` key (EUR/kWh).
        n: Number of time slots.
        efficiency: Round-trip discharge efficiency (0-1).
        cycle_cost_eur: Estimated degradation cost per full cycle (EUR).
        charge_kwh_slot: Maximum energy charged per slot (kWh).
        discharge_kwh_slot: Maximum energy discharged per slot (kWh).
        cap: Usable battery capacity (kWh).
        current_soc: Current battery SOC in percent.
        min_soc: Minimum allowed SOC in percent.
        max_soc: Maximum allowed SOC in percent.
        slot_h: Duration of one slot in hours.

    Returns:
        A tuple ``(actions, total_adjustments)`` where *actions* is the
        (mutated) input list and *total_adjustments* is the number of slot
        changes made across all passes.
    """
    smoothed = 0

    # Pass 1/6: Remove single-slot charge/discharge enclaves.
    # Keep enclaves that have a same-action slot within 2 positions
    # (these are part of a block with a 1-slot gap, not true noise).
    for i in range(1, n - 1):
        act = actions[i]
        if act in ("charge", "discharge"):
            prev_same = (actions[i - 1] == act)
            next_same = (actions[i + 1] == act)
            if not prev_same and not next_same:
                has_nearby = (
                    (i >= 2 and actions[i - 2] == act)
                    or (i + 2 < n and actions[i + 2] == act)
                )
                if not has_nearby:
                    actions[i] = "idle"
                    smoothed += 1

    # Pass 2/6: Remove rapid charge<->discharge alternation
    avg_plan_price = sum(h["price"] for h in hourly_data) / n if n else 0.25
    break_even_spread = cycle_cost_eur + (1 - efficiency) * avg_plan_price
    for i in range(1, n):
        prev_a, cur_a = actions[i - 1], actions[i]
        if (prev_a == "charge" and cur_a == "discharge") or \
           (prev_a == "discharge" and cur_a == "charge"):
            p_prev = hourly_data[i - 1]["price"]
            p_cur = hourly_data[i]["price"]
            spread = abs(p_cur - p_prev)
            if spread < break_even_spread:
                actions[i] = "idle"
                smoothed += 1

    # Pass 3/6: Swap cheap discharge slots with more expensive idle slots.
    swapped = 0
    while True:
        cheapest_d_idx = None
        cheapest_d_price = float("inf")
        for i in range(n):
            if actions[i] == "discharge" and hourly_data[i]["price"] < cheapest_d_price:
                cheapest_d_price = hourly_data[i]["price"]
                cheapest_d_idx = i

        best_idle_idx = None
        best_idle_price = 0.0
        for i in range(n):
            if actions[i] == "idle" and hourly_data[i]["price"] > best_idle_price:
                best_idle_price = hourly_data[i]["price"]
                best_idle_idx = i

        if (cheapest_d_idx is not None
                and best_idle_idx is not None
                and best_idle_price > cheapest_d_price + 0.01
                and best_idle_idx > cheapest_d_idx):
            actions[cheapest_d_idx] = "idle"
            actions[best_idle_idx] = "discharge"
            swapped += 1
        else:
            break

    smoothed += swapped

    # Pass 4/6: Merge separated charge blocks at same price.
    charge_blocks: list[tuple[int, int]] = []
    block_s = None
    for i in range(n):
        if actions[i] == "charge":
            if block_s is None:
                block_s = i
        else:
            if block_s is not None:
                charge_blocks.append((block_s, i - block_s))
                block_s = None
    if block_s is not None:
        charge_blocks.append((block_s, n - block_s))

    removed_islands = 0
    if len(charge_blocks) > 1:
        main_block = max(charge_blocks, key=lambda b: b[1])
        main_start, main_len = main_block
        main_price = hourly_data[main_start]["price"] if main_start < n else 0

        for start, length in charge_blocks:
            if (start, length) == main_block:
                continue
            block_price = hourly_data[start]["price"] if start < n else 0

            if start < main_start and abs(block_price - main_price) < 0.005:
                gap_slots: list[int] = []
                for j in range(start + length, main_start):
                    if actions[j] in ("idle", "hold"):
                        p = hourly_data[j]["price"]
                        if abs(p - main_price) < 0.02:
                            gap_slots.append(j)

                if len(gap_slots) >= length:
                    for j in range(start, start + length):
                        actions[j] = "idle"
                        removed_islands += 1
                    gap_slots.sort(reverse=True)
                    for j in gap_slots[:length]:
                        actions[j] = "charge"
                    _LOGGER.info(
                        "Pass 4: merged %d charge slots from t=%d "
                        "into main block (shifted to latest slots)",
                        length, start,
                    )
            elif start >= main_start + main_len:
                main_end = main_start + main_len
                gap = start - main_end
                if length < 4 and gap > 2:
                    for j in range(start, start + length):
                        actions[j] = "idle"
                        removed_islands += 1
            else:
                main_end = main_start + main_len
                gap = min(abs(start - main_end),
                          abs(main_start - (start + length)))
                if length < 4 and gap > 2:
                    for j in range(start, start + length):
                        actions[j] = "idle"
                        removed_islands += 1

        if removed_islands:
            smoothed += removed_islands
            _LOGGER.info(
                "Pass 4: adjusted %d charge slots total",
                removed_islands,
            )

    # Pass 5/6: Shift charge block to latest position within same price band.
    charge_blocks_final: list[tuple[int, int]] = []
    block_s = None
    for i in range(n):
        if actions[i] == "charge":
            if block_s is None:
                block_s = i
        else:
            if block_s is not None:
                charge_blocks_final.append((block_s, i - block_s))
                block_s = None
    if block_s is not None:
        charge_blocks_final.append((block_s, n - block_s))

    shifted = 0
    for cb_start, cb_len in charge_blocks_final:
        cb_end = cb_start + cb_len
        cb_price = hourly_data[cb_start]["price"] if cb_start < n else 0

        tail_slots: list[int] = []
        for j in range(cb_end, n):
            if actions[j] == "discharge":
                break
            if actions[j] in ("idle", "hold"):
                p = hourly_data[j]["price"]
                if abs(p - cb_price) < 0.003:
                    tail_slots.append(j)
                elif p > cb_price + 0.003:
                    break

        if not tail_slots:
            continue

        shift_count = min(len(tail_slots), cb_len)
        if shift_count == 0:
            continue

        slots_to_free = list(range(cb_start, cb_start + shift_count))
        tail_slots.sort(reverse=True)
        slots_to_fill = tail_slots[:shift_count]

        for j in slots_to_free:
            actions[j] = "idle"
        for j in slots_to_fill:
            actions[j] = "charge"
        shifted += shift_count
        _LOGGER.info(
            "Pass 5: shifted %d charge slots later "
            "(from t=%d to t=%d-%d, price %.1f ct)",
            shift_count, cb_start,
            min(slots_to_fill), max(slots_to_fill),
            cb_price * 100,
        )

    smoothed += shifted

    # Pass 6/6 (LAST): Target-based backward charge fill.
    # Only fill if charging is profitable: charge_price must be low enough
    # that the subsequent discharge actually earns money after efficiency
    # losses and cycle costs.
    filled = 0
    half_cycle_eur = cycle_cost_eur / 2
    charge_pct_per_slot = charge_kwh_slot / cap * 100 if cap > 0 else 0

    if charge_kwh_slot > 0 and charge_pct_per_slot > 0:
        sim_soc = current_soc
        # Track discharge block starts and ends for search range limiting
        discharge_block_info: list[tuple[int, int, float]] = []  # (start, end, soc_at_start)
        current_block_start: int | None = None
        for i in range(n):
            if actions[i] == "discharge":
                if current_block_start is None:
                    current_block_start = i
                    discharge_block_info.append((i, i, sim_soc))
            else:
                if current_block_start is not None:
                    # Update the end index of the last block
                    discharge_block_info[-1] = (
                        discharge_block_info[-1][0], i, discharge_block_info[-1][2]
                    )
                    current_block_start = None
            if actions[i] == "charge":
                delta = min(charge_kwh_slot, (max_soc - sim_soc) / 100 * cap)
                sim_soc = min(max_soc, sim_soc + delta / cap * 100)
            elif actions[i] == "discharge":
                delta = min(discharge_kwh_slot, (sim_soc - min_soc) / 100 * cap)
                sim_soc = max(min_soc, sim_soc - delta / cap * 100)
        if current_block_start is not None:
            discharge_block_info[-1] = (
                discharge_block_info[-1][0], n, discharge_block_info[-1][2]
            )

        for blk_idx, (block_start, block_end, soc_at_start) in enumerate(discharge_block_info):
            soc_gap = max_soc - soc_at_start
            if soc_gap <= 1.0:
                continue

            # Use tracked block_end (first non-discharge slot after block)
            block_prices = [hourly_data[i]["price"] for i in range(block_start, block_end)]
            avg_discharge_price = sum(block_prices) / len(block_prices) if block_prices else 0

            # Max acceptable charge price: discharge must be profitable
            max_charge_price = avg_discharge_price * efficiency - cycle_cost_eur

            slots_needed = int(soc_gap / charge_pct_per_slot) + 1

            # Only search for fill slots AFTER the previous discharge block
            # (filling before an earlier block is useless - that energy gets
            # discharged there and never reaches this block)
            if blk_idx > 0:
                search_start = discharge_block_info[blk_idx - 1][1]
            else:
                search_start = 0

            candidates: list[tuple[float, int, int]] = []
            for i in range(search_start, block_start):
                if actions[i] in ("idle", "hold"):
                    price = hourly_data[i]["price"]
                    if price <= max_charge_price:
                        candidates.append((price, -i, i))
            candidates.sort()

            block_filled = 0
            for _, _, idx in candidates[:slots_needed]:
                actions[idx] = "charge"
                block_filled += 1
            filled += block_filled

            if block_filled:
                _LOGGER.info(
                    "Pass 6 fill block@t=%d: SOC was %.1f%%, gap %.1f%% "
                    "-> added %d charge slots (needed %d, "
                    "avg_discharge=%.1fct, max_charge=%.1fct)",
                    block_start, soc_at_start, soc_gap,
                    block_filled, slots_needed,
                    avg_discharge_price * 100, max_charge_price * 100,
                )
            elif slots_needed > 0:
                _LOGGER.info(
                    "Pass 6 skip block@t=%d: no profitable charge slots "
                    "(avg_discharge=%.1fct, max_charge=%.1fct, "
                    "cheapest_idle=%.1fct)",
                    block_start,
                    avg_discharge_price * 100, max_charge_price * 100,
                    min((hourly_data[i]["price"] for i in range(block_start)
                         if actions[i] in ("idle", "hold")), default=0) * 100,
                )

    smoothed += filled

    if smoothed:
        _LOGGER.info(
            "Plan smoothing: %d slots adjusted (%d swaps, %d filled)",
            smoothed, swapped, filled,
        )

    return actions, smoothed
