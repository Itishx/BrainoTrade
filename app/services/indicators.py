from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from .models import Candle


def ema_series(values: List[float], period: int) -> List[float]:
    if not values:
        return []

    multiplier = 2 / (period + 1)
    seed_count = min(period, len(values))
    running = sum(values[:seed_count]) / seed_count  # SMA seed, not just values[0]
    series = []
    for value in values:
        running = (value - running) * multiplier + running
        series.append(round(running, 4))
    return series


def detect_crossover(fast: List[float], slow: List[float]) -> Optional[str]:
    if len(fast) < 2 or len(slow) < 2:
        return None

    prev_fast = fast[-2]
    prev_slow = slow[-2]
    curr_fast = fast[-1]
    curr_slow = slow[-1]

    if prev_fast <= prev_slow and curr_fast > curr_slow:
        return "BUY"
    if prev_fast >= prev_slow and curr_fast < curr_slow:
        return "SELL"
    return None


def series_to_points(candles: List[Candle], values: List[float]) -> List[Dict[str, float]]:
    points = []
    for candle, value in zip(candles, values):
        points.append({"time": candle.time, "value": round(value, 2)})
    return points


def upsert_live_candle(
    candles: List[Candle],
    price: float,
    timestamp: datetime,
    interval_minutes: int,
    volume_delta: float = 0.0,
) -> None:
    bucket_size = interval_minutes * 60
    bucket = int(timestamp.timestamp() // bucket_size * bucket_size)

    if not candles or candles[-1].time != bucket:
        candles.append(
            Candle(
                time=bucket,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=max(volume_delta, 0.0),
            )
        )
        return

    candle = candles[-1]
    candle.high = max(candle.high, price)
    candle.low = min(candle.low, price)
    candle.close = price
    candle.volume += max(volume_delta, 0.0)

