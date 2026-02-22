"""Tests for US-001: YAML Configuration Loading."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from clawstrike.config import (
    ClawStrikeConfig,
    ClawStrikeMode,
    LlmJudgeTrigger,
    RunMode,
    TransportMode,
    TrustLevel,
    load_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_yaml(tmp_path: Path, data: dict) -> Path:
    """Write *data* as YAML under *tmp_path* and return the file path."""
    cfg_file = tmp_path / "clawstrike.yaml"
    cfg_file.write_text(yaml.dump(data))
    return cfg_file


def minimal_config(extra: dict | None = None) -> dict:
    """Return the minimum valid config dict (only required fields)."""
    base: dict = {"clawstrike": {"classifier": {"model": "prompt-guard-2"}}}
    if extra:
        base["clawstrike"].update(extra)
    return base


# ---------------------------------------------------------------------------
# AC: reads from clawstrike.yaml in the working directory
# ---------------------------------------------------------------------------


def test_loads_from_explicit_path(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())
    config = load_config(cfg_file)
    assert isinstance(config, ClawStrikeConfig)


def test_raises_if_file_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(FileNotFoundError, match="does_not_exist.yaml"):
        load_config(missing)


# ---------------------------------------------------------------------------
# AC: missing required fields cause a startup error naming the field
# ---------------------------------------------------------------------------


def test_error_on_missing_classifier_model(tmp_path: Path) -> None:
    """classifier.model is required; ValueError must name the field."""
    data = {"clawstrike": {"classifier": {}}}
    cfg_file = write_yaml(tmp_path, data)

    with pytest.raises(ValueError) as exc_info:
        load_config(cfg_file)

    error_msg = str(exc_info.value)
    assert "classifier" in error_msg
    assert "model" in error_msg


def test_error_on_missing_classifier_section_entirely(tmp_path: Path) -> None:
    """Omitting the classifier block entirely must raise ValueError."""
    data = {"clawstrike": {}}
    cfg_file = write_yaml(tmp_path, data)

    with pytest.raises(ValueError) as exc_info:
        load_config(cfg_file)

    assert "classifier" in str(exc_info.value)


# ---------------------------------------------------------------------------
# AC: unknown fields are ignored with a warning logged to stderr
# ---------------------------------------------------------------------------


def test_unknown_top_level_key_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    data = minimal_config()
    data["totally_unknown_top_level"] = "value"
    cfg_file = write_yaml(tmp_path, data)

    config = load_config(cfg_file)  # must NOT raise

    captured = capsys.readouterr()
    assert "totally_unknown_top_level" in captured.err
    assert "Warning" in captured.err
    assert isinstance(config, ClawStrikeConfig)


def test_unknown_nested_key_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    data = minimal_config({"unknown_nested_key": "ignored"})
    cfg_file = write_yaml(tmp_path, data)

    config = load_config(cfg_file)

    captured = capsys.readouterr()
    assert "unknown_nested_key" in captured.err
    assert isinstance(config, ClawStrikeConfig)


def test_unknown_classifier_key_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    data: dict = {
        "clawstrike": {"classifier": {"model": "prompt-guard-2", "future_field": "xyz"}}
    }
    cfg_file = write_yaml(tmp_path, data)

    config = load_config(cfg_file)

    captured = capsys.readouterr()
    assert "future_field" in captured.err
    assert isinstance(config, ClawStrikeConfig)


# ---------------------------------------------------------------------------
# AC: default values are applied for all optional fields
# ---------------------------------------------------------------------------


def test_defaults_classifier(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())
    config = load_config(cfg_file)

    assert config.classifier.run_mode == RunMode.LOCAL
    assert config.classifier.threshold.block == pytest.approx(0.92)
    assert config.classifier.threshold.flag == pytest.approx(0.70)
    assert config.classifier.custom_model_path is None


def test_defaults_mode_and_mcp(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())
    config = load_config(cfg_file)

    assert config.mode == ClawStrikeMode.SKILL
    assert config.mcp.transport == TransportMode.STDIO


def test_defaults_trust(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())
    config = load_config(cfg_file)

    assert config.trust.channel_defaults["owner_dm"] == TrustLevel.HIGH
    assert config.trust.channel_defaults["email_body"] == TrustLevel.LOW
    assert config.trust.channel_defaults["webhook"] == TrustLevel.UNTRUSTED
    assert config.trust.auto_promote_after == 5


def test_defaults_trust_modifiers(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())
    config = load_config(cfg_file)

    mods = config.trust.threshold_modifiers
    assert mods[TrustLevel.HIGH].block == pytest.approx(0.05)
    assert mods[TrustLevel.HIGH].flag == pytest.approx(0.10)
    assert mods[TrustLevel.UNTRUSTED].block == pytest.approx(-0.10)
    assert mods[TrustLevel.UNTRUSTED].flag == pytest.approx(-0.20)


def test_defaults_action_gating(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())
    config = load_config(cfg_file)

    assert config.action_gating.enabled is True
    assert config.action_gating.confirmation_channel == "owner_dm"
    assert config.action_gating.allowlist_learning is True


def test_defaults_audit(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())
    config = load_config(cfg_file)

    assert config.audit.enabled is True
    assert config.audit.retention_days == 90
    assert config.audit.log_raw_input is True
    assert config.audit.raw_input_max_chars == 200
    assert config.audit.db_path == Path("./data/audit.db")


def test_defaults_proxy(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())
    config = load_config(cfg_file)

    assert config.proxy.listen_port == 8019
    assert config.proxy.upstream_llm_url == "https://api.anthropic.com/v1"


def test_defaults_llm_judge(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())
    config = load_config(cfg_file)

    assert config.llm_judge.enabled is False
    assert config.llm_judge.trigger == LlmJudgeTrigger.HIGH_RISK_UNTRUSTED


# ---------------------------------------------------------------------------
# AC: invalid enum values produce an error naming the field and valid options
# ---------------------------------------------------------------------------


def test_invalid_classifier_model_enum(tmp_path: Path) -> None:
    data = {"clawstrike": {"classifier": {"model": "invalid-model"}}}
    cfg_file = write_yaml(tmp_path, data)

    with pytest.raises(ValueError) as exc_info:
        load_config(cfg_file)

    error_msg = str(exc_info.value)
    assert "classifier" in error_msg
    assert "model" in error_msg
    # Pydantic v2 lists valid enum options in the message
    assert "prompt-guard-2" in error_msg or "deberta-v3" in error_msg


def test_invalid_mode_enum(tmp_path: Path) -> None:
    data = minimal_config({"mode": "bad-mode"})
    cfg_file = write_yaml(tmp_path, data)

    with pytest.raises(ValueError) as exc_info:
        load_config(cfg_file)

    error_msg = str(exc_info.value)
    assert "mode" in error_msg


def test_invalid_trust_level_in_channel_defaults(tmp_path: Path) -> None:
    data = minimal_config(
        {"trust": {"channel_defaults": {"owner_dm": "super_trusted"}}}
    )
    cfg_file = write_yaml(tmp_path, data)

    with pytest.raises(ValueError) as exc_info:
        load_config(cfg_file)

    assert (
        "trust" in str(exc_info.value).lower()
        or "channel" in str(exc_info.value).lower()
    )


# ---------------------------------------------------------------------------
# AC: Phase 1.5 fields (proxy block) are parsed and validated in skill mode
# ---------------------------------------------------------------------------


def test_proxy_block_validated_in_skill_mode(tmp_path: Path) -> None:
    """Proxy block must be parsed/validated even when mode is 'skill'."""
    data = minimal_config(
        {
            "mode": "skill",
            "proxy": {
                "listen_port": 9000,
                "upstream_llm_url": "https://api.openai.com/v1",
            },
        }
    )
    cfg_file = write_yaml(tmp_path, data)
    config = load_config(cfg_file)

    assert config.mode == ClawStrikeMode.SKILL
    assert config.proxy.listen_port == 9000
    assert config.proxy.upstream_llm_url == "https://api.openai.com/v1"


def test_proxy_block_invalid_port_type_caught_in_skill_mode(tmp_path: Path) -> None:
    """Invalid proxy config values are caught at startup even in skill mode."""
    data = minimal_config({"mode": "skill", "proxy": {"listen_port": "not-an-int"}})
    cfg_file = write_yaml(tmp_path, data)

    with pytest.raises(ValueError) as exc_info:
        load_config(cfg_file)

    assert "proxy" in str(exc_info.value) or "listen_port" in str(exc_info.value)


def test_llm_judge_block_parsed_when_disabled(tmp_path: Path) -> None:
    """llm_judge block is parsed and validated even when enabled: false."""
    data = minimal_config(
        {"llm_judge": {"enabled": False, "trigger": "ambiguous_score"}}
    )
    cfg_file = write_yaml(tmp_path, data)
    config = load_config(cfg_file)

    assert config.llm_judge.enabled is False
    assert config.llm_judge.trigger == LlmJudgeTrigger.AMBIGUOUS_SCORE


def test_llm_judge_invalid_trigger_caught_when_disabled(tmp_path: Path) -> None:
    data = minimal_config({"llm_judge": {"enabled": False, "trigger": "invalid"}})
    cfg_file = write_yaml(tmp_path, data)

    with pytest.raises(ValueError):
        load_config(cfg_file)


# ---------------------------------------------------------------------------
# AC: classifier model enum values round-trip correctly
# ---------------------------------------------------------------------------


def test_all_classifier_model_values_accepted(tmp_path: Path) -> None:
    for model_value in ("prompt-guard-2", "deberta-v3", "custom"):
        data = {"clawstrike": {"classifier": {"model": model_value}}}
        cfg_file = write_yaml(tmp_path, data)
        config = load_config(cfg_file)
        assert config.classifier.model.value == model_value


# ---------------------------------------------------------------------------
# AC: overriding defaults works correctly
# ---------------------------------------------------------------------------


def test_custom_thresholds_override_defaults(tmp_path: Path) -> None:
    data: dict = {
        "clawstrike": {
            "classifier": {
                "model": "deberta-v3",
                "threshold": {"block": 0.85, "flag": 0.60},
            }
        }
    }
    cfg_file = write_yaml(tmp_path, data)
    config = load_config(cfg_file)

    assert config.classifier.threshold.block == pytest.approx(0.85)
    assert config.classifier.threshold.flag == pytest.approx(0.60)


def test_empty_yaml_raises_on_missing_classifier(tmp_path: Path) -> None:
    cfg_file = tmp_path / "clawstrike.yaml"
    cfg_file.write_text("")  # empty file

    with pytest.raises(ValueError):
        load_config(cfg_file)


def test_partial_config_with_audit_override(tmp_path: Path) -> None:
    data = minimal_config(
        {
            "audit": {
                "retention_days": 30,
                "log_raw_input": False,
                "db_path": "/tmp/audit.db",
            }
        }
    )
    cfg_file = write_yaml(tmp_path, data)
    config = load_config(cfg_file)

    assert config.audit.retention_days == 30
    assert config.audit.log_raw_input is False
    assert config.audit.db_path == Path("/tmp/audit.db")
    # defaults still applied for other audit fields
    assert config.audit.raw_input_max_chars == 200
