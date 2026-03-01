"""ClawStrike configuration: Pydantic v2 models and YAML loader."""

from __future__ import annotations

import sys
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ClawStrikeMode(StrEnum):
    SKILL = "skill"
    PROXY = "proxy"


class ClassifierModel(StrEnum):
    MULTILINGUAL = "multilingual"  # Llama Prompt Guard 2 86M
    ENGLISH_ONLY = "english-only"  # Llama Prompt Guard 2 22M


class RunMode(StrEnum):
    LOCAL = "local"
    API = "api"


class TrustLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNTRUSTED = "untrusted"


class LlmJudgeTrigger(StrEnum):
    HIGH_RISK_UNTRUSTED = "high_risk_untrusted"
    AMBIGUOUS_SCORE = "ambiguous_score"
    BOTH = "both"


class ContactOverrideLevel(StrEnum):
    """Valid trust override levels for contacts defined in config."""

    TRUSTED = "trusted"
    BLOCKED = "blocked"


# ---------------------------------------------------------------------------
# Config models — all use extra="allow" so that unknown fields are stored in
# model_extra, enabling downstream warning via _warn_extra_fields().
# ---------------------------------------------------------------------------


class ThresholdConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    block: float = 0.92
    flag: float = 0.70


class ClassifierConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: ClassifierModel = ClassifierModel.MULTILINGUAL
    run_mode: RunMode = RunMode.LOCAL
    threshold: ThresholdConfig = Field(default_factory=ThresholdConfig)


class McpConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True


class ProxyConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    listen_port: int = 8019
    upstream_llm_url: str = "https://api.anthropic.com/v1"


class ThresholdModifier(BaseModel):
    model_config = ConfigDict(extra="allow")

    block: float = 0.0
    flag: float = 0.0


class TrustConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    channel_defaults: dict[str, TrustLevel] = Field(
        default_factory=lambda: {
            "owner_dm": TrustLevel.HIGH,
            "trusted_group": TrustLevel.MEDIUM,
            "public_group": TrustLevel.LOW,
            "email_body": TrustLevel.LOW,
            "webhook": TrustLevel.UNTRUSTED,
            "skill_input": TrustLevel.UNTRUSTED,
        }
    )
    threshold_modifiers: dict[TrustLevel, ThresholdModifier] = Field(
        default_factory=lambda: {
            TrustLevel.HIGH: ThresholdModifier(block=0.05, flag=0.10),
            TrustLevel.MEDIUM: ThresholdModifier(block=0.0, flag=0.0),
            TrustLevel.LOW: ThresholdModifier(block=-0.05, flag=-0.10),
            TrustLevel.UNTRUSTED: ThresholdModifier(block=-0.10, flag=-0.20),
        }
    )
    auto_promote_after: int = 5
    contacts: dict[str, ContactOverrideLevel] = Field(default_factory=dict)


class StaticAllowlistRule(BaseModel):
    """A static allowlist rule defined in config (pre-approved action)."""

    model_config = ConfigDict(extra="allow")

    action_type: str
    source_scope: str = "global"


class ActionGatingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    confirmation_channel: str = "owner_dm"
    allowlist_learning: bool = True
    guard_allowlist_on_flag: bool = True
    static_rules: list[StaticAllowlistRule] = Field(default_factory=list)


class AuditConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    retention_days: int = 90
    log_raw_input: bool = True
    raw_input_max_chars: int = 200
    db_path: Path = Path("./data/audit.db")


class LlmJudgeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    # "model" here is the LLM identifier, not a Pydantic model class.
    model: str = "claude-sonnet-4-5-20250929"
    trigger: LlmJudgeTrigger = LlmJudgeTrigger.HIGH_RISK_UNTRUSTED


class ClawStrikeConfig(BaseModel):
    """Top-level ClawStrike configuration (the `clawstrike:` block)."""

    model_config = ConfigDict(extra="allow")

    mode: ClawStrikeMode = ClawStrikeMode.SKILL
    mcp: McpConfig = Field(default_factory=McpConfig)
    # proxy block is always parsed and validated, even in skill mode.
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    trust: TrustConfig = Field(default_factory=TrustConfig)
    action_gating: ActionGatingConfig = Field(default_factory=ActionGatingConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    # llm_judge block is always parsed and validated, even when disabled.
    llm_judge: LlmJudgeConfig = Field(default_factory=LlmJudgeConfig)


class _RootConfig(BaseModel):
    """Wrapper that expects `clawstrike:` as the top-level key.

    The ``clawstrike`` key is optional — an empty or absent YAML file produces
    a fully-defaulted :class:`ClawStrikeConfig`.
    """

    model_config = ConfigDict(extra="allow")

    clawstrike: ClawStrikeConfig = Field(default_factory=ClawStrikeConfig)


# ---------------------------------------------------------------------------
# Unknown-field warning helpers
# ---------------------------------------------------------------------------

# Map each model class to the nested model classes for its known fields, so
# _collect_extra_paths can recurse accurately.
_NESTED: dict[type[BaseModel], dict[str, type[BaseModel]]] = {
    _RootConfig: {"clawstrike": ClawStrikeConfig},
    ClawStrikeConfig: {
        "mcp": McpConfig,
        "proxy": ProxyConfig,
        "classifier": ClassifierConfig,
        "trust": TrustConfig,
        "action_gating": ActionGatingConfig,
        "audit": AuditConfig,
        "llm_judge": LlmJudgeConfig,
    },
    ClassifierConfig: {"threshold": ThresholdConfig},
}


def _collect_extra_paths(
    raw: Any,
    model_cls: type[BaseModel],
    path: str = "",
) -> list[str]:
    """Recursively collect dotted paths of unknown keys in *raw* vs *model_cls*."""
    if not isinstance(raw, dict):
        return []

    known = set(model_cls.model_fields.keys())
    nested = _NESTED.get(model_cls, {})
    extras: list[str] = []

    for key, value in raw.items():
        field_path = f"{path}.{key}" if path else key
        if key not in known:
            extras.append(field_path)
        elif key in nested and isinstance(value, dict):
            extras.extend(_collect_extra_paths(value, nested[key], field_path))

    return extras


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------


def load_config(path: str | Path = "clawstrike.yaml") -> ClawStrikeConfig:
    """Load and validate configuration from a YAML file.

    Args:
        path: Path to the YAML config file. Defaults to ``clawstrike.yaml``
              in the current working directory.

    Returns:
        Validated :class:`ClawStrikeConfig` instance with all defaults applied.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If validation fails (missing required fields, invalid enum
                    values, etc.).  The error message names the offending field
                    and, for enum fields, lists the valid options.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with path.open() as fh:
        raw = yaml.safe_load(fh)

    if raw is None:
        raw = {}

    # Warn about unknown top-level keys (outside the `clawstrike:` block).
    if isinstance(raw, dict):
        extra_paths = _collect_extra_paths(raw, _RootConfig)
        for ep in extra_paths:
            print(
                f"Warning: unknown configuration field '{ep}' ignored",
                file=sys.stderr,
            )

    try:
        root = _RootConfig.model_validate(raw)
    except ValidationError as exc:
        lines: list[str] = []
        for err in exc.errors():
            loc = ".".join(str(part) for part in err["loc"])
            msg = err["msg"]
            lines.append(f"  {loc}: {msg}")
        raise ValueError(
            "Configuration validation failed:\n" + "\n".join(lines)
        ) from exc

    return root.clawstrike
