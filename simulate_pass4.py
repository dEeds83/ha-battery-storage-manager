#!/usr/bin/env python3
"""Simulate the full DP + Pass 1-5 pipeline with synthetic price scenarios."""

# ─── Battery parameters (from user's system) ───
CAP = 7.5           # kWh
CHARGE_KWH = 0.220  # per 15-min slot
DISCHARGE_KWH = 0.175
EFFICIENCY = 0.90
CYCLE_COST = 0.04   # EUR/kWh full cycle
HALF_CYCLE = CYCLE_COST / 2
MIN_SOC = 12.0
MAX_SOC = 90.0
SOC_STEP = 1.0
TV_PER_KWH = 0.18   # terminal value

CHARGE_PCT = CHARGE_KWH / CAP * 100  # ~2.93%
DISCHARGE_PCT = DISCHARGE_KWH / CAP * 100
NUM_SOC = int((MAX_SOC - MIN_SOC) / SOC_STEP) + 1


def soc_to_idx(soc):
    idx = round((soc - MIN_SOC) / SOC_STEP)
    return max(0, min(NUM_SOC - 1, idx))


def idx_to_soc(si):
    return MIN_SOC + si * SOC_STEP


def solve_dp(slots, start_soc):
    n = len(slots)
    INF = float("-inf")
    dp = [[0.0] * NUM_SOC for _ in range(n + 1)]
    action_dp = [["idle"] * NUM_SOC for _ in range(n)]

    for si in range(NUM_SOC):
        soc = idx_to_soc(si)
        dp[n][si] = TV_PER_KWH * (soc - MIN_SOC) / 100 * CAP

    for t in range(n - 1, -1, -1):
        price = slots[t]["price"]
        gf = slots[t].get("gf", 1.0)
        for si in range(NUM_SOC):
            soc = idx_to_soc(si)
            best_val = INF
            best_act = "idle"

            val = dp[t + 1][si]
            if val > best_val:
                best_val = val
                best_act = "idle"

            if soc < MAX_SOC and CHARGE_KWH > 0:
                delta = min(CHARGE_KWH, (MAX_SOC - soc) / 100 * CAP)
                new_soc = soc + delta / CAP * 100
                new_si = soc_to_idx(new_soc)
                if new_si > si:
                    cost = delta * gf * price + delta * HALF_CYCLE
                    val = -cost + dp[t + 1][new_si]
                    if val >= best_val:
                        best_val = val
                        best_act = "charge"

            if soc > MIN_SOC and DISCHARGE_KWH > 0:
                delta = min(DISCHARGE_KWH, (soc - MIN_SOC) / 100 * CAP)
                delivered = delta * EFFICIENCY
                new_soc = soc - delta / CAP * 100
                new_si = soc_to_idx(new_soc)
                if new_si < si:
                    revenue = delivered * price - delta * HALF_CYCLE
                    val = revenue + dp[t + 1][new_si]
                    if val > best_val:
                        best_val = val
                        best_act = "discharge"

            dp[t][si] = best_val
            action_dp[t][si] = best_act

    actions = []
    current_si = soc_to_idx(start_soc)
    for t in range(n):
        act = action_dp[t][current_si]
        actions.append(act)
        soc = idx_to_soc(current_si)
        if act == "charge":
            delta = min(CHARGE_KWH, (MAX_SOC - soc) / 100 * CAP)
            new_soc = soc + delta / CAP * 100
        elif act == "discharge":
            delta = min(DISCHARGE_KWH, (soc - MIN_SOC) / 100 * CAP)
            new_soc = soc - delta / CAP * 100
        else:
            new_soc = soc
        current_si = soc_to_idx(new_soc)

    return actions, dp[0][soc_to_idx(start_soc)]


def pass4_fill(actions, slots, start_soc):
    n = len(actions)
    sim_soc = start_soc
    discharge_block_starts = []
    for i in range(n):
        if actions[i] == "discharge" and (i == 0 or actions[i - 1] != "discharge"):
            discharge_block_starts.append((i, sim_soc))
        if actions[i] == "charge":
            delta = min(CHARGE_KWH, (MAX_SOC - sim_soc) / 100 * CAP)
            sim_soc = min(MAX_SOC, sim_soc + delta / CAP * 100)
        elif actions[i] == "discharge":
            delta = min(DISCHARGE_KWH, (sim_soc - MIN_SOC) / 100 * CAP)
            sim_soc = max(MIN_SOC, sim_soc - delta / CAP * 100)

    filled = 0
    for block_start, soc_at_start in discharge_block_starts:
        soc_gap = MAX_SOC - soc_at_start
        if soc_gap <= 1.0:
            continue
        slots_needed = int(soc_gap / CHARGE_PCT) + 1
        candidates = []
        for i in range(block_start):
            if actions[i] == "idle":
                candidates.append((slots[i]["price"], -i, i))
        candidates.sort()
        for _, _, idx in candidates[:slots_needed]:
            actions[idx] = "charge"
            filled += 1
    return filled


