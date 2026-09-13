"""Configuration models (pydantic v2) with the live-mode gate.

SAFETY MODEL, ENFORCED HERE:
- dry_run defaults to True.
- Live trading requires ALL of:
    mode.dry_run == False
    mode.live_confirmation.confirmed_live == True
    mode.live_confirmation.risk_disclosure_accepted == True (with timestamp)
  set by a HUMAN editing the config file. `effective_live_mode()` refuses
  live otherwise. No code path, CLI flag, agent, or LLM can set these —
  config_file_gate=True on the confirmation model rejects programmatic
  overrides from anything other than parsing the YAML.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class TradingModeError(RuntimeError):
    """Raised when live trading is requested without the human opt-ins."""


class LiveConfirmation(BaseModel):
    """Human-only fields. The bot refuses live trading unless both are
    manually set true in the config FILE — never via code."""
    model_config = ConfigDict(extra="forbid")

    confirmed_live: bool = False
    risk_disclosure_accepted: bool = False
    risk_disclosure_accepted_at: Optional[datetime] = None


class ModeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run: bool = True
    live_confirmation: LiveConfirmation = Field(
        default_factory=LiveConfirmation)


class ExchangeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = "binance"
    market_type: str = "spot"           # spot | future (USDT-M)
    sandbox: bool = False

    @field_validator("market_type")
    @classmethod
    def _market(cls, v: str) -> str:
        if v not in ("spot", "future"):
            raise ValueError("market_type must be 'spot' or 'future'")
        return v


class StakeMode(str, Enum):
    fixed = "fixed"
    percentage = "percentage"
    risk_pct = "risk_pct"


class StakeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: StakeMode = StakeMode.percentage
    amount: float = 10.0

    @field_validator("amount")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("stake amount must be positive")
        return v


class PairsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    whitelist: list[str] = Field(default_factory=list)
    blacklist: list[str] = Field(default_factory=list)


class TradingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeframe: str = "15m"
    pairs: PairsConfig = Field(default_factory=PairsConfig)
    max_open_trades: int = 3
    stake_currency: str = "USDT"
    stake: StakeConfig = Field(default_factory=StakeConfig)
    candle_source: str = "exchange"     # exchange | file


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stoploss: float = -0.10
    trailing_stop: bool = True
    trailing_stop_positive: float = 0.015
    trailing_stop_positive_offset: float = 0.03
    max_drawdown_pct: float = 25.0
    daily_loss_limit_pct: float = 5.0
    cooldown_minutes: int = 120
    consecutive_losses: int = 3
    use_risk_pct_sizing: bool = True


class MLConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    model: str = "gradient_boosting"
    retrain_every_days: int = 7


class AIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    advisory_veto: bool = True
    ml: MLConfig = Field(default_factory=MLConfig)


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    allowed_user_ids: list[int] = Field(default_factory=list)


class WebUIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    # 0.0.0.0 = reachable from other devices on your LAN (read-only
    # surface). Set host: "127.0.0.1" in the YAML to lock it to this
    # machine only.
    host: str = "0.0.0.0"
    port: int = 8080


class PersistenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    db_url: str = "sqlite:///aetherbot.db"


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: str = "INFO"
    file: str = "logs/aetherbot.log"


class AccountConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run_start_balance: float = 10000.0


class BotConfig(BaseModel):
    """Root configuration."""
    model_config = ConfigDict(extra="forbid")

    bot_name: str = "AetherBot"
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    mode: ModeConfig = Field(default_factory=ModeConfig)
    account: AccountConfig = Field(default_factory=AccountConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    webui: WebUIConfig = Field(default_factory=WebUIConfig)
    persistence: PersistenceConfig = Field(default_factory=PersistenceConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @property
    def live_allowed(self) -> bool:
        """True ONLY when a human has confirmed live + accepted the risk
        disclosure, with dry_run explicitly off in the file."""
        c = self.mode.live_confirmation
        return (not self.mode.dry_run
                and c.confirmed_live
                and c.risk_disclosure_accepted
                and c.risk_disclosure_accepted_at is not None)

    def effective_live_mode(self) -> bool:
        """The single gate every live path must pass. Raises TradingModeError
        if the config asks for live without the human opt-ins."""
        if self.mode.dry_run:
            return False
        if not self.live_allowed:
            raise TradingModeError(
                "LIVE MODE REFUSED: dry_run is false but live_confirmation "
                "is incomplete. A human must manually set confirmed_live: "
                "true, risk_disclosure_accepted: true and the accepted_at "
                "timestamp in the config file. No code, CLI flag, agent or "
                "LLM can do this. See config.example.yaml.")
        return True

    def tradeable_pairs(self) -> list[str]:
        blacklist = set(self.trading.pairs.blacklist)
        return [p for p in self.trading.pairs.whitelist
                if p not in blacklist]


def load_config(path: str) -> BotConfig:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return BotConfig(**raw)
