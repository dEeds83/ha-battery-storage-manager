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
    solar_max_soc: float | None = None,
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
        solar_max_soc: Physical SOC ceiling for opportunistic solar
            absorption (grid charging stays capped at ``max_soc``). Solar
            surplus may fill the battery above ``max_soc``, so the state
            space extends up to this value. ``None`` = same as ``max_soc``.

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

    smax = max_soc if solar_max_soc is None else max(max_soc, solar_max_soc)
    soc_levels: list[float] = []
    s = float(min_soc)
    while s <= smax + 0.01:
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
        # Pro-Slot Discharge-Kapazitaet, falls vom Coordinator gesetzt
        # (zero-feed-begrenzt auf Hausverbrauch minus Solar). Fallback
        # auf globalen Wert.
        slot_dis_kwh = h.get("discharge_kwh", discharge_kwh_slot)

        # Solar-Surplus pro Slot: wird in idle/hold + discharge opportunistisch
        # in den Akku absorbiert (Dimmer / Switch-Charger). Hebt SOC.
        slot_solar_kwh = max(0.0, h.get("solar_surplus_kwh", 0) or 0.0)

        for si in range(num_soc):
            soc = soc_levels[si]
            best_val = INF
            best_act = "idle"

            # Idle-Transition: Solar-Surplus geht in Akku (capped an max_soc).
            solar_to_batt = min(slot_solar_kwh, max(0.0, (smax - soc) / 100 * cap))
            new_soc_idle = soc + solar_to_batt / cap * 100
            new_si_idle = soc_to_idx(new_soc_idle)
            val = dp[t + 1][new_si_idle]
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

            # Discharge: valid if net energy actually leaves the battery.
            # v2.50.0: Frueher wurde `new_si < si` verlangt — das verwarf
            # profitable Discharges, wenn paralleler Solar-Surplus den
            # SOC wieder hochzog (Plan zeigte fälschlich hold). Neuer
            # Check: Netto-Abgabe > 0, also delta abzueglich gleichzeitig
            # absorbiertem Solar muss echte Energie liefern. So bleiben
            # degenerierte Idle/Discharge-Ties ausgeschlossen, ohne
            # legitime Arbitrage zu blockieren.
            if soc > min_soc and slot_dis_kwh > 0:
                delta = min(slot_dis_kwh, (soc - min_soc) / 100 * cap)
                delivered = delta * efficiency
                soc_after_dis = soc - delta / cap * 100
                solar_to_batt_d = min(
                    slot_solar_kwh, max(0.0, (smax - soc_after_dis) / 100 * cap),
                )
                net_export_kwh = delta - solar_to_batt_d
                # v2.51.0: Slot-eigener Revenue muss positiv ueber einer
                # Mindestmarge liegen. Ohne diesen Threshold konnte DP
                # einen Slot mit revenue ≈ -0.1 ct waehlen, wenn der
                # Folge-Zustand dp[t+1] minimal besser war — verschenkter
                # Wechselrichter-Zyklus ohne wirtschaftlichen Nutzen.
                revenue = delivered * price - delta * half_cycle_eur
                if net_export_kwh > 0.001 and revenue > 0.0005:  # >= 0.05 ct
                    new_soc = soc_after_dis + solar_to_batt_d / cap * 100
                    new_si = soc_to_idx(new_soc)
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
        h = hourly_data[t]
        slot_dis_kwh = h.get("discharge_kwh", discharge_kwh_slot)
        slot_solar_kwh = max(0.0, h.get("solar_surplus_kwh", 0) or 0.0)

        if act == "charge":
            delta = min(charge_kwh_slot, (max_soc - soc) / 100 * cap)
            new_soc = soc + delta / cap * 100
        elif act == "discharge":
            delta = min(slot_dis_kwh, (soc - min_soc) / 100 * cap)
            soc_after = soc - delta / cap * 100
            solar_in = min(slot_solar_kwh, max(0.0, (smax - soc_after) / 100 * cap))
            new_soc = soc_after + solar_in / cap * 100
        else:
            solar_in = min(slot_solar_kwh, max(0.0, (smax - soc) / 100 * cap))
            new_soc = soc + solar_in / cap * 100
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
                delta = min(hourly_data[i].get("discharge_kwh", discharge_kwh_slot), (sim_soc - min_soc) / 100 * cap)
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
            delta = min(hourly_data[i].get("discharge_kwh", discharge_kwh_slot) if i < len(hourly_data) else discharge_kwh_slot, (soc_sim - min_soc) / 100 * cap)
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
                            delta = min(hourly_data[i].get("discharge_kwh", discharge_kwh_slot) if i < len(hourly_data) else discharge_kwh_slot, (soc_sim - min_soc) / 100 * cap)
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
                d = min(hourly_data[i].get("discharge_kwh", discharge_kwh_slot) if i < len(hourly_data) else discharge_kwh_slot, (s - min_soc) / 100 * cap)
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

    # SOC-Feasibility-Cleanup vor Final Pass 5:
    # DP/Pass 6 markieren manchmal mehr Charge-Slots als nötig — _create_
    # battery_plan würde sie später per max_soc-Cap zu idle machen, aber zur
    # Pass-5-Zeit zählen sie noch als charge und verfälschen die "teuerster
    # Charge"-Auswahl. Lösung: simuliere SOC, jeder charge-Slot wo SOC bereits
    # max_soc erreicht hat → idle. Frees ineffective charges als available
    # für sinnvollen Swap.
    if charge_kwh_slot > 0:
        sim_soc = current_soc
        cleaned = 0
        for i in range(n):
            if actions[i] == "charge":
                if sim_soc >= max_soc - 0.5:
                    actions[i] = "idle"
                    cleaned += 1
                else:
                    sim_soc = min(max_soc, sim_soc + charge_kwh_slot / cap * 100)
            elif actions[i] == "discharge":
                _d = hourly_data[i].get("discharge_kwh", discharge_kwh_slot)
                sim_soc = max(min_soc, sim_soc - _d / cap * 100)
        if cleaned:
            smoothed += cleaned
            _LOGGER.info(
                "Pre-Pass-5 SOC-cleanup: %d ineffective charge slots -> idle",
                cleaned,
            )

    # Final Pass 5: garantierter iterativer Swap-Pass.
    # Wiederholt: tausche teuersten Charge-Slot eines Blocks mit billigstem
    # idle/hold-Slot zwischen Block-Ende und nächstem Discharge, solange
    # mind. 0.2 ct/kWh Verbesserung. Loop bis kein Swap mehr möglich.
    # Gegen "stranded expensive charge" wenn DP/Pass 6 quantisiert.
    final_shifted = 0
    diag_blocks_logged = False
    for guard in range(200):
        # Charge-Blöcke pro Iteration neu bestimmen.
        blocks: list[tuple[int, int]] = []
        block_s = None
        for i in range(n):
            if actions[i] == "charge":
                if block_s is None:
                    block_s = i
            else:
                if block_s is not None:
                    blocks.append((block_s, i - block_s))
                    block_s = None
        if block_s is not None:
            blocks.append((block_s, n - block_s))
        if not blocks:
            break

        if not diag_blocks_logged:
            _LOGGER.info(
                "Final Pass 5 DIAG: %d charge blocks: %s",
                len(blocks),
                [(s, l, hourly_data[s]["price"] * 100) for s, l in blocks],
            )
            diag_blocks_logged = True

        best_swap: tuple[float, int, int, float, float] | None = None  # (gain, c_idx, a_idx, c_price, a_price)
        for cb_start, cb_len in blocks:
            cb_end = cb_start + cb_len
            # Fenster: ab Block-Ende bis zum nächsten Discharge.
            available: list[tuple[float, int]] = []
            for j in range(cb_end, n):
                if actions[j] == "discharge":
                    break
                if actions[j] in ("idle", "hold"):
                    available.append((hourly_data[j]["price"], j))
            if not available:
                continue
            available.sort()
            a_price, a_idx = available[0]

            charges_in_block = [
                (hourly_data[i]["price"], i)
                for i in range(cb_start, cb_end)
                if actions[i] == "charge"
            ]
            if not charges_in_block:
                continue
            charges_in_block.sort(reverse=True)
            c_price, c_idx = charges_in_block[0]

            gain = c_price - a_price
            if guard == 0:
                _LOGGER.info(
                    "Final Pass 5 DIAG block@t=%d: most_exp_charge=t=%d (%.2fct), "
                    "cheapest_avail=t=%d (%.2fct), gain=%.2fct, available_count=%d",
                    cb_start, c_idx, c_price * 100,
                    a_idx, a_price * 100, gain * 100, len(available),
                )
            if gain > 0.002:
                if best_swap is None or gain > best_swap[0]:
                    best_swap = (gain, c_idx, a_idx, c_price, a_price)

        if best_swap is None:
            if guard == 0:
                _LOGGER.info("Final Pass 5 DIAG: no profitable swap found")
            break
        gain, c_idx, a_idx, c_price, a_price = best_swap
        actions[c_idx] = "idle"
        actions[a_idx] = "charge"
        final_shifted += 1
        _LOGGER.info(
            "Final Pass 5: t=%d (%.1fct) -> idle, t=%d (%.1fct) -> charge "
            "(gain %.2fct/kWh)",
            c_idx, c_price * 100, a_idx, a_price * 100, gain * 100,
        )

    if final_shifted:
        smoothed += final_shifted
        _LOGGER.info("Final Pass 5: %d charge slots shifted total", final_shifted)

    if smoothed:
        _LOGGER.info(
            "Plan smoothing: %d total adjustments",
            smoothed,
        )

    return actions, smoothed