def pass5_remove_islands(actions):
    n = len(actions)
    charge_blocks = []
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

    if len(charge_blocks) <= 1:
        return 0

    main_block = max(charge_blocks, key=lambda b: b[1])
    removed = 0
    for start, length in charge_blocks:
        if (start, length) == main_block:
            continue
        if length < 4:
            main_start, main_len = main_block
            main_end = main_start + main_len
            gap = min(abs(start - main_end), abs(main_start - (start + length)))
            if gap > 2:
                for j in range(start, start + length):
                    actions[j] = "idle"
                    removed += 1
    return removed


def simulate_soc(actions, start_soc):
    soc = start_soc
    trajectory = [soc]
    for act in actions:
        if act == "charge":
            delta = min(CHARGE_KWH, (MAX_SOC - soc) / 100 * CAP)
            soc = min(MAX_SOC, soc + delta / CAP * 100)
        elif act == "discharge":
            delta = min(DISCHARGE_KWH, (soc - MIN_SOC) / 100 * CAP)
            soc = max(MIN_SOC, soc - delta / CAP * 100)
        trajectory.append(soc)
    return trajectory


def slot_to_time(slot):
    h = slot // 4
    m = (slot % 4) * 15
    return f"{h:02d}:{m:02d}"


def print_plan(actions, slots, trajectory, name):
    print(f"\n{'='*70}")
    print(f"  {name}")
    print(f"{'='*70}")

    counts = {}
    for a in actions:
        counts[a] = counts.get(a, 0) + 1
    print(f"  Slots: {len(actions)} | " + " | ".join(f"{k}: {v}" for k, v in sorted(counts.items())))

    # Charge blocks
    blocks = []
    bs = None
    for i in range(len(actions)):
        if actions[i] == "charge":
            if bs is None: bs = i
        else:
            if bs is not None:
                blocks.append((bs, i - bs))
                bs = None
    if bs is not None:
        blocks.append((bs, len(actions) - bs))

    print(f"\n  Lade-Blöcke ({len(blocks)}):")
    for start, length in blocks:
        soc_s = trajectory[start]
        soc_e = trajectory[start + length]
        p = slots[start]["price"] * 100
        print(f"    {slot_to_time(start)}-{slot_to_time(start+length)} "
              f"({length} Slots, {length*15}min) "
              f"SOC {soc_s:.0f}% → {soc_e:.0f}% @ {p:.1f} ct")

    # Discharge blocks
    dis_blocks = []
    bs = None
    for i in range(len(actions)):
        if actions[i] == "discharge":
            if bs is None: bs = i
        else:
            if bs is not None:
                dis_blocks.append((bs, i - bs))
                bs = None
    if bs is not None:
        dis_blocks.append((bs, len(actions) - bs))

    print(f"\n  Entlade-Blöcke ({len(dis_blocks)}):")
    for start, length in dis_blocks:
        soc_s = trajectory[start]
        soc_e = trajectory[start + length]
        p_avg = sum(slots[start+j]["price"] for j in range(length)) / length * 100
        print(f"    {slot_to_time(start)}-{slot_to_time(start+length)} "
              f"({length} Slots) "
              f"SOC {soc_s:.0f}% → {soc_e:.0f}% @ Ø{p_avg:.1f} ct")
        if soc_s < 80:
            print(f"    ⚠️  Startet bei nur {soc_s:.0f}% SOC!")
        else:
            print(f"    ✅ Startet bei {soc_s:.0f}% SOC")

    # Max SOC reached
    max_soc = max(trajectory)
    print(f"\n  Max SOC erreicht: {max_soc:.0f}%", "✅" if max_soc >= 88 else "⚠️")


def run_scenario(name, slots, start_soc):
    actions, profit = solve_dp(slots, start_soc)
    dp_charges = sum(1 for a in actions if a == "charge")

    filled = pass4_fill(actions, slots, start_soc)
    removed = pass5_remove_islands(actions)

    final_charges = sum(1 for a in actions if a == "charge")
    print(f"\n  DP: {dp_charges} charge → Pass4: +{filled} → Pass5: -{removed} → Final: {final_charges} charge")

    trajectory = simulate_soc(actions, start_soc)
    print_plan(actions, slots, trajectory, name)


