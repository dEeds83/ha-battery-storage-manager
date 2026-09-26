"""Pure, dependency-free helpers for Battery Storage Manager.

Kept import-light (no Home Assistant, no relative imports) so the logic can be
unit-tested in isolation.
"""

from __future__ import annotations


def combine_grid_power(
    net_w: float | None,
    consumption_w: float | None,
    production_w: float | None,
) -> float | None:
    """Net grid power (positive = import) from the available readings.

    The netted sensor wins when it has a value; otherwise fall back to
    consumption - production (either side alone is accepted).
    """
    if net_w is not None:
        return net_w
    if consumption_w is not None and production_w is not None:
        return consumption_w - production_w
    if consumption_w is not None:
        return consumption_w
    if production_w is not None:
        return -production_w
    return None


def should_self_correct_target(
    target_w: float,
    actual_w: float | None,
    actual_age_s: float | None,
    *,
    deadband_w: float = 150.0,
    max_age_s: float = 120.0,
) -> bool:
    """Decide whether the inverter target should be clamped to the measured actual.

    Self-correct (v2.50.2) clamps the internal target down when the inverter
    has clearly not adopted the setpoint (actual sits well below target). v2.52.0
    adds a staleness guard: a frozen or stale actual reading must not be trusted,
    otherwise an old/low value could spuriously clamp the target and throttle a
    needed discharge.

    Args:
        target_w: Current internal inverter target (W).
        actual_w: Measured inverter output (W), or ``None`` if unavailable.
        actual_age_s: Seconds since the actual sensor last changed, or ``None``
            if unknown (then the reading is trusted, preserving pre-watchdog
            behaviour).
        deadband_w: Minimum target-minus-actual gap (W) before correcting.
        max_age_s: Maximum age (s) for the reading to be considered fresh.

    Returns:
        True if the target should be clamped down to ``actual_w`` (+reserve).
    """
    if actual_w is None:
        return False
    if actual_age_s is not None and actual_age_s > max_age_s:
        return False
    return (target_w - actual_w) > deadband_w