def _simulate_soc(
    actions: list[str],
    hourly_data: list[dict],
    current_soc: float,
    charge_kwh_slot: float,
    discharge_kwh_slot: float,
    cap: float,
    min_soc: float,
    max_soc: float,
    solar_max_soc: float | None = None,
) -> list[float]:
    """Simulate per-slot SOC after each action (incl. opportunistic solar).

    Mirrors the SOC update logic used in coordinator.py during plan
    building so that pre-solar discharge decisions match what will
    actually happen at runtime.
    """
    smax = max_soc if solar_max_soc is None else max(max_soc, solar_max_soc)
    proj: list[float] = []
    soc = current_soc
    for i, h in enumerate(hourly_data):
        act = actions[i]
        if act == "charge":
            delta = min(charge_kwh_slot, max(0.0, (max_soc - soc) / 100 * cap))
            soc += delta / cap * 100
        elif act == "discharge":
            slot_dis = h.get("discharge_kwh", discharge_kwh_slot)
            delta = min(slot_dis, max(0.0, (soc - min_soc) / 100 * cap))
            soc -= delta / cap * 100
        if act != "charge":
            surplus = max(0.0, h.get("solar_surplus_kwh", 0) or 0.0)
            solar_in = min(surplus, max(0.0, (smax - soc) / 100 * cap))
            soc += solar_in / cap * 100
        soc = max(min_soc, min(smax, soc))
        proj.append(soc)
    return proj


