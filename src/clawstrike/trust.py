"""ClawStrike trust engine — channel trust resolution and threshold modulation."""

from __future__ import annotations

from clawstrike.config import ThresholdModifier, TrustConfig, TrustLevel


def resolve_trust_level(channel_type: str, trust_cfg: TrustConfig) -> TrustLevel:
    """Return the trust level for *channel_type*.

    Looks up *channel_type* in ``trust_cfg.channel_defaults``.  If the channel
    type is not present in the config, ``TrustLevel.UNTRUSTED`` is returned
    (fail-closed / most-restrictive default).
    """
    return trust_cfg.channel_defaults.get(channel_type, TrustLevel.UNTRUSTED)


def compute_effective_thresholds(
    base_block: float,
    base_flag: float,
    trust_level: TrustLevel,
    modifiers: dict[TrustLevel, ThresholdModifier],
) -> tuple[float, float]:
    """Apply the trust-level modifier to base thresholds and clamp to [0.0, 1.0].

    Args:
        base_block: Configured block threshold (e.g. 0.92).
        base_flag: Configured flag threshold (e.g. 0.70).
        trust_level: Resolved trust level for the incoming message.
        modifiers: Mapping of trust level → additive modifier from config.

    Returns:
        ``(effective_block, effective_flag)`` after applying the modifier and
        clamping both values to the valid [0.0, 1.0] range.
    """
    mod = modifiers.get(trust_level, ThresholdModifier())
    eff_block = max(0.0, min(1.0, base_block + mod.block))
    eff_flag = max(0.0, min(1.0, base_flag + mod.flag))
    return eff_block, eff_flag
