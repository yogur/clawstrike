"""Unit tests for the trust engine."""

from __future__ import annotations

import pytest

from clawstrike.config import ThresholdModifier, TrustConfig, TrustLevel
from clawstrike.trust import compute_effective_thresholds, resolve_trust_level

# ---------------------------------------------------------------------------
# Channel Trust Level Resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "channel_type, expected",
    [
        ("owner_dm", TrustLevel.HIGH),
        ("trusted_group", TrustLevel.MEDIUM),
        ("public_group", TrustLevel.LOW),
        ("email_body", TrustLevel.LOW),
        ("webhook", TrustLevel.UNTRUSTED),
        ("skill_input", TrustLevel.UNTRUSTED),
    ],
)
def test_known_channels_resolve(channel_type: str, expected: TrustLevel) -> None:
    trust_cfg = TrustConfig()
    assert resolve_trust_level(channel_type, trust_cfg) == expected


def test_unknown_channel_defaults_to_untrusted() -> None:
    trust_cfg = TrustConfig()
    assert resolve_trust_level("telegram", trust_cfg) == TrustLevel.UNTRUSTED


def test_custom_channel_in_config_resolves_correctly() -> None:
    trust_cfg = TrustConfig(channel_defaults={"slack_dm": TrustLevel.HIGH})
    assert resolve_trust_level("slack_dm", trust_cfg) == TrustLevel.HIGH


# ---------------------------------------------------------------------------
# Trust-Modulated Classifier Thresholds
# ---------------------------------------------------------------------------


def _default_modifiers() -> dict[TrustLevel, ThresholdModifier]:
    return TrustConfig().threshold_modifiers


def test_high_trust_increases_thresholds() -> None:
    eff_block, eff_flag = compute_effective_thresholds(
        0.92, 0.70, TrustLevel.HIGH, _default_modifiers()
    )
    assert pytest.approx(eff_block, abs=1e-9) == 0.97
    assert pytest.approx(eff_flag, abs=1e-9) == 0.80


def test_medium_trust_no_change() -> None:
    eff_block, eff_flag = compute_effective_thresholds(
        0.92, 0.70, TrustLevel.MEDIUM, _default_modifiers()
    )
    assert pytest.approx(eff_block, abs=1e-9) == 0.92
    assert pytest.approx(eff_flag, abs=1e-9) == 0.70


def test_low_trust_decreases_thresholds() -> None:
    eff_block, eff_flag = compute_effective_thresholds(
        0.92, 0.70, TrustLevel.LOW, _default_modifiers()
    )
    assert pytest.approx(eff_block, abs=1e-9) == 0.87
    assert pytest.approx(eff_flag, abs=1e-9) == 0.60


def test_untrusted_decreases_thresholds() -> None:
    eff_block, eff_flag = compute_effective_thresholds(
        0.92, 0.70, TrustLevel.UNTRUSTED, _default_modifiers()
    )
    assert pytest.approx(eff_block, abs=1e-9) == 0.82
    assert pytest.approx(eff_flag, abs=1e-9) == 0.50


@pytest.mark.parametrize(
    "base_block,base_flag,trust_level,modifiers,expected_block,expected_flag",
    [
        (
            0.05,
            0.08,
            TrustLevel.UNTRUSTED,
            {TrustLevel.UNTRUSTED: ThresholdModifier(block=-0.10, flag=-0.10)},
            0.0,
            0.0,
        ),
        (
            0.97,
            0.95,
            TrustLevel.HIGH,
            {TrustLevel.HIGH: ThresholdModifier(block=0.10, flag=0.10)},
            1.0,
            1.0,
        ),
    ],
)
def test_clamp(
    base_block: float,
    base_flag: float,
    trust_level: TrustLevel,
    modifiers: dict,
    expected_block: float,
    expected_flag: float,
) -> None:
    eff_block, eff_flag = compute_effective_thresholds(
        base_block, base_flag, trust_level, modifiers
    )
    assert eff_block == expected_block
    assert eff_flag == expected_flag


def test_missing_trust_level_in_modifiers_yields_no_change() -> None:
    # Empty modifiers dict — no entry for MEDIUM → defaults to zero ThresholdModifier
    eff_block, eff_flag = compute_effective_thresholds(
        0.92, 0.70, TrustLevel.MEDIUM, {}
    )
    assert pytest.approx(eff_block, abs=1e-9) == 0.92
    assert pytest.approx(eff_flag, abs=1e-9) == 0.70