def force_pre_solar_discharge(
    actions: list[str],
    hourly_data: list[dict],
    current_soc: float,
    charge_kwh_slot: float,
    discharge_kwh_slot: float,
    cap: float,
    min_soc: float,
    max_soc: float,
    solar_max_soc: float | None = None,
) -> tuple[int, float, set[int]]:
    """Convert idle/hold slots before solar overflow into discharge.

    The DP solver and smoothing pipeline frequently leave the morning
    hours as ``idle``/``hold`` because the price spread doesn't justify
    a planned discharge. When a sunny day is forecast, however, the
    battery can fill up and clip incoming solar surplus — energy that
    is lost as uncompensated export.

    This pass simulates the projected SOC under the current plan, finds
    the cumulative solar surplus that wouldn't fit into the battery,
    and promotes the most expensive idle/hold slots *before* the first
    overflow to discharge until the excess is absorbed. Only slots with
    enough SOC headroom (above ``min_soc + 1``) are eligible.

    Args:
        actions: Plan actions, modified in place.
        hourly_data: Per-slot dicts with ``price``, ``solar_surplus_kwh``,
            and optionally ``discharge_kwh``.
        current_soc: Starting SOC in percent.
        charge_kwh_slot: Max grid charge energy per slot (kWh).
        discharge_kwh_slot: Max discharge energy per slot (kWh).
        cap: Usable battery capacity (kWh).
        min_soc: Minimum allowed SOC in percent.
        max_soc: Maximum allowed SOC in percent (real limit, not
            DP-headroom-reduced).
        solar_max_soc: Physical ceiling for solar absorption (overflow
            only happens there). ``None`` = same as ``max_soc``.

    Returns:
        Tuple ``(forced_count, excess_addressed_kwh, forced_indices)``.
        ``forced_indices`` lists the slot indices that were promoted, so
        callers can mark them in their plan-reason set (otherwise they
        would later look like degenerate single-slot discharges).
    """
    n = len(hourly_data)
    if n == 0:
        return 0, 0.0, set()
    smax = max_soc if solar_max_soc is None else max(max_soc, solar_max_soc)

    # Simulate baseline SOC trajectory.
    proj = _simulate_soc(
        actions, hourly_data, current_soc,
        charge_kwh_slot, discharge_kwh_slot, cap, min_soc, max_soc,
        solar_max_soc,
    )

    # Find the cumulative solar surplus that exceeds available headroom.
    # When SOC reaches max, additional surplus is clipped.
    excess_kwh = 0.0
    overflow_start_idx: int | None = None
    soc_walk = current_soc
    for i, h in enumerate(hourly_data):
        act = actions[i]
        if act == "charge":
            delta = min(charge_kwh_slot, max(0.0, (max_soc - soc_walk) / 100 * cap))
            soc_walk += delta / cap * 100
        elif act == "discharge":
            slot_dis = h.get("discharge_kwh", discharge_kwh_slot)
            delta = min(slot_dis, max(0.0, (soc_walk - min_soc) / 100 * cap))
            soc_walk -= delta / cap * 100
        if act != "charge":
            surplus = max(0.0, h.get("solar_surplus_kwh", 0) or 0.0)
            free_kwh = max(0.0, (smax - soc_walk) / 100 * cap)
            absorbed = min(surplus, free_kwh)
            clipped = surplus - absorbed
            if clipped > 0.001:
                if overflow_start_idx is None:
                    overflow_start_idx = i
                excess_kwh += clipped
            soc_walk += absorbed / cap * 100
        soc_walk = max(min_soc, min(smax, soc_walk))

    if overflow_start_idx is None or excess_kwh < 0.05:
        return 0, 0.0, set()

    # Promote the most expensive idle/hold slots before overflow to
    # discharge. Each iteration: pick best candidate, re-simulate to
    # check whether overflow is now resolved.
    forced = 0
    forced_indices: set[int] = set()
    excess_addressed = 0.0
    guard = 0
    # Terminate on the *re-simulated* overflow (the `new_overflow is None`
    # break below) and on candidate exhaustion — not on the one-shot
    # ``excess_kwh`` estimate, which credits each discharge delta 1:1 even
    # when an intermediate charge re-saturates SOC and the discharge does
    # not actually relieve clipping. The stale estimate could stop the loop
    # while real overflow (and free candidates) remained. ``guard < n`` is a
    # hard backstop against runaway iteration.
    while guard < n:
        guard += 1
        # Re-simulate to get current SOC trajectory.
        proj = _simulate_soc(
            actions, hourly_data, current_soc,
            charge_kwh_slot, discharge_kwh_slot, cap, min_soc, max_soc,
            solar_max_soc,
        )

        # Build candidate list: idle/hold before the (re-evaluated) first
        # overflow, with enough SOC to actually discharge.
        # Re-evaluate overflow position because earlier conversions may
        # have shifted it.
        new_overflow = None
        soc_walk = current_soc
        for i, h in enumerate(hourly_data):
            act = actions[i]
            if act == "charge":
                delta = min(charge_kwh_slot, max(0.0, (max_soc - soc_walk) / 100 * cap))
                soc_walk += delta / cap * 100
            elif act == "discharge":
                slot_dis = h.get("discharge_kwh", discharge_kwh_slot)
                delta = min(slot_dis, max(0.0, (soc_walk - min_soc) / 100 * cap))
                soc_walk -= delta / cap * 100
            if act != "charge":
                surplus = max(0.0, h.get("solar_surplus_kwh", 0) or 0.0)
                free_kwh = max(0.0, (smax - soc_walk) / 100 * cap)
                if surplus > free_kwh + 0.001 and new_overflow is None:
                    new_overflow = i
                soc_walk += min(surplus, free_kwh) / cap * 100
            soc_walk = max(min_soc, min(smax, soc_walk))

        if new_overflow is None:
            break

        candidates: list[tuple[float, int]] = []
        for i in range(new_overflow):
            if actions[i] not in ("idle", "hold"):
                continue
            # Slots mit eigenem Solar-Surplus sind keine Discharge-
            # Kandidaten: zero-export verbietet Entladen, waehrend PV
            # einspeist. Stattdessen absorbiert der Slot Solar in den
            # Akku — was wir wollen.
            if (hourly_data[i].get("solar_surplus_kwh") or 0.0) > 0.05:
                continue
            soc_before = proj[i - 1] if i > 0 else current_soc
            if soc_before <= min_soc + 1.0:
                continue
            slot_dis = hourly_data[i].get("discharge_kwh", discharge_kwh_slot)
            if slot_dis <= 0.02:
                continue
            candidates.append((hourly_data[i]["price"], i))

        if not candidates:
            break

        # Most expensive idle slot first — promoting it captures the
        # most value per kWh discharged.
        candidates.sort(reverse=True)
        _, idx = candidates[0]
        slot_dis = hourly_data[idx].get("discharge_kwh", discharge_kwh_slot)
        soc_before = proj[idx - 1] if idx > 0 else current_soc
        delta = min(slot_dis, max(0.0, (soc_before - min_soc) / 100 * cap))
        if delta <= 0.02:
            break

        actions[idx] = "discharge"
        forced_indices.add(idx)
        forced += 1
        excess_addressed += delta

    return forced, excess_addressed, forced_indices