# ═══════════════════════════════════════════════════
# Szenarien
# ═══════════════════════════════════════════════════

def scenario_long_cheap():
    """Standard: ein langer günstiger Block."""
    slots = []
    slots += [{"price": 0.25}] * 32   # 00:00-08:00 moderat
    slots += [{"price": 0.17}] * 24   # 08:00-14:00 günstig
    slots += [{"price": 0.22}] * 8    # 14:00-16:00 moderat
    slots += [{"price": 0.33}] * 24   # 16:00-22:00 teuer
    slots += [{"price": 0.25}] * 8    # 22:00-00:00 moderat
    return slots

def scenario_three_islands():
    """DREI günstige Inseln über den Tag verteilt."""
    slots = []
    slots += [{"price": 0.25}] * 8    # 00:00-02:00 moderat
    slots += [{"price": 0.15}] * 8    # 02:00-04:00 GÜNSTIG 1
    slots += [{"price": 0.24}] * 16   # 04:00-08:00 moderat
    slots += [{"price": 0.16}] * 8    # 08:00-10:00 GÜNSTIG 2
    slots += [{"price": 0.23}] * 12   # 10:00-13:00 moderat
    slots += [{"price": 0.14}] * 6    # 13:00-14:30 GÜNSTIG 3
    slots += [{"price": 0.22}] * 6    # 14:30-16:00 moderat
    slots += [{"price": 0.35}] * 24   # 16:00-22:00 teuer
    slots += [{"price": 0.26}] * 8    # 22:00-00:00 moderat
    return slots

def scenario_two_peaks():
    """Zwei Preisspitzen mit günstigem Tal dazwischen."""
    slots = []
    slots += [{"price": 0.16}] * 24   # 00:00-06:00 günstig
    slots += [{"price": 0.32}] * 16   # 06:00-10:00 PEAK 1
    slots += [{"price": 0.15}] * 16   # 10:00-14:00 günstig
    slots += [{"price": 0.22}] * 16   # 14:00-18:00 moderat
    slots += [{"price": 0.36}] * 16   # 18:00-22:00 PEAK 2
    slots += [{"price": 0.24}] * 8    # 22:00-00:00 moderat
    return slots

def scenario_volatile():
    """Volatile Preise mit vielen kleinen Schwankungen."""
    import random
    random.seed(42)
    base = (
        [0.22]*8 + [0.14]*4 + [0.25]*8 + [0.13]*4 +
        [0.28]*8 + [0.35]*8 + [0.20]*4 + [0.15]*6 +
        [0.22]*6 + [0.30]*4 + [0.38]*12 + [0.32]*8 +
        [0.25]*8 + [0.20]*8
    )
    return [{"price": p + random.uniform(-0.02, 0.02)} for p in base]

def scenario_flat_cheap():
    """Flacher billiger Tag - fast kein Spread."""
    slots = []
    slots += [{"price": 0.18}] * 32   # 00:00-08:00
    slots += [{"price": 0.17}] * 24   # 08:00-14:00
    slots += [{"price": 0.19}] * 8    # 14:00-16:00
    slots += [{"price": 0.22}] * 24   # 16:00-22:00 leicht teurer
    slots += [{"price": 0.19}] * 8    # 22:00-00:00
    return slots


if __name__ == "__main__":
    START_SOC = 25.0

    print("="*70)
    print("  Battery Storage Manager – Szenario-Simulation")
    print("="*70)
    print(f"  Batterie: {CAP} kWh, Laden={CHARGE_KWH*4000:.0f}W, "
          f"Entladen={DISCHARGE_KWH*4000:.0f}W, η={EFFICIENCY*100:.0f}%")
    print(f"  SOC: {MIN_SOC:.0f}%-{MAX_SOC:.0f}%, "
          f"Zykluskosten={CYCLE_COST*100:.0f} ct/kWh, "
          f"Terminal={TV_PER_KWH*100:.0f} ct/kWh")
    print(f"  Laden/Slot: {CHARGE_KWH:.3f} kWh ({CHARGE_PCT:.1f}% SOC)")
    print(f"  Start-SOC: {START_SOC:.0f}%")

    for name, gen in [
        ("1: Langer günstiger Block", scenario_long_cheap),
        ("2: DREI günstige Inseln", scenario_three_islands),
        ("3: Zwei Peaks mit Tal", scenario_two_peaks),
        ("4: Volatile Preise", scenario_volatile),
        ("5: Flacher Tag (wenig Spread)", scenario_flat_cheap),
    ]:
        slots = gen()
        run_scenario(name, slots, START_SOC)

    print("\n" + "="*70)
    print("  Simulation abgeschlossen.")
    print("="*70)
