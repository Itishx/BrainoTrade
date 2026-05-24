from __future__ import annotations

import math
import random
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from .models import Candle, QuoteSnapshot


class BaseMarketProvider:
    label = "Base Provider"
    demo_mode = False
    supports_live_orders = False
    supports_account_data = False
    status_message = ""

    def __init__(self, settings):
        self.settings = settings
        self.tz = ZoneInfo(settings.timezone)
        self.error_code = ""

    def is_ready(self) -> bool:
        return True

    def get_bulk_ltp(self, symbols: List[str]) -> Dict[str, float]:
        raise NotImplementedError

    def get_quote(self, symbol: str, name: str) -> Optional[QuoteSnapshot]:
        raise NotImplementedError

    def get_historical_candles(self, symbol: str, lookback_candles: int) -> List[Candle]:
        raise NotImplementedError

    def refresh_access_token(self) -> (bool, str):
        return False, "Token refresh is not available for this provider."

    def set_access_token(self, token: str) -> (bool, str):
        return False, "This provider does not support token updates."

    def set_status(self, message: str, code: str = "") -> None:
        self.status_message = message
        self.error_code = code

    def place_market_order(self, symbol: str, quantity: int, side: str, reference_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def get_account_snapshot(self) -> Dict[str, Any]:
        return {}

    def get_tradable_cash_universe(self) -> List[Dict[str, str]]:
        return []


class DemoMarketProvider(BaseMarketProvider):
    label = "Demo market simulator"
    demo_mode = True

    def __init__(self, settings, reason: str = ""):
        super().__init__(settings)
        self.set_status(reason or "Using synthetic market data because Groww is not configured.")
        self.random = random.Random(42)
        self.symbol_state: Dict[str, Dict[str, float]] = {}
        for item in settings.watchlist:
            self.symbol_state[item["symbol"]] = self._seed_state(item["symbol"])

    def _seed_state(self, symbol: str) -> Dict[str, float]:
        seeded = random.Random(symbol)
        base_price = max(85.0, seeded.uniform(120.0, 3400.0))
        day_open = round(base_price * seeded.uniform(0.985, 1.015), 2)
        current = round(day_open * seeded.uniform(0.975, 1.03), 2)
        spread = max(0.05, current * 0.0008)
        return {
            "price": current,
            "open": day_open,
            "high": max(current, day_open),
            "low": min(current, day_open),
            "volume": seeded.randint(200_000, 2_500_000),
            "phase": seeded.uniform(0.0, math.pi * 2),
            "volatility": max(0.15, current * seeded.uniform(0.0009, 0.0025)),
            "last_update": time.time(),
            "spread": spread,
        }

    def _tick(self, symbol: str) -> Dict[str, float]:
        state = self.symbol_state[symbol]
        now = time.time()
        elapsed = max(0.5, now - state["last_update"])
        wave = math.sin((now / 42.0) + state["phase"]) * state["volatility"] * 0.55
        noise = self.random.gauss(0, state["volatility"] * 0.15) * math.sqrt(elapsed)
        next_price = max(20.0, state["price"] + wave + noise)
        state["price"] = round(next_price, 2)
        state["high"] = max(state["high"], state["price"])
        state["low"] = min(state["low"], state["price"])
        state["volume"] += int(self.random.uniform(800, 4500) * elapsed)
        state["spread"] = max(0.05, state["price"] * 0.0008)
        state["last_update"] = now
        return state

    def get_bulk_ltp(self, symbols: List[str]) -> Dict[str, float]:
        prices = {}
        for symbol in symbols:
            state = self._tick(symbol)
            prices[symbol] = state["price"]
        return prices

    def get_quote(self, symbol: str, name: str) -> Optional[QuoteSnapshot]:
        state = self._tick(symbol)
        bid = round(state["price"] - state["spread"], 2)
        ask = round(state["price"] + state["spread"], 2)
        change_pct = ((state["price"] - state["open"]) / state["open"] * 100) if state["open"] else 0.0
        return QuoteSnapshot(
            symbol=symbol,
            name=name,
            ltp=state["price"],
            open=state["open"],
            high=state["high"],
            low=state["low"],
            previous_close=round(state["open"] * 0.995, 2),
            volume=float(state["volume"]),
            bid=bid,
            ask=ask,
            change_pct=change_pct,
            updated_at=datetime.now(self.tz).isoformat(),
            provider=self.label,
        )

    def get_historical_candles(self, symbol: str, lookback_candles: int) -> List[Candle]:
        seeded = random.Random(f"{symbol}-history")
        current_state = self.symbol_state[symbol]
        interval = self.settings.candle_interval_minutes
        bucket_seconds = interval * 60
        current_bucket = int(time.time() // bucket_seconds * bucket_seconds) - bucket_seconds
        price = current_state["open"] * seeded.uniform(0.97, 1.03)
        candles = []
        for index in range(lookback_candles):
            ts = current_bucket - (lookback_candles - index - 1) * bucket_seconds
            drift = math.sin(index / 5.0 + current_state["phase"]) * current_state["volatility"] * 0.5
            open_price = price
            close_price = max(20.0, open_price + drift + seeded.uniform(-1.2, 1.2) * current_state["volatility"] * 0.22)
            high_price = max(open_price, close_price) + seeded.uniform(0.1, current_state["volatility"] * 0.35)
            low_price = min(open_price, close_price) - seeded.uniform(0.1, current_state["volatility"] * 0.35)
            candles.append(
                Candle(
                    time=ts,
                    open=round(open_price, 2),
                    high=round(high_price, 2),
                    low=round(max(1.0, low_price), 2),
                    close=round(close_price, 2),
                    volume=float(seeded.randint(10_000, 90_000)),
                )
            )
            price = close_price
        return candles

    def refresh_access_token(self) -> (bool, str):
        return True, "Demo provider does not require token refresh."


class GrowwMarketProvider(BaseMarketProvider):
    label = "Groww live data"
    demo_mode = False
    supports_live_orders = True
    supports_account_data = True

    def __init__(self, settings):
        super().__init__(settings)
        self.client = None
        self.sdk = None
        self.status_message = ""
        self._initialize_client()

    def _initialize_client(self) -> None:
        try:
            from growwapi import GrowwAPI
        except ImportError:
            self.set_status("growwapi is not installed.")
            return

        self.sdk = GrowwAPI
        access_token = None

        # Priority 1: TOTP flow (permanent — never expires daily)
        if self.settings.groww_totp_token and self.settings.groww_totp_secret:
            try:
                import pyotp
                totp = pyotp.TOTP(self.settings.groww_totp_secret).now()
                access_token = GrowwAPI.get_access_token(
                    api_key=self.settings.groww_totp_token,
                    totp=totp,
                )
            except Exception as exc:
                self.set_status(f"Groww TOTP bootstrap failed: {exc}")
                return

        # Priority 2: Direct access token (manual daily paste)
        elif self.settings.groww_api_access_token:
            access_token = self.settings.groww_api_access_token

        # Priority 3: API key + secret
        elif self.settings.groww_api_key and self.settings.groww_api_secret:
            try:
                access_token = GrowwAPI.get_access_token(
                    api_key=self.settings.groww_api_key,
                    secret=self.settings.groww_api_secret,
                )
            except Exception as exc:
                self.set_status(f"Groww token bootstrap failed: {exc}")
                return

        if not access_token:
            self.set_status("No Groww credentials found. Set GROWW_TOTP_TOKEN + GROWW_TOTP_SECRET in .env.")
            return

        try:
            self.client = GrowwAPI(access_token)
            self.set_status("Connected to Groww live data.")
        except Exception as exc:
            self.client = None
            self.set_status(f"Groww client initialization failed: {exc}")

    def is_ready(self) -> bool:
        return self.client is not None

    def _unwrap_payload(self, response):
        if isinstance(response, dict) and "payload" in response:
            return response.get("payload") or {}
        return response or {}

    def _deep_get(self, data, *paths):
        for path in paths:
            current = data
            found = True
            for key in path.split("."):
                if isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    found = False
                    break
            if found:
                return current
        return None

    def _number(self, value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _best_book_price(self, book, side: str) -> float:
        prices = []
        if isinstance(book, list):
            for level in book:
                if isinstance(level, dict):
                    prices.append(self._number(level.get("price")))
        elif isinstance(book, dict):
            for level in book.values():
                if isinstance(level, dict):
                    prices.append(self._number(level.get("price")))
        prices = [price for price in prices if price > 0]
        if not prices:
            return 0.0
        return min(prices) if side == "ask" else max(prices)

    def _bool_flag(self, value: Any) -> bool:
        return str(value).strip() in {"1", "true", "True", "YES", "yes"}

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            try:
                if math.isnan(value):
                    return ""
            except TypeError:
                pass
        return str(value).strip()

    def get_tradable_cash_universe(self) -> List[Dict[str, str]]:
        if not self.client:
            return []

        try:
            instruments = self.client.get_all_instruments()
        except Exception as exc:
            self.set_status(f"Groww instrument universe load failed: {exc}", getattr(exc, "code", ""))
            return []

        records = instruments.to_dict(orient="records")
        allowed_series = {"EQ", "BE", "BZ", "SM", "ST", "IV"}
        excluded_name_fragments = {"-NCD", "GSEC", "GOVT", "BOND", "SDL", "TREASURY"}
        universe: List[Dict[str, str]] = []
        seen = set()

        for item in records:
            exchange = self._clean_text(item.get("exchange")).upper()
            segment = self._clean_text(item.get("segment")).upper()
            instrument_type = self._clean_text(item.get("instrument_type")).upper()
            series = self._clean_text(item.get("series")).upper()
            symbol = self._clean_text(item.get("trading_symbol")).upper()
            name = self._clean_text(item.get("name")) or symbol

            if exchange != "NSE" or segment != "CASH" or instrument_type != "EQ":
                continue
            if not self._bool_flag(item.get("buy_allowed")) or not self._bool_flag(item.get("sell_allowed")):
                continue
            if self._bool_flag(item.get("is_reserved")):
                continue
            if series not in allowed_series:
                continue
            if any(fragment in name.upper() for fragment in excluded_name_fragments):
                continue
            if not symbol or symbol in seen:
                continue

            seen.add(symbol)
            universe.append({"symbol": symbol, "name": name or symbol})

        universe.sort(key=lambda item: item["symbol"])
        if universe:
            self.set_status(f"Connected to Groww live data. Loaded {len(universe)} NSE cash stocks.")
        return universe

    def _list_from_payload(self, response, key: str) -> List[Dict[str, Any]]:
        payload = self._unwrap_payload(response)
        if isinstance(payload, dict):
            values = payload.get(key)
            if isinstance(values, list):
                return [item for item in values if isinstance(item, dict)]
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def get_bulk_ltp(self, symbols: List[str]) -> Dict[str, float]:
        if not self.client:
            return {}

        try:
            composite = tuple(f"NSE_{symbol}" for symbol in symbols)
            response = self.client.get_ltp(
                segment=self.client.SEGMENT_CASH,
                exchange_trading_symbols=composite,
            )
        except Exception as exc:
            self.set_status(f"Groww LTP request failed: {exc}", getattr(exc, "code", ""))
            return {}

        payload = self._unwrap_payload(response)
        results: Dict[str, float] = {}

        candidates = []
        if isinstance(payload, dict):
            candidates.append(payload)
            if "ltp" in payload and isinstance(payload["ltp"], dict):
                candidates.append(payload["ltp"])
            if "data" in payload and isinstance(payload["data"], dict):
                candidates.append(payload["data"])
        elif isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    symbol = item.get("trading_symbol") or item.get("symbol")
                    if symbol:
                        results[symbol] = self._number(
                            item.get("ltp") or item.get("last_traded_price") or item.get("price")
                        )

        for candidate in candidates:
            for key, value in candidate.items():
                symbol = key.split("_", 1)[1] if isinstance(key, str) and key.startswith("NSE_") else key
                if symbol not in symbols:
                    continue
                if isinstance(value, dict):
                    price = self._number(
                        value.get("ltp") or value.get("last_traded_price") or value.get("price")
                    )
                else:
                    price = self._number(value)
                if price > 0:
                    results[symbol] = price

        return results

    def get_quote(self, symbol: str, name: str) -> Optional[QuoteSnapshot]:
        if not self.client:
            return None

        try:
            response = self.client.get_quote(
                exchange=self.client.EXCHANGE_NSE,
                segment=self.client.SEGMENT_CASH,
                trading_symbol=symbol,
            )
        except Exception as exc:
            self.set_status(f"Groww quote request failed for {symbol}: {exc}", getattr(exc, "code", ""))
            return None

        payload = self._unwrap_payload(response)
        ohlc = self._deep_get(payload, "ohlc") or {}
        depth = (
            self._deep_get(payload, "market_depth")
            or self._deep_get(payload, "marketDepth")
            or self._deep_get(payload, "depth")
            or {}
        )
        buy_book = depth.get("buyBook") or depth.get("buy_book") or depth.get("buy") or {}
        sell_book = depth.get("sellBook") or depth.get("sell_book") or depth.get("sell") or {}

        ltp = self._number(
            self._deep_get(payload, "ltp", "last_price", "last_traded_price", "price", "close")
        )
        open_price = self._number(
            self._deep_get(payload, "open", "ohlc.open", "day_ohlc.open", "ohlc_data.open")
        )
        high_price = self._number(
            self._deep_get(payload, "high", "ohlc.high", "day_ohlc.high", "ohlc_data.high")
        )
        low_price = self._number(
            self._deep_get(payload, "low", "ohlc.low", "day_ohlc.low", "ohlc_data.low")
        )
        previous_close = self._number(
            self._deep_get(payload, "close", "previous_close", "ohlc.close", "day_ohlc.close")
        )
        volume = self._number(
            self._deep_get(payload, "volume", "volume_traded", "market_volume", "total_traded_volume")
        )

        bid = self._best_book_price(buy_book, "bid")
        ask = self._best_book_price(sell_book, "ask")
        change_pct = ((ltp - open_price) / open_price * 100) if open_price else 0.0

        if ltp <= 0 and open_price <= 0 and high_price <= 0 and low_price <= 0:
            self.set_status(f"Groww returned an empty quote payload for {symbol}.")
            return None

        self.set_status("Connected to Groww live data.")

        return QuoteSnapshot(
            symbol=symbol,
            name=name,
            ltp=ltp,
            open=open_price,
            high=high_price or max(open_price, ltp),
            low=low_price or min(open_price or ltp, ltp),
            previous_close=previous_close,
            volume=volume,
            bid=bid,
            ask=ask,
            change_pct=change_pct,
            updated_at=datetime.now(self.tz).isoformat(),
            provider=self.label,
        )

    def get_historical_candles(self, symbol: str, lookback_candles: int) -> List[Candle]:
        if not self.client:
            return []

        interval = self.settings.candle_interval_minutes
        end_time = datetime.now(self.tz)
        # Go back 5 calendar days so Monday-morning starts always capture Friday's session
        start_time = end_time - timedelta(days=5)
        try:
            response = self.client.get_historical_candle_data(
                trading_symbol=symbol,
                exchange=self.client.EXCHANGE_NSE,
                segment=self.client.SEGMENT_CASH,
                start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
                end_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
                interval_in_minutes=interval,
            )
        except Exception as exc:
            self.set_status(
                f"Groww historical candle request failed for {symbol}: {exc}",
                getattr(exc, "code", ""),
            )
            return []

        payload = self._unwrap_payload(response)
        raw_candles = payload.get("candles") if isinstance(payload, dict) else None
        candles: List[Candle] = []
        if not isinstance(raw_candles, list):
            self.set_status(f"Groww historical candle payload was empty for {symbol}.")
            return candles

        for item in raw_candles:
            if not isinstance(item, list) or len(item) < 5:
                continue
            volume = item[5] if len(item) > 5 else 0.0
            candles.append(
                Candle(
                    time=int(item[0]),
                    open=self._number(item[1]),
                    high=self._number(item[2]),
                    low=self._number(item[3]),
                    close=self._number(item[4]),
                    volume=self._number(volume),
                )
            )
        # Keep only the most recent lookback_candles to avoid feeding stale old data
        if len(candles) > lookback_candles:
            candles = candles[-lookback_candles:]
        if candles:
            self.set_status("Connected to Groww live data.")
        return candles

    def get_account_snapshot(self) -> Dict[str, Any]:
        if not self.client:
            return {}

        errors: List[str] = []
        profile: Dict[str, Any] = {}
        margin: Dict[str, Any] = {}
        holdings: List[Dict[str, Any]] = []
        positions: List[Dict[str, Any]] = []

        try:
            profile_response = self.client.get_user_profile()
            payload = self._unwrap_payload(profile_response)
            if isinstance(payload, dict):
                profile = payload
        except Exception as exc:
            errors.append(f"profile: {exc}")

        try:
            margin_response = self.client.get_available_margin_details()
            payload = self._unwrap_payload(margin_response)
            if isinstance(payload, dict):
                margin = payload
        except Exception as exc:
            errors.append(f"margin: {exc}")

        try:
            holdings = self._list_from_payload(self.client.get_holdings_for_user(), "holdings")
        except Exception as exc:
            errors.append(f"holdings: {exc}")

        try:
            positions = self._list_from_payload(
                self.client.get_positions_for_user(segment=self.client.SEGMENT_CASH),
                "positions",
            )
        except Exception as exc:
            errors.append(f"positions: {exc}")

        equity_margin = margin.get("equity_margin_details") if isinstance(margin, dict) else {}
        if not isinstance(equity_margin, dict):
            equity_margin = {}

        clear_cash = self._number(margin.get("clear_cash"))
        mis_balance_available = self._number(equity_margin.get("mis_balance_available"))
        cnc_balance_available = self._number(equity_margin.get("cnc_balance_available"))
        net_margin_used = self._number(margin.get("net_margin_used"))
        brokerage_and_charges = self._number(margin.get("brokerage_and_charges"))
        holdings_cost_value = sum(
            self._number(item.get("quantity")) * self._number(item.get("average_price"))
            for item in holdings
        )
        holding_symbols = []
        for item in holdings:
            symbol = (item.get("trading_symbol") or item.get("symbol") or "").strip().upper()
            if symbol:
                holding_symbols.append(symbol)
        holding_ltps = self.get_bulk_ltp(sorted(set(holding_symbols))) if holding_symbols else {}
        holdings_market_value = 0.0
        priced_holdings_count = 0
        for item in holdings:
            symbol = (item.get("trading_symbol") or item.get("symbol") or "").strip().upper()
            quantity = self._number(item.get("quantity"))
            ltp = self._number(holding_ltps.get(symbol))
            if quantity <= 0 or ltp <= 0:
                continue
            holdings_market_value += quantity * ltp
            priced_holdings_count += 1
        holdings_unrealized_pnl = holdings_market_value - holdings_cost_value
        holdings_return_pct = (
            (holdings_unrealized_pnl / holdings_cost_value * 100.0) if holdings_cost_value > 0 else 0.0
        )
        open_positions = []
        positions_realized_pnl = 0.0
        for item in positions:
            net_quantity = self._number(
                item.get("net_quantity")
                or item.get("net_carry_forward_quantity")
                or item.get("quantity")
                or (self._number(item.get("credit_quantity")) - self._number(item.get("debit_quantity")))
            )
            positions_realized_pnl += self._number(item.get("realised_pnl") or item.get("realized_pnl"))
            if abs(net_quantity) > 0:
                open_positions.append(item)

        snapshot = {
            "status": "ok" if not errors else "partial",
            "errors": errors,
            "updated_at": datetime.now(self.tz).isoformat(),
            "profile": {
                "vendor_user_id": profile.get("vendor_user_id") or "",
                "ucc": profile.get("ucc") or "",
                "nse_enabled": bool(profile.get("nse_enabled")),
                "bse_enabled": bool(profile.get("bse_enabled")),
                "ddpi_enabled": bool(profile.get("ddpi_enabled")),
                "active_segments": profile.get("active_segments") or [],
            },
            "margin": margin,
            "holdings": holdings,
            "positions": positions,
            "summary": {
                "clear_cash": clear_cash,
                "mis_balance_available": mis_balance_available,
                "cnc_balance_available": cnc_balance_available,
                "net_margin_used": net_margin_used,
                "brokerage_and_charges": brokerage_and_charges,
                "holdings_count": len(holdings),
                "holdings_cost_value": holdings_cost_value,
                "holdings_market_value": holdings_market_value,
                "holdings_unrealized_pnl": holdings_unrealized_pnl,
                "holdings_return_pct": holdings_return_pct,
                "priced_holdings_count": priced_holdings_count,
                "open_positions_count": len(open_positions),
                "positions_realized_pnl": positions_realized_pnl,
            },
        }

        if errors and not (profile or margin or holdings or positions):
            self.set_status(f"Groww account snapshot failed: {'; '.join(errors[:2])}")
        else:
            self.set_status("Connected to Groww live data.")
        return snapshot

    def refresh_access_token(self) -> (bool, str):
        if not self.sdk:
            return False, "growwapi is not installed."

        # Prefer TOTP refresh (no daily expiry)
        if self.settings.groww_totp_token and self.settings.groww_totp_secret:
            try:
                import pyotp
                totp = pyotp.TOTP(self.settings.groww_totp_secret).now()
                access_token = self.sdk.get_access_token(
                    api_key=self.settings.groww_totp_token,
                    totp=totp,
                )
                self.client = self.sdk(access_token)
                self.set_status("Groww access token refreshed via TOTP.")
                return True, "Groww access token refreshed via TOTP."
            except Exception as exc:
                self.set_status(f"Groww TOTP refresh failed: {exc}", getattr(exc, "code", ""))
                return False, f"Groww TOTP refresh failed: {exc}"

        if not self.settings.groww_api_key or not self.settings.groww_api_secret:
            return False, "No TOTP or API key/secret configured for auto-refresh."

        try:
            access_token = self.sdk.get_access_token(
                api_key=self.settings.groww_api_key,
                secret=self.settings.groww_api_secret,
            )
            self.client = self.sdk(access_token)
            self.set_status("Groww access token refreshed successfully.")
            return True, "Groww access token refreshed successfully."
        except Exception as exc:
            self.set_status(f"Groww token refresh failed: {exc}", getattr(exc, "code", ""))
            return False, f"Groww token refresh failed: {exc}"

    def set_access_token(self, token: str) -> (bool, str):
        if not self.sdk:
            return False, "growwapi is not installed."
        if not token:
            return False, "Token is empty."
        try:
            self.client = self.sdk(token)
            self.set_status("Groww access token updated successfully.")
            return True, "Token applied. Groww client reconnected."
        except Exception as exc:
            self.set_status(f"Token update failed: {exc}", getattr(exc, "code", ""))
            return False, f"Token update failed: {exc}"

    def place_market_order(self, symbol: str, quantity: int, side: str, reference_id: str) -> Dict[str, Any]:
        if not self.client:
            raise RuntimeError("Groww client is not ready for live orders.")

        transaction_type = (
            self.client.TRANSACTION_TYPE_BUY
            if side.upper() == "BUY"
            else self.client.TRANSACTION_TYPE_SELL
        )

        try:
            response = self.client.place_order(
                trading_symbol=symbol,
                quantity=quantity,
                validity=self.client.VALIDITY_DAY,
                exchange=self.client.EXCHANGE_NSE,
                segment=self.client.SEGMENT_CASH,
                product=self.client.PRODUCT_MIS,
                order_type=self.client.ORDER_TYPE_MARKET,
                transaction_type=transaction_type,
                order_reference_id=reference_id,
            )
        except Exception as exc:
            self.set_status(
                f"Groww {side.upper()} order failed for {symbol}: {exc}",
                getattr(exc, "code", ""),
            )
            raise

        order_payload = self._unwrap_payload(response)
        groww_order_id = order_payload.get("groww_order_id") or order_payload.get("order_id") or reference_id
        order_status = (order_payload.get("order_status") or "PLACED").upper()
        remark = order_payload.get("remark") or "Order placed."
        filled_quantity = int(self._number(order_payload.get("filled_quantity")) or quantity)
        average_fill_price = self._number(order_payload.get("average_fill_price"))

        if groww_order_id:
            try:
                detail_response = self.client.get_order_detail(
                    segment=self.client.SEGMENT_CASH,
                    groww_order_id=groww_order_id,
                )
                detail_payload = self._unwrap_payload(detail_response)
                order_status = (detail_payload.get("order_status") or order_status).upper()
                remark = detail_payload.get("remark") or remark
                filled_quantity = int(self._number(detail_payload.get("filled_quantity")) or filled_quantity)
                average_fill_price = self._number(detail_payload.get("average_fill_price")) or average_fill_price
            except Exception:
                pass

        self.set_status("Connected to Groww live data.")
        return {
            "groww_order_id": groww_order_id,
            "order_status": order_status,
            "remark": remark,
            "filled_quantity": filled_quantity,
            "average_fill_price": average_fill_price,
        }


def build_market_provider(settings) -> BaseMarketProvider:
    if not settings.use_demo_data:
        groww_provider = GrowwMarketProvider(settings)
        if groww_provider.is_ready():
            if settings.watchlist_source == "groww_nse_all":
                universe = groww_provider.get_tradable_cash_universe()
                if universe:
                    settings.watchlist = universe
                    symbols = {item["symbol"] for item in universe}
                    if settings.default_symbol not in symbols:
                        settings.default_symbol = "RELIANCE" if "RELIANCE" in symbols else universe[0]["symbol"]
            return groww_provider
        return DemoMarketProvider(settings, reason=groww_provider.status_message)

    return DemoMarketProvider(settings)