def remove_dp_discharge_enclaves(
    actions: list[str],
    hourly_data: list[dict],
    protect_indices: set[int],
) -> int:
    """Demote isolated discharge slots that aren't part of a real block.

    Runs after ``force_pre_solar_discharge``. A single discharge slot
    surrounded by non-discharge actions, with no same-action slot within
    two positions, is treated as a DP discretisation artefact and
    converted back to ``idle``.

    Slots in ``protect_indices`` are never touched — those were
    deliberately promoted by ``force_pre_solar_discharge`` and may
    legitimately stand alone.

    Args:
        actions: Plan actions, modified in place.
        hourly_data: Per-slot dicts (unused beyond length; kept for symmetry).
        protect_indices: Slot indices that must not be demoted.

    Returns:
        Number of slots demoted.
    """
    n = len(actions)
    if n < 3:
        return 0
    demoted = 0
    for i in range(1, n - 1):
        if actions[i] != "discharge":
            continue
        if i in protect_indices:
            continue
        prev_same = actions[i - 1] == "discharge"
        next_same = actions[i + 1] == "discharge"
        if prev_same or next_same:
            continue
        nearby = (
            (i >= 2 and actions[i - 2] == "discharge")
            or (i + 2 < n and actions[i + 2] == "discharge")
        )
        if nearby:
            continue
        actions[i] = "idle"
        demoted += 1
    return demoted


