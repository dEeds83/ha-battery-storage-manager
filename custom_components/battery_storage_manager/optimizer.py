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
    soc_step = max(0.3, min(1.0, min_delta_pct * 0.2))
    soc_step = round(soc_step, 2) or 0.5

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
    uncertainty_discount = 0.85
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
            # Cost uses FULL grid price.  Solar surplus is captured by
            # opportunistic charging in hold/idle modes regardless, so
            # "charge" should only be planned when grid-only charging is
            # profitable.  This prevents expensive-looking charge slots
            # (e.g., 30ct with 32% grid) that are actually just solar.
            if soc < max_soc and charge_kwh_slot > 0:
                delta = min(charge_kwh_slot, (max_soc - soc) / 100 * cap)
                new_soc = soc + delta / cap * 100
                new_si = soc_to_idx(new_soc)
                if new_si > si:
                    cost = delta * price + delta * half_cycle_eur
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
    # For each discharge, find the best idle AFTER it and swap if profitable.
    # Sort candidates by spread (highest first) so the best swaps happen first.
    swapped = 0
    max_rounds = 50
    for _ in range(max_rounds):
        candidates: list[tuple[float, int, int]] = []  # (spread, d_idx, idle_idx)
        for d_idx in range(n):
            if actions[d_idx] != "discharge":
                continue
            d_price = hourly_data[d_idx]["price"]
            # Find best idle/hold after this discharge
            best_idle = None
            best_idle_p = 0.0
            for j in range(d_idx + 1, n):
                if actions[j] in ("idle", "hold") and hourly_data[j]["price"] > best_idle_p:
                    best_idle_p = hourly_data[j]["price"]
                    best_idle = j
            if best_idle is not None and best_idle_p > d_price + 0.01:
                candidates.append((best_idle_p - d_price, d_idx, best_idle))

        if not candidates:
            break

        # Execute best swap (highest spread)
        candidates.sort(reverse=True)
        spread, d_idx, idle_idx = candidates[0]
        actions[d_idx] = "idle"
        actions[idle_idx] = "discharge"
        swapped += 1

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

    # Pass 5: For each charge slot, check if a cheaper idle/hold slot
    # exists AFTER the charge block (before next discharge). If so, swap
    # them — charge later at a lower price, leave room for solar earlier.
    shifted = 0
    for cb_start, cb_len in charge_blocks_final:
        cb_end = cb_start + cb_len

        # Collect available idle/hold slots after this charge block
        # (up to next discharge block)
        available: list[tuple[float, int]] = []  # (price, index)
        for j in range(cb_end, n):
            if actions[j] == "discharge":
                break
            if actions[j] in ("idle", "hold"):
                available.append((hourly_data[j]["price"], j))

        if not available:
            continue

        # For each charge slot (most expensive first), try to swap
        # with a cheaper available slot
        charge_slots = [
            (hourly_data[i]["price"], i)
            for i in range(cb_start, cb_end)
            if actions[i] == "charge"
        ]
        charge_slots.sort(reverse=True)  # most expensive first
        available.sort()  # cheapest first

        avail_idx = 0
        for c_price, c_idx in charge_slots:
            if avail_idx >= len(available):
                break
            a_price, a_idx = available[avail_idx]
            if a_price < c_price - 0.002:  # at least 0.2ct cheaper
                actions[c_idx] = "idle"
                actions[a_idx] = "charge"
                shifted += 1
                avail_idx += 1
                _LOGGER.info(
                    "Pass 5: swapped charge t=%d (%.1fct) → t=%d (%.1fct, %.1fct cheaper)",
                    c_idx, c_price * 100, a_idx, a_price * 100,
                    (c_price - a_price) * 100,
                )
            else:
                break  # no more profitable swaps

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

            # Max acceptable charge price: the lesser of:
            # 1. Profitability threshold (discharge must cover charge + costs)
            # 2. Most expensive existing DP charge slot (don't add slots the
            #    DP deliberately skipped as too expensive)
            profit_threshold = avg_discharge_price * efficiency - cycle_cost_eur
            existing_charge_prices = [
                hourly_data[i]["price"] for i in range(n)
                if actions[i] == "charge"
            ]
            dp_max_price = max(existing_charge_prices) if existing_charge_prices else profit_threshold
            max_charge_price = min(profit_threshold, dp_max_price + 0.002)

            slots_needed = int(soc_gap / charge_pct_per_slot) + 1

            # Only search for fill slots AFTER the previous discharge block
            # (filling before an earlier block is useless - that energy gets
            # discharged there and never reaches this block).
            # Exception: look past tiny blocks (1-2 slots) since their
            # discharge barely affects SOC and the search shouldn't be
            # blocked by them.
            search_start = 0
            if blk_idx > 0:
                # Walk back past tiny preceding discharge blocks
                prev_idx = blk_idx - 1
                while prev_idx >= 0:
                    prev_start, prev_end, _ = discharge_block_info[prev_idx]
                    prev_len = prev_end - prev_start
                    if prev_len <= 2:
                        # Tiny block: look past it
                        prev_idx -= 1
                    else:
                        # Substantial block: stop here
                        search_start = prev_end
                        break
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

    # Post-pass: fill idle/hold gaps inside charge blocks.
    # If an idle slot sits between two charge slots (within 2 positions),
    # and its effective_charge_cost is <= the costliest neighbour charge slot,
    # convert it to charge.  Fixes DP quantization holes like
    # charge→idle→charge where the idle slot is actually cheaper.
    gap_filled = 0
    for i in range(1, n - 1):
        if actions[i] not in ("idle", "hold"):
            continue
        # Check for charge neighbours within 2 slots
        has_prev = any(actions[max(0, i - k)] == "charge" for k in (1, 2))
        has_next = any(actions[min(n - 1, i + k)] == "charge" for k in (1, 2))
        if not (has_prev and has_next):
            continue
        # Find the effective cost of this slot and the costliest neighbour
        slot_cost = hourly_data[i].get("effective_charge_cost",
                                        hourly_data[i]["price"])
        neighbour_costs = []
        for k in (1, 2):
            for j in (i - k, i + k):
                if 0 <= j < n and actions[j] == "charge":
                    neighbour_costs.append(
                        hourly_data[j].get("effective_charge_cost",
                                           hourly_data[j]["price"])
                    )
        if neighbour_costs and slot_cost <= max(neighbour_costs) + 0.005:
            actions[i] = "charge"
            gap_filled += 1

    if gap_filled:
        smoothed += gap_filled
        _LOGGER.info("Post-pass gap fill: %d idle gaps inside charge blocks filled", gap_filled)

    # SOC-aware discharge reorder: simulate SOC forward, then for each
    # discharge slot followed by a more expensive idle/hold, swap them
    # only if the SOC at the idle slot is still above min_soc.
    # This fixes DP quantization issues where the last discharge slot
    # drains the battery just before a more expensive slot.
    soc_sim = current_soc
    soc_track: list[float] = []
    for i in range(n):
        soc_track.append(soc_sim)
        if actions[i] == "charge":
            delta = min(charge_kwh_slot, (max_soc - soc_sim) / 100 * cap)
            soc_sim = min(max_soc, soc_sim + delta / cap * 100)
        elif actions[i] == "discharge":
            delta = min(discharge_kwh_slot, (soc_sim - min_soc) / 100 * cap)
            soc_sim = max(min_soc, soc_sim - delta / cap * 100)

    soc_swaps = 0
    changed = True
    while changed:
        changed = False
        for i in range(n - 1):
            if actions[i] != "discharge" or actions[i + 1] not in ("idle", "hold"):
                continue
            if hourly_data[i + 1]["price"] <= hourly_data[i]["price"] + 0.002:
                continue
            # Would the battery have energy at slot i+1 if we idled at i?
            # Re-simulate SOC from the start up to i
            soc_at_i = soc_track[i]
            if soc_at_i <= min_soc + 0.5:
                # Near min_soc: swapping means we idle at i (keep energy)
                # and discharge at i+1 (use it at higher price).
                # This is feasible if soc_at_i > min_soc.
                if soc_at_i > min_soc:
                    actions[i] = "idle"
                    actions[i + 1] = "discharge"
                    soc_swaps += 1
                    changed = True
                    # Re-simulate SOC
                    soc_sim = current_soc
                    soc_track.clear()
                    for j in range(n):
                        soc_track.append(soc_sim)
                        if actions[j] == "charge":
                            delta = min(charge_kwh_slot, (max_soc - soc_sim) / 100 * cap)
                            soc_sim = min(max_soc, soc_sim + delta / cap * 100)
                        elif actions[j] == "discharge":
                            delta = min(discharge_kwh_slot, (soc_sim - min_soc) / 100 * cap)
                            soc_sim = max(min_soc, soc_sim - delta / cap * 100)
                    break  # restart scan with updated SOC

    if soc_swaps:
        smoothed += soc_swaps
        _LOGGER.info("SOC-aware reorder: %d discharge slots shifted to higher-priced neighbours", soc_swaps)

    # SOC-constrained cleanup: convert discharge slots where SOC <= min_soc
    # to idle (battery empty, can't actually discharge), then re-run Pass 3
    # to move cheap evening discharges into the now-freed morning slots.
    def _sim_soc(acts: list[str]) -> list[float]:
        """Simulate SOC forward and return track."""
        s = current_soc
        track = []
        for i in range(n):
            track.append(s)
            if acts[i] == "charge":
                d = min(charge_kwh_slot, (max_soc - s) / 100 * cap)
                s = min(max_soc, s + d / cap * 100)
            elif acts[i] == "discharge":
                d = min(discharge_kwh_slot, (s - min_soc) / 100 * cap)
                s = max(min_soc, s - d / cap * 100)
        return track

    soc_track = _sim_soc(actions)
    infeasible = 0
    for i in range(n):
        if actions[i] == "discharge" and soc_track[i] <= min_soc + 0.01:
            actions[i] = "idle"
            infeasible += 1
    if infeasible:
        smoothed += infeasible
        _LOGGER.info("SOC cleanup: %d infeasible discharge slots -> idle", infeasible)

    # Final Pass 3 re-run: now that infeasible discharges are idle,
    # swap remaining cheap discharges with the newly available expensive idles.
    final_swaps = 0
    for _ in range(50):
        candidates = []
        for d_idx in range(n):
            if actions[d_idx] != "discharge":
                continue
            d_price = hourly_data[d_idx]["price"]
            best_j = None
            best_p = 0.0
            for j in range(d_idx + 1, n):
                if actions[j] in ("idle", "hold") and hourly_data[j]["price"] > best_p:
                    best_p = hourly_data[j]["price"]
                    best_j = j
            if best_j is not None and best_p > d_price + 0.01:
                candidates.append((best_p - d_price, d_idx, best_j))
        if not candidates:
            break
        candidates.sort(reverse=True)
        _, d_idx, idle_idx = candidates[0]
        actions[d_idx] = "idle"
        actions[idle_idx] = "discharge"
        final_swaps += 1

    # Verify SOC feasibility of final swaps
    if final_swaps:
        soc_track = _sim_soc(actions)
        reverted = 0
        for i in range(n):
            if actions[i] == "discharge" and soc_track[i] <= min_soc + 0.01:
                actions[i] = "idle"
                reverted += 1
        smoothed += final_swaps - reverted
        _LOGGER.info(
            "Final Pass 3: %d swaps (%d reverted for SOC feasibility)",
            final_swaps, reverted,
        )

    # Final Pass 5: re-run charge slot optimization after Pass 6 added
    # new charge slots that may be more expensive than available later slots.
    charge_blocks_final2: list[tuple[int, int]] = []
    block_s = None
    for i in range(n):
        if actions[i] == "charge":
            if block_s is None:
                block_s = i
        else:
            if block_s is not None:
                charge_blocks_final2.append((block_s, i - block_s))
                block_s = None
    if block_s is not None:
        charge_blocks_final2.append((block_s, n - block_s))

    final_shifted = 0
    for cb_start, cb_len in charge_blocks_final2:
        cb_end = cb_start + cb_len
        available: list[tuple[float, int]] = []
        for j in range(cb_end, n):
            if actions[j] == "discharge":
                break
            if actions[j] in ("idle", "hold"):
                available.append((hourly_data[j]["price"], j))
        if not available:
            continue

        charge_slots = [
            (hourly_data[i]["price"], i)
            for i in range(cb_start, cb_end)
            if actions[i] == "charge"
        ]
        charge_slots.sort(reverse=True)
        available.sort()

        avail_idx = 0
        for c_price, c_idx in charge_slots:
            if avail_idx >= len(available):
                break
            a_price, a_idx = available[avail_idx]
            if a_price < c_price - 0.002:
                actions[c_idx] = "idle"
                actions[a_idx] = "charge"
                final_shifted += 1
                avail_idx += 1
            else:
                break

    if final_shifted:
        smoothed += final_shifted
        _LOGGER.info("Final Pass 5: %d charge slots shifted to cheaper later slots", final_shifted)

    if smoothed:
        _LOGGER.info(
            "Plan smoothing: %d total adjustments",
            smoothed,
        )

    return actions, smoothed
