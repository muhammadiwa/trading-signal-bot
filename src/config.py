"""Configuration loader — reads settings.yaml and .env."""

from dataclasses import dataclass, field
from pathlib import Path
import os
import re
import yaml


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass
class Settings:
    """Typed settings loaded from config/settings.yaml."""

    # Pipeline
    timeframe: str = "1d"
    lookback_months: int = 6
    freshness_max_hours: int = 4
    parallel_workers: int = 10
    runtime_budget_minutes: int = 60
    cron_time_utc: str = "23:00"

    # Signals
    min_confidence: float = 0.70
    max_signals_per_day: int = 30
    cooldown_hours: int = 24
    cooldown_override_confidence: float = 0.80

    # Strategies
    strategies: dict = field(default_factory=dict)

    # Research
    research: dict = field(default_factory=dict)

    # Backtest
    min_win_rate: float = 0.40
    min_sharpe: float = 0.5
    walk_forward_enabled: bool = False

    # Risk
    atr_sl_multiplier: float = 1.5
    atr_tp_multiplier: float = 3.0
    max_size_pct: float = 0.10
    min_size_pct: float = 0.01
    global_max_drawdown: float = 0.25

    # LLM
    llm_provider: str = "tokenrouter"
    llm_model: str = "deepseek/deepseek-v4-pro"
    llm_timeout_seconds: int = 3
    llm_max_tokens: int = 150

    # Telegram
    telegram_retry_attempts: int = 3

    # Top coins
    top_coins_source: str = "coingecko"
    top_coins_limit: int = 100
    top_coins_exclude_stablecoins: bool = True

    def __post_init__(self):
        """Validate cross-field constraints and types."""
        errors: list[str] = []

        # Numeric range checks
        if not isinstance(self.min_confidence, (int, float)) or not 0 < self.min_confidence <= 1:
            errors.append(f"min_confidence must be in (0, 1], got {self.min_confidence}")
        if not isinstance(self.parallel_workers, int) or self.parallel_workers < 1:
            errors.append(f"parallel_workers must be int >= 1, got {self.parallel_workers}")
        if not isinstance(self.lookback_months, int) or self.lookback_months < 1:
            errors.append(f"lookback_months must be int >= 1, got {self.lookback_months}")

        # Risk constraint checks
        if not isinstance(self.min_size_pct, (int, float)) or self.min_size_pct < 0:
            errors.append(f"min_size_pct must be >= 0, got {self.min_size_pct}")
        if not isinstance(self.max_size_pct, (int, float)) or self.max_size_pct <= 0:
            errors.append(f"max_size_pct must be > 0, got {self.max_size_pct}")
        if self.min_size_pct > self.max_size_pct:
            errors.append(
                f"min_size_pct ({self.min_size_pct}) cannot exceed max_size_pct ({self.max_size_pct})"
            )
        if not isinstance(self.global_max_drawdown, (int, float)) or self.global_max_drawdown <= 0:
            errors.append(f"global_max_drawdown must be > 0, got {self.global_max_drawdown}")

        # LLM timeout
        if not isinstance(self.llm_timeout_seconds, int) or self.llm_timeout_seconds < 1:
            errors.append(f"llm_timeout_seconds must be int >= 1, got {self.llm_timeout_seconds}")

        # Pipeline constraints
        if not isinstance(self.freshness_max_hours, (int, float)) or self.freshness_max_hours < 1:
            errors.append(f"freshness_max_hours must be >= 1, got {self.freshness_max_hours}")
        if not isinstance(self.runtime_budget_minutes, (int, float)) or self.runtime_budget_minutes < 5:
            errors.append(f"runtime_budget_minutes must be >= 5, got {self.runtime_budget_minutes}")
        if not isinstance(self.max_signals_per_day, int) or self.max_signals_per_day < 1:
            errors.append(f"max_signals_per_day must be int >= 1, got {self.max_signals_per_day}")
        if not isinstance(self.cooldown_hours, (int, float)) or self.cooldown_hours < 0:
            errors.append(f"cooldown_hours must be >= 0, got {self.cooldown_hours}")
        if not isinstance(self.cooldown_override_confidence, (int, float)) or not 0 <= self.cooldown_override_confidence <= 1:
            errors.append(f"cooldown_override_confidence must be in [0, 1], got {self.cooldown_override_confidence}")
        if not isinstance(self.atr_sl_multiplier, (int, float)) or self.atr_sl_multiplier <= 0:
            errors.append(f"atr_sl_multiplier must be > 0, got {self.atr_sl_multiplier}")
        if not isinstance(self.atr_tp_multiplier, (int, float)) or self.atr_tp_multiplier <= 0:
            errors.append(f"atr_tp_multiplier must be > 0, got {self.atr_tp_multiplier}")
        if not isinstance(self.top_coins_limit, int) or self.top_coins_limit < 1:
            errors.append(f"top_coins_limit must be int >= 1, got {self.top_coins_limit}")

        # cron_time_utc format validation
        try:
            parts = self.cron_time_utc.split(":")
            if len(parts) != 2:
                raise ValueError("expected HH:MM")
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError("hour 0-23, minute 0-59")
        except (ValueError, AttributeError) as e:
            errors.append(f"cron_time_utc must be 'HH:MM' (00:00–23:59), got '{self.cron_time_utc}': {e}")

        if errors:
            raise ConfigError("\n".join(errors))