def remove_unprofitable_predischarge(
    actions: list[str],
    hourly_data: list[dict],
    efficiency: float,
    cycle_cost_eur: float,
    protect_indices: set[int],
) -> int:
    """Demote discharge slots whose energy is re-bought later at a loss.

    The DP can discharge battery energy in a moderately-priced slot when the
    evening peak cannot absorb all available energy (per-slot ``discharge_kwh``
    is throttled to house-load by the zero-feed cap). ``smooth_plan`` Pass 6
    then refills the battery from cheaper slots before the evening block. The
    net effect is a *sell-high/buy-low* intraday round-trip that loses money
    once efficiency and the full cycle cost are accounted for — exactly the
    "netto -X ct" discharge slots users observe on low-solar days.

    This pass runs last (after ``force_pre_solar_discharge`` and
    ``remove_dp_discharge_enclaves``). For every discharge slot ``i`` that is
    followed by at least one charge slot, it computes the round-trip margin of
    selling at ``i`` and rebuying at the *cheapest* later charge slot::

        margin = price[i] * efficiency - min(later_charge_prices) - cycle_cost_eur

    If that margin is not strictly positive the discharge is a wasted cycle and
    the slot is demoted to ``idle``. Slots in ``protect_indices`` (deliberately
    promoted by ``force_pre_solar_discharge`` to avoid solar export) are kept,
    and terminal discharges with no later charge are always kept — they sell
    real energy and are never re-bought.

    Args:
        actions: Plan actions, modified in place.
        hourly_data: Per-slot dicts; only ``"price"`` is read.
        efficiency: Round-trip discharge efficiency (0-1).
        cycle_cost_eur: Full-cycle degradation cost (EUR/kWh throughput).
        protect_indices: Slot indices that must not be demoted.

    Returns:
        Number of slots demoted.
    """
    n = len(actions)
    if n < 2:
        return 0
    # Cheapest charge price strictly after each index, via a suffix scan.
    INF = float("inf")
    cheapest_charge_after = [INF] * n
    running = INF
    for i in range(n - 1, -1, -1):
        cheapest_charge_after[i] = running
        if actions[i] == "charge":
            price = hourly_data[i]["price"]
            if price < running:
                running = price

    margin_floor = 0.0005  # require a real positive round-trip (>= 0.05 ct/kWh)
    demoted = 0
    for i in range(n):
        if actions[i] != "discharge" or i in protect_indices:
            continue
        cheapest = cheapest_charge_after[i]
        if cheapest == INF:
            continue  # no later charge — energy is sold, not re-bought
        margin = hourly_data[i]["price"] * efficiency - cheapest - cycle_cost_eur
        if margin < margin_floor:
            actions[i] = "idle"
            demoted += 1
    return demoted


