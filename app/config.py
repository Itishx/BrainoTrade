from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List

from .data.nifty50 import NIFTY_50

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _watchlist_source_from_env() -> str:
    if os.getenv("WATCHLIST_SYMBOLS", "").strip():
        return "custom"
    source = os.getenv("WATCHLIST_SOURCE", "nifty50").strip().lower()
    if source in {"groww_nse_all", "nse_all", "all"}:
        return "groww_nse_all"
    return "nifty50"


def _watchlist_from_env() -> List[Dict[str, str]]:
    symbols = os.getenv("WATCHLIST_SYMBOLS", "").strip()
    if not symbols:
        return list(NIFTY_50)

    catalog = {item["symbol"]: item for item in NIFTY_50}
    watchlist = []
    for raw_symbol in symbols.split(","):
        symbol = raw_symbol.strip().upper()
        if not symbol:
            continue
        watchlist.append(catalog.get(symbol, {"symbol": symbol, "name": symbol}))
    return watchlist or list(NIFTY_50)


@dataclass
class Settings:
    secret_key: str
    timezone: str
    poll_interval_seconds: int
    account_refresh_interval_seconds: int
    candle_interval_minutes: int
    history_lookback_candles: int
    chart_candle_limit: int
    quote_bootstrap_batch_size: int
    market_scan_batch_size: int
    watchlist_panel_limit: int
    ai_candidate_pool_size: int
    quote_hydration_batch_size: int
    history_hydration_batch_size: int
    quote_detail_max_age_seconds: int
    market_min_price: float
    market_min_turnover: float
    max_capital_per_trade: float
    max_loss_per_day: float
    max_trades_per_day: int
    bot_starting_capital: float
    max_capital_loss_pct: float
    default_symbol: str
    use_demo_data: bool
    allow_live_trading: bool
    ai_decision_enabled: bool
    ai_decision_interval_seconds: int
    ai_watchlist_limit: int
    token_refresh_hour: int
    token_refresh_minute: int
    groww_api_access_token: str
    groww_api_key: str
    groww_api_secret: str
    openai_api_key: str
    openai_model: str
    stop_loss_pct: float
    hyper_ema_fast: int
    hyper_ema_slow: int
    hyper_max_trades: int
    watchlist_source: str
    watchlist: List[Dict[str, str]] = field(default_factory=list)


def load_settings() -> Settings:
    watchlist = _watchlist_from_env()
    watchlist_source = _watchlist_source_from_env()
    watchlist_symbols = {item["symbol"] for item in watchlist}
    default_symbol = os.getenv("DEFAULT_SYMBOL", "RELIANCE").strip().upper() or "RELIANCE"
    if default_symbol not in watchlist_symbols and watchlist:
        default_symbol = watchlist[0]["symbol"]

    return Settings(
        secret_key=os.getenv("FLASK_SECRET_KEY", "brainotrade-dev-secret"),
        timezone=os.getenv("TIMEZONE", "Asia/Kolkata"),
        poll_interval_seconds=_env_int("MARKET_POLL_INTERVAL_SECONDS", 5),
        account_refresh_interval_seconds=_env_int("ACCOUNT_REFRESH_INTERVAL_SECONDS", 45),
        candle_interval_minutes=_env_int("CANDLE_INTERVAL_MINUTES", 5),
        history_lookback_candles=_env_int("HISTORY_LOOKBACK_CANDLES", 96),
        chart_candle_limit=_env_int("CHART_CANDLE_LIMIT", 80),
        quote_bootstrap_batch_size=_env_int("QUOTE_BOOTSTRAP_BATCH_SIZE", 8),
        market_scan_batch_size=_env_int("MARKET_SCAN_BATCH_SIZE", 120),
        watchlist_panel_limit=_env_int("WATCHLIST_PANEL_LIMIT", 60),
        ai_candidate_pool_size=_env_int("AI_CANDIDATE_POOL_SIZE", 24),
        quote_hydration_batch_size=_env_int("QUOTE_HYDRATION_BATCH_SIZE", 6),
        history_hydration_batch_size=_env_int("HISTORY_HYDRATION_BATCH_SIZE", 2),
        quote_detail_max_age_seconds=_env_int("QUOTE_DETAIL_MAX_AGE_SECONDS", 180),
        market_min_price=_env_float("MARKET_MIN_PRICE", 20.0),
        market_min_turnover=_env_float("MARKET_MIN_TURNOVER", 2000000.0),
        max_capital_per_trade=_env_float("MAX_CAPITAL_PER_TRADE", 500.0),
        max_loss_per_day=_env_float("MAX_LOSS_PER_DAY", 200.0),
        max_trades_per_day=_env_int("MAX_TRADES_PER_DAY", 5),
        bot_starting_capital=_env_float("BOT_STARTING_CAPITAL", 1000.0),
        max_capital_loss_pct=_env_float("MAX_CAPITAL_LOSS_PCT", 80.0),
        default_symbol=default_symbol,
        use_demo_data=_env_bool("USE_DEMO_DATA", False),
        allow_live_trading=_env_bool("ALLOW_LIVE_TRADING", False),
        ai_decision_enabled=_env_bool("AI_DECISION_ENABLED", False),
        ai_decision_interval_seconds=_env_int("AI_DECISION_INTERVAL_SECONDS", 60),
        ai_watchlist_limit=_env_int("AI_WATCHLIST_LIMIT", 12),
        token_refresh_hour=_env_int("TOKEN_REFRESH_HOUR", 6),
        token_refresh_minute=_env_int("TOKEN_REFRESH_MINUTE", 30),
        groww_api_access_token=os.getenv("GROWW_API_ACCESS_TOKEN", "").strip(),
        groww_api_key=os.getenv("GROWW_API_KEY", "").strip(),
        groww_api_secret=os.getenv("GROWW_API_SECRET", "").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini").strip() or "gpt-5.4-mini",
        stop_loss_pct=_env_float("STOP_LOSS_PCT", 0.5),
        hyper_ema_fast=_env_int("HYPER_EMA_FAST", 3),
        hyper_ema_slow=_env_int("HYPER_EMA_SLOW", 8),
        hyper_max_trades=_env_int("HYPER_MAX_TRADES", 50),
        watchlist_source=watchlist_source,
        watchlist=watchlist,
    )