def _load_dotenv(env_path: Path) -> dict[str, str]:
    """Parse a .env file into a dict without mutating os.environ.

    Handles: quoted values, inline comments, export keyword, empty lines.
    """
    result: dict[str, str] = {}
    if not env_path.exists():
        return result

    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Strip 'export ' prefix
            if line.startswith("export "):
                line = line[7:]
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Remove inline comments (handles: VALUE # comment)
            # Only strip a `#` preceded by whitespace — preserves # in URLs/tokens
            value = re.sub(r"\s+#[^#]*$", "", value)
            # Unquote single/double quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            result[key] = value
    return result


def load_config(config_dir: Path | None = None) -> Settings:
    """Load settings.yaml and resolve environment variables.

    Args:
        config_dir: Directory containing settings.yaml. Defaults to
                    PROJECT_ROOT/config/.

    Returns:
        Settings dataclass with all configuration values.

    Raises:
        ConfigError: If settings.yaml is missing, unparseable, or invalid.
    """
    if config_dir is None:
        config_dir = Path(__file__).resolve().parent.parent / "config"

    yaml_path = config_dir / "settings.yaml"
    if not yaml_path.exists():
        raise ConfigError(f"settings.yaml not found at {yaml_path}")

    try:
        with open(yaml_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {yaml_path}: {e}") from e

    # Handle empty file (safe_load returns None)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"settings.yaml must contain a YAML mapping, got {type(raw).__name__}"
        )

    pipeline = raw.get("pipeline", {})
    signals = raw.get("signals", {})
    backtest = raw.get("backtest", {})
    risk = raw.get("risk", {})
    llm = raw.get("llm", {})
    telegram = raw.get("telegram", {})
    top_coins = raw.get("top_coins", {})

    # Load .env into a dict, then selectively inject into os.environ
    env_path = config_dir.parent / ".env"
    env_vars = _load_dotenv(env_path)
    for k, v in env_vars.items():
        # Only inject app-specific env vars, not system vars
        if k in ("PATH", "HOME", "USER", "SHELL", "PYTHONPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"):
            continue
        os.environ.setdefault(k, v)

    return Settings(
        timeframe=pipeline.get("timeframe", "1d"),
        lookback_months=pipeline.get("lookback_months", 6),
        freshness_max_hours=pipeline.get("freshness_max_hours", 4),
        parallel_workers=pipeline.get("parallel_workers", 10),
        runtime_budget_minutes=pipeline.get("runtime_budget_minutes", 60),
        cron_time_utc=pipeline.get("cron_time_utc", "23:00"),
        min_confidence=signals.get("min_confidence", 0.60),
        max_signals_per_day=signals.get("max_signals_per_day", 30),
        cooldown_hours=signals.get("cooldown_hours", 24),
        cooldown_override_confidence=signals.get("cooldown_override_confidence", 0.80),
        strategies=raw.get("strategies", {}),
        research=raw.get("research", {}),
        min_win_rate=backtest.get("min_win_rate", 0.40),
        min_sharpe=backtest.get("min_sharpe", 0.5),
        walk_forward_enabled=backtest.get("walk_forward_enabled", False),
        atr_sl_multiplier=risk.get("atr_sl_multiplier", 1.5),
        atr_tp_multiplier=risk.get("atr_tp_multiplier", 3.0),
        max_size_pct=risk.get("max_size_pct", 0.10),
        min_size_pct=risk.get("min_size_pct", 0.01),
        global_max_drawdown=risk.get("global_max_drawdown", 0.25),
        llm_provider=llm.get("provider", "tokenrouter"),
        llm_model=llm.get("model", "deepseek/deepseek-v4-pro"),
        llm_timeout_seconds=llm.get("timeout_seconds", 3),
        llm_max_tokens=llm.get("max_tokens", 150),
        telegram_retry_attempts=telegram.get("retry_attempts", 3),
        top_coins_source=top_coins.get("source", "coingecko"),
        top_coins_limit=top_coins.get("limit", 100),
        top_coins_exclude_stablecoins=top_coins.get("exclude_stablecoins", True),
    )
