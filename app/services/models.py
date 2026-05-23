from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        return {
            "time": self.time,
            "open": round(self.open, 2),
            "high": round(self.high, 2),
            "low": round(self.low, 2),
            "close": round(self.close, 2),
            "volume": round(self.volume, 2),
        }


@dataclass
class QuoteSnapshot:
    symbol: str
    name: str
    ltp: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    previous_close: float = 0.0
    volume: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    change_pct: float = 0.0
    updated_at: str = ""
    provider: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "ltp": round(self.ltp, 2),
            "open": round(self.open, 2),
            "high": round(self.high, 2),
            "low": round(self.low, 2),
            "previous_close": round(self.previous_close, 2),
            "volume": round(self.volume, 2),
            "bid": round(self.bid, 2),
            "ask": round(self.ask, 2),
            "change_pct": round(self.change_pct, 2),
            "updated_at": self.updated_at,
            "provider": self.provider,
        }


@dataclass
class SignalEvent:
    symbol: str
    name: str
    action: str
    price: float
    timestamp: str
    reason: str
    quantity: int = 0
    source: str = "rule"
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "action": self.action,
            "price": round(self.price, 2),
            "timestamp": self.timestamp,
            "reason": self.reason,
            "quantity": self.quantity,
            "source": self.source,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class PositionState:
    symbol: str
    quantity: int
    average_price: float
    opened_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "average_price": round(self.average_price, 2),
            "opened_at": self.opened_at,
        }


@dataclass
class TradeEvent:
    trade_id: str
    symbol: str
    name: str
    side: str
    price: float
    quantity: int
    timestamp: str
    pnl: float
    mode: str
    status: str
    remark: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "name": self.name,
            "side": self.side,
            "price": round(self.price, 2),
            "quantity": self.quantity,
            "timestamp": self.timestamp,
            "pnl": round(self.pnl, 2),
            "mode": self.mode,
            "status": self.status,
            "remark": self.remark,
        }


def serialize_optional(value: Optional[Any]) -> Optional[Any]:
    if value is None:
        return None
    return value.to_dict()
