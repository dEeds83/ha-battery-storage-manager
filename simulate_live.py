#!/usr/bin/env python3
"""
End-to-end simulation of the smoothing pipeline using LIVE data from MCP.

Usage: python3 simulate_live.py

Fetches the current plan from Home Assistant via MCP (ha-mcp),
then replays all 6 smoothing passes and validates the result.

This script should be run BEFORE every release to catch pipeline bugs.
"""

import json
import os
import subprocess
import sys


# ─── Fetch live data from HA via ha-mcp ─────────────────────
def fetch_plan_from_ha():
    """Fetch current plan from HA. Returns plan list + coordinator state."""
    import os

    # Read token from .mcp.json
    mcp_path = os.path.join(os.path.dirname(__file__), ".mcp.json")
    with open(mcp_path) as f:
        mcp = json.load(f)
    args = mcp["mcpServers"]["homeassistant"]["args"]
    token = None
    url = None
    for i, a in enumerate(args):
        if a == "--ha-token" and i + 1 < len(args):
            token = args[i + 1]
        if a == "--ha-url" and i + 1 < len(args):
            url = args[i + 1]

    if not token or not url:
        print("ERROR: Could not read token/url from .mcp.json")
        sys.exit(1)

    # Use HA REST API directly
    import urllib.request

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    def get_state(entity_id):
        req = urllib.request.Request(
            f"{url}/api/states/{entity_id}", headers=headers
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    plan_state = get_state("sensor.battery_storage_manager_speicherplan")
    mode_state = get_state("sensor.battery_storage_manager_betriebsmodus")

    plan = plan_state["attributes"]["plan"]
    soc = mode_state["attributes"]["battery_soc"]
    action_counts = plan_state["attributes"].get("action_counts", {})
    ha_version = mode_state["attributes"].get("version")
    ha_source_hash = mode_state["attributes"].get("source_hash")

    return plan, soc, action_counts, ha_version, ha_source_hash


# ─── Smoothing Pipeline (mirrors coordinator.py) ────────────
def run_pipeline(plan, current_soc, max_soc=90.0, min_soc=12.0,
                 charge_kwh=0.220, discharge_kwh=0.175, cap=7.5,
                 efficiency=0.90, cycle_cost=0.04):
    """Run all 6 smoothing passes on the plan actions."""

    n = len(plan)
    actions = [e["action"] for e in plan]
    prices = [e["price"] for e in plan]
    times = [e["hour"] for e in plan]
    charge_pct = charge_kwh / cap * 100
    half_cycle = cycle_cost / 2

    log = []

    def find_charge_blocks():
        blocks = []
        bs = None
        for i in range(n):
            if actions[i] == "charge":
                if bs is None:
                    bs = i
            else:
                if bs is not None:
                    blocks.append((bs, i - bs))
                    bs = None
        if bs is not None:
            blocks.append((bs, n - bs))
        return blocks

    def sim_soc():
        """Simulate SOC trajectory."""
        soc = current_soc
        traj = [soc]
        for i in range(n):
            if actions[i] == "charge":
                delta = min(charge_kwh, (max_soc - soc) / 100 * cap)
                soc = min(max_soc, soc + delta / cap * 100)
            elif actions[i] == "discharge":
                delta = min(discharge_kwh, (soc - min_soc) / 100 * cap)
                soc = max(min_soc, soc - delta / cap * 100)
            traj.append(soc)
        return traj

    # ── Pass 1-3: Already applied by DP (we work on the plan as-is) ──
    log.append("Pass 1-3: Using DP output (already smoothed)")

    # ── Pass 5: Merge same-price charge blocks ──
    blocks = find_charge_blocks()
    if len(blocks) > 1:
        main = max(blocks, key=lambda b: b[1])
        ms, ml = main
        main_price = prices[ms]
        merged = 0
        for start, length in blocks:
            if (start, length) == main:
                continue
            bp = prices[start]
            if start < ms and abs(bp - main_price) < 0.005:
                gap_slots = [
                    j for j in range(start + length, ms)
                    if actions[j] in ("idle", "hold") and abs(prices[j] - main_price) < 0.02
                ]
                if len(gap_slots) >= length:
                    for j in range(start, start + length):
                        actions[j] = "idle"
                        merged += 1
                    gap_slots.sort(reverse=True)
                    for j in gap_slots[:length]:
                        actions[j] = "charge"
            elif start >= ms + ml:
                gap = start - (ms + ml)
                if length < 4 and gap > 2:
                    for j in range(start, start + length):
                        actions[j] = "idle"
                        merged += 1
            else:
                gap = min(abs(start - (ms + ml)), abs(ms - (start + length)))
                if length < 4 and gap > 2:
                    for j in range(start, start + length):
                        actions[j] = "idle"
                        merged += 1
        log.append(f"Pass 5: merged/removed {merged} slots")
    else:
        log.append("Pass 5: single block, no merge needed")

    # ── Pass 6: Shift to latest position ──
    blocks = find_charge_blocks()
    shifted = 0
    for cb_s, cb_l in blocks:
        cb_e = cb_s + cb_l
        cb_p = prices[cb_s]
        tail = []
        for j in range(cb_e, n):
            if actions[j] == "discharge":
                break
            if actions[j] in ("idle", "hold"):
                p = prices[j]
                if abs(p - cb_p) < 0.003:
                    tail.append(j)
                elif p > cb_p + 0.003:
                    break
        if tail:
            sc = min(len(tail), cb_l)
            for j in range(cb_s, cb_s + sc):
                actions[j] = "idle"
            tail.sort(reverse=True)
            for j in tail[:sc]:
                actions[j] = "charge"
            shifted += sc
    log.append(f"Pass 6: shifted {shifted} slots to latest position")

    # ── Pass 4 (LAST): Fill to max_soc ──
    filled = 0
    if charge_pct > 0:
        soc = current_soc
        dis_starts = []
        for i in range(n):
            if actions[i] == "discharge" and (i == 0 or actions[i - 1] != "discharge"):
                dis_starts.append((i, soc))
            if actions[i] == "charge":
                delta = min(charge_kwh, (max_soc - soc) / 100 * cap)
                soc = min(max_soc, soc + delta / cap * 100)
            elif actions[i] == "discharge":
                delta = min(discharge_kwh, (soc - min_soc) / 100 * cap)
                soc = max(min_soc, soc - delta / cap * 100)

        for bs, soc_at in dis_starts:
            gap = max_soc - soc_at
            if gap <= 1.0:
                continue
            need = int(gap / charge_pct) + 1
            cands = [
                (prices[i], -i, i) for i in range(bs)
                if actions[i] in ("idle", "hold")
            ]
            cands.sort()
            bf = 0
            for _, _, idx in cands[:need]:
                actions[idx] = "charge"
                bf += 1
            filled += bf
            log.append(
                f"Pass 4: block@{times[bs]}: SOC was {soc_at:.1f}%, "
                f"gap {gap:.1f}% → +{bf} slots (needed {need})"
            )

    if not filled:
        log.append("Pass 4: no fill needed")

    # ── Results ──
    traj = sim_soc()
    blocks = find_charge_blocks()

    return actions, traj, blocks, log


# ─── Validation ─────────────────────────────────────────────
def validate(plan, actions, traj, blocks, current_soc, max_soc=90.0):
    """Run validation checks and return pass/fail."""
    times = [e["hour"] for e in plan]
    n = len(actions)
    issues = []

    # Check 1: SOC at each discharge start
    for i in range(n):
        if actions[i] == "discharge" and (i == 0 or actions[i - 1] != "discharge"):
            soc = traj[i]
            if soc < max_soc - 2:
                issues.append(
                    f"⚠️  Discharge at {times[i]} starts at {soc:.1f}% "
                    f"(< {max_soc}%)"
                )
            else:
                pass  # OK

    # Check 2: No night charging (00:00-06:00) when morning slots available
    night_charge = []
    morning_hold = []
    for i in range(n):
        h = times[i][11:13] if len(times[i]) > 12 else ""
        if h.isdigit():
            hour = int(h)
            if 0 <= hour < 6 and actions[i] == "charge":
                night_charge.append(times[i])
            if 8 <= hour < 14 and actions[i] in ("hold", "idle"):
                morning_hold.append(times[i])

    if night_charge and morning_hold:
        issues.append(
            f"⚠️  Night charging ({len(night_charge)} slots) "
            f"while {len(morning_hold)} morning hold slots available"
        )

    # Check 3: Charge block count (should ideally be 1)
    if len(blocks) > 1:
        issues.append(
            f"⚠️  {len(blocks)} separate charge blocks "
            f"(sizes: {[l for _, l in blocks]})"
        )

    # Check 4: Max SOC reached
    max_reached = max(traj)
    if max_reached < max_soc - 2:
        issues.append(
            f"⚠️  Max SOC only {max_reached:.1f}% (target {max_soc}%)"
        )

    # Check 5: Total charge/discharge balance
    total_charge = sum(1 for a in actions if a == "charge")
    total_discharge = sum(1 for a in actions if a == "discharge")

    return issues, total_charge, total_discharge, max_reached


# ─── Main ───────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  Battery Storage Manager – Live Pipeline Simulation")
    print("=" * 60)

    # Load expected version from manifest.json
    manifest_path = os.path.join(os.path.dirname(__file__),
        "custom_components", "battery_storage_manager", "manifest.json")
    with open(manifest_path) as f:
        expected_version = json.load(f).get("version", "?")

    # Calculate local source hash
    import hashlib
    component_dir = os.path.join(os.path.dirname(__file__),
        "custom_components", "battery_storage_manager")
    hasher = hashlib.md5()
    for py_file in sorted(os.listdir(component_dir)):
        if py_file.endswith(".py"):
            with open(os.path.join(component_dir, py_file), "rb") as fh:
                hasher.update(fh.read())
    local_hash = hasher.hexdigest()[:12]

    print(f"\n  Expected version: {expected_version}")
    print(f"  Local source hash: {local_hash}")
    print("\nFetching live data from Home Assistant...")
    try:
        plan, soc, counts, ha_version, ha_hash = fetch_plan_from_ha()
    except Exception as e:
        print(f"ERROR: Could not fetch data: {e}")
        print("Make sure HA is running and .mcp.json is configured.")
        sys.exit(1)

    print(f"  HA version: {ha_version or 'unknown'}")
    print(f"  HA source hash: {ha_hash or 'unknown'}")
    if ha_version and ha_version != expected_version:
        print(f"  ⚠️  VERSION MISMATCH: HA has {ha_version}, "
              f"expected {expected_version}")
    elif ha_version:
        print(f"  ✅ Version match: {ha_version}")
    if ha_hash and ha_hash != local_hash:
        print(f"  ⚠️  SOURCE MISMATCH: HA={ha_hash}, local={local_hash}")
        print(f"     The code running in HA differs from local files!")
    elif ha_hash:
        print(f"  ✅ Source hash match: {ha_hash}")

    print(f"  SOC: {soc:.1f}%")
    print(f"  Plan: {len(plan)} slots")
    print(f"  Actions: {counts}")

    # Extract battery params from plan attributes if possible
    print("\nRunning smoothing pipeline...")
    actions, traj, blocks, log = run_pipeline(plan, soc)

    print("\n  Pipeline log:")
    for entry in log:
        print(f"    {entry}")

    print(f"\n  Charge blocks ({len(blocks)}):")
    for s, l in blocks:
        soc_s = traj[s]
        soc_e = traj[s + l]
        print(
            f"    {plan[s]['hour'][:16]} - {plan[s+l-1]['hour'][:16]} "
            f"({l} slots) SOC {soc_s:.0f}% → {soc_e:.0f}% "
            f"@ {plan[s]['price']*100:.1f}ct"
        )

    print("\nValidation:")
    issues, tc, td, max_soc = validate(plan, actions, traj, blocks, soc)

    print(f"  Total: {tc} charge, {td} discharge slots")
    print(f"  Max SOC: {max_soc:.1f}%")

    if issues:
        for issue in issues:
            print(f"  {issue}")
        print(f"\n  ❌ FAILED ({len(issues)} issues)")
        return 1
    else:
        print("  ✅ ALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
