"""Configuration loader — reads settings.yaml and .env."""

from dataclasses import dataclass, field
from pathlib import Path
import os
import yaml


class ConfigError(Exception):
    """Raised when required configuration is missing."""


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
    min_confidence: float = 0.60
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


def load_config(config_dir: Path | None = None) -> Settings:
    """Load settings.yaml and resolve environment variables.

    Args:
        config_dir: Directory containing settings.yaml. Defaults to
                    PROJECT_ROOT/config/.

    Returns:
        Settings dataclass with all configuration values.

    Raises:
        ConfigError: If settings.yaml is missing or unparseable.
        FileNotFoundError: If .env is missing (logged as warning, not error).
    """
    if config_dir is None:
        config_dir = Path(__file__).resolve().parent.parent / "config"

    yaml_path = config_dir / "settings.yaml"
    if not yaml_path.exists():
        raise ConfigError(f"settings.yaml not found at {yaml_path}")

    with open(yaml_path) as f:
        raw = yaml.safe_load(f)

    pipeline = raw.get("pipeline", {})
    signals = raw.get("signals", {})
    backtest = raw.get("backtest", {})
    risk = raw.get("risk", {})
    llm = raw.get("llm", {})
    telegram = raw.get("telegram", {})
    top_coins = raw.get("top_coins", {})

    # Load .env
    env_path = config_dir.parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ[key.strip()] = value.strip()

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