def compute_presolar_discharge_hours(
    actions: list[str],
    hourly_data: list[dict],
    forced_indices: set[int],
    *,
    solar_slot_threshold: float = 0.05,
    min_cumulative_surplus: float = 0.1,
) -> set[int]:
    """Return slot indices whose discharge reason is "make room for solar".

    Used only for the human-readable plan reason. A discharge slot before
    the first meaningful solar-surplus slot is labelled as room-making — but
    *only* when the cumulative later solar surplus is large enough to plausibly
    need that room. Previously every pre-solar discharge was labelled, so on a
    near-overcast day (~0.07 kWh total surplus) ordinary arbitrage discharges
    were mislabelled "Platz für Solar schaffen". Requiring a meaningful
    cumulative surplus keeps the label honest.

    Slots in ``forced_indices`` (deliberately promoted by
    ``force_pre_solar_discharge``) are always included — they exist precisely
    to avoid solar export, regardless of how the heuristic scores the day.

    Args:
        actions: Plan actions.
        hourly_data: Per-slot dicts with ``"solar_surplus_kwh"``.
        forced_indices: Indices promoted by ``force_pre_solar_discharge``.
        solar_slot_threshold: A slot counts as "solar" above this surplus (kWh).
        min_cumulative_surplus: Minimum total later surplus (kWh) before any
            pre-solar discharge is labelled room-making.

    Returns:
        Set of slot indices to mark as presolar discharges.
    """
    n = len(actions)
    first_solar_idx = next(
        (i for i, h in enumerate(hourly_data)
         if (h.get("solar_surplus_kwh", 0) or 0.0) > solar_slot_threshold),
        n,
    )
    result: set[int] = set()
    total_later_surplus = sum(
        max(0.0, h.get("solar_surplus_kwh", 0) or 0.0)
        for h in hourly_data[first_solar_idx:]
    )
    if total_later_surplus >= min_cumulative_surplus:
        for i in range(first_solar_idx):
            if actions[i] == "discharge":
                result.add(i)
    result.update(forced_indices)
    return result
