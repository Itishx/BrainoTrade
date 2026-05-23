from __future__ import annotations

import math
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import random
from typing import Deque, Dict, List, Optional
from zoneinfo import ZoneInfo

from .ai_decision_engine import OpenAIDecisionEngine
from .indicators import detect_crossover, ema_series, series_to_points, upsert_live_candle
from .models import Candle, PositionState, QuoteSnapshot, SignalEvent, TradeEvent, serialize_optional

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:  # pragma: no cover - optional at runtime
    BackgroundScheduler = None


@dataclass
class SymbolRuntimeState:
    symbol: str
    name: str
    quote: QuoteSnapshot
    candles: List[Candle] = field(default_factory=list)
    ema9: List[Dict[str, float]] = field(default_factory=list)
    ema21: List[Dict[str, float]] = field(default_factory=list)
    latest_signal: Optional[SignalEvent] = None
    latest_crossover: Optional[SignalEvent] = None
    position: Optional[PositionState] = None
    history_loaded: bool = False
    history_is_real: bool = False
    quote_details_loaded: bool = False
    last_signal_key: str = ""
    last_total_volume: float = 0.0
    last_quote_detail_refresh_monotonic: float = -1_000_000_000.0
    last_history_attempt_monotonic: float = -1_000_000_000.0


class TradingEngine:
    LIVE_FILLED_STATUSES = {"COMPLETED", "EXECUTED"}
    LIVE_FAILED_STATUSES = {"REJECTED", "FAILED", "CANCELLED"}

    def __init__(self, settings, provider):
        self.settings = settings
        self.provider = provider
        self.ai_engine = OpenAIDecisionEngine(settings)
        self.tz = ZoneInfo(settings.timezone)
        self.catalog = {item["symbol"]: item["name"] for item in settings.watchlist}
        self.symbol_order = [item["symbol"] for item in settings.watchlist]
        if settings.watchlist_source == "groww_nse_all" and len(self.symbol_order) > settings.market_scan_batch_size:
            shuffle_seed = int(self._local_now().strftime("%Y%m%d"))
            random.Random(shuffle_seed).shuffle(self.symbol_order)
        self.states: Dict[str, SymbolRuntimeState] = {
            item["symbol"]: SymbolRuntimeState(
                symbol=item["symbol"],
                name=item["name"],
                quote=QuoteSnapshot(symbol=item["symbol"], name=item["name"], provider=provider.label),
            )
            for item in settings.watchlist
        }
        self.selected_symbol = settings.default_symbol
        self.mode = "paper"
        self.latest_signal: Optional[SignalEvent] = None
        self.trade_log: Deque[TradeEvent] = deque(maxlen=200)
        self.thinking_log: Deque[Dict[str, str]] = deque(maxlen=120)
        self.alerts: Deque[Dict[str, str]] = deque(maxlen=8)
        self._last_scan_log: Dict[str, float] = {}
        self.trade_count = 0
        self.realized_pnl = 0.0
        self.wallet_realized_pnl = 0.0
        self.bot_halted = False
        self.stop_reason = ""
        self.session_day = self._local_now().date()
        self.account_snapshot: Dict[str, object] = {}
        self.peak_bot_equity = max(0.0, settings.bot_starting_capital)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._refresh_thread: Optional[threading.Thread] = None
        self._bootstrap_thread: Optional[threading.Thread] = None
        self._scheduler = None
        self._started = False
        self._last_provider_status = ""
        self._last_account_refresh_monotonic = -float(settings.account_refresh_interval_seconds)
        self._last_ai_decision_monotonic = -float(settings.ai_decision_interval_seconds)
        self._ai_request_in_flight = False
        self._scan_cursor = 0

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._sync_provider_status()
        self._refresh_account_snapshot(force=True)
        if self.settings.ai_decision_enabled:
            self._push_alert("info", self.ai_engine.status_message)
        self._prime_default_symbol()
        self._start_scheduler()
        self._refresh_thread = threading.Thread(target=self._run_refresh_loop, name="market-refresh", daemon=True)
        self._refresh_thread.start()
        self._bootstrap_thread = threading.Thread(target=self._bootstrap_watchlist, name="bootstrap", daemon=True)
        self._bootstrap_thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None

    def set_mode(self, mode: str) -> (bool, str):
        desired_mode = (mode or "paper").strip().lower()
        if desired_mode not in {"paper", "live", "hyper"}:
            return False, "Mode must be 'paper', 'live', or 'hyper'."

        if desired_mode == "live":
            self._refresh_account_snapshot(force=True)
            if not self.settings.allow_live_trading:
                self._push_alert(
                    "warning",
                    "Live trading is blocked by ALLOW_LIVE_TRADING=false in .env. Paper mode remains active.",
                )
                return False, "Set ALLOW_LIVE_TRADING=true in .env to enable real orders."
            if not self.provider.supports_live_orders or not self.provider.is_ready():
                self._push_alert("warning", "Groww live execution is not ready, so paper mode remains active.")
                return False, "Groww live execution is not ready."
            self.mode = "live"
            self._push_alert("warning", "Live mode enabled. Future signals can place real Groww MIS market orders.")
            return True, "Live mode enabled."

        if desired_mode == "hyper":
            self._refresh_account_snapshot(force=True)
            if not self.settings.allow_live_trading:
                self._push_alert(
                    "warning",
                    "Hyper mode is blocked by ALLOW_LIVE_TRADING=false in .env. Paper mode remains active.",
                )
                return False, "Set ALLOW_LIVE_TRADING=true in .env to enable real orders."
            if not self.provider.supports_live_orders or not self.provider.is_ready():
                self._push_alert("warning", "Groww live execution is not ready, so paper mode remains active.")
                return False, "Groww live execution is not ready."
            self.mode = "hyper"
            self._push_alert(
                "warning",
                f"Hyper mode enabled. EMA {self.settings.hyper_ema_fast}/{self.settings.hyper_ema_slow}, "
                f"{self.settings.hyper_max_trades} trades/day cap. Real Groww orders will be placed.",
            )
            return True, "Hyper mode enabled."

        self.mode = "paper"
        self._push_alert("info", "Paper mode enabled.")
        return True, "Paper mode enabled."

    def get_dashboard_state(self, selected_symbol: Optional[str]) -> Dict[str, object]:
        resolved = (selected_symbol or self.selected_symbol or self.settings.default_symbol).upper()
        if resolved in self.states:
            self._ensure_history(resolved)
        with self._lock:
            symbol = resolved
            if symbol in self.states:
                self.selected_symbol = symbol
            selected_state = self.states[self.selected_symbol]
            session_pnl = self._session_pnl_locked()
            capital = self._bot_capital_snapshot_locked()
            broker = self._broker_account_view_locked()

            ranked_candidates = self._rank_market_candidates_locked()
            watchlist = self._watchlist_panel_locked(ranked_candidates)
            positions = self._open_positions_view_locked(ranked_candidates)
            selected = {
                **selected_state.quote.to_dict(),
                "candles": [candle.to_dict() for candle in selected_state.candles[-self.settings.chart_candle_limit :]],
                "ema9": selected_state.ema9[-self.settings.chart_candle_limit :],
                "ema21": selected_state.ema21[-self.settings.chart_candle_limit :],
                "latest_signal": serialize_optional(selected_state.latest_signal),
                "position": serialize_optional(selected_state.position),
                "history_loaded": selected_state.history_loaded,
            }

            return {
                "as_of": self._local_now().isoformat(),
                "provider": {
                    "name": self.provider.label,
                    "demo_mode": self.provider.demo_mode,
                    "supports_live_orders": self.provider.supports_live_orders,
                    "supports_account_data": getattr(self.provider, "supports_account_data", False),
                },
                "decision_engine": {
                    "mode": "ai" if self.ai_engine.is_enabled() else "ema",
                    "model": self.settings.openai_model if self.ai_engine.is_enabled() else "",
                    "enabled": self.ai_engine.is_enabled(),
                },
                "mode": self.mode,
                "bot": {
                    "halted": self.bot_halted,
                    "stop_reason": self.stop_reason,
                    "active": not self.bot_halted,
                },
                "limits": {
                    "max_capital_per_trade": self.settings.max_capital_per_trade,
                    "max_loss_per_day": self.settings.max_loss_per_day,
                    "max_trades_per_day": self.settings.hyper_max_trades if self.mode == "hyper" else self.settings.max_trades_per_day,
                    "bot_starting_capital": self.settings.bot_starting_capital,
                    "max_capital_loss_pct": self.settings.max_capital_loss_pct,
                    "stop_loss_pct": self.settings.stop_loss_pct,
                },
                "session": {
                    "trade_count": self.trade_count,
                    "realized_pnl": round(self.realized_pnl, 2),
                    "session_pnl": round(session_pnl, 2),
                    "open_positions": sum(1 for state in self.states.values() if state.position),
                },
                "capital": capital,
                "broker": broker,
                "scanner": self._scanner_status_locked(ranked_candidates),
                "watchlist": watchlist,
                "positions": positions,
                "selected_symbol": self.selected_symbol,
                "selected": selected,
                "latest_signal": serialize_optional(self.latest_signal),
                "alerts": list(self.alerts),
                "thinking": list(self.thinking_log),
                "trades": [trade.to_dict() for trade in self.trade_log],
            }

    def _prime_default_symbol(self) -> None:
        self._ensure_history(self.selected_symbol)
        quote = self.provider.get_quote(self.selected_symbol, self.catalog[self.selected_symbol])
        if quote:
            with self._lock:
                self._apply_quote_locked(quote)

    def _bootstrap_watchlist(self) -> None:
        for symbol in self._bootstrap_symbols():
            if self._stop_event.is_set():
                return
            name = self.catalog[symbol]
            self._ensure_history(symbol)
            quote = self.provider.get_quote(symbol, name)
            if quote:
                with self._lock:
                    self._apply_quote_locked(quote)
            else:
                self._sync_provider_status()
            time.sleep(0.12)

    def _run_refresh_loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self.refresh_market()
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                self._push_alert("error", f"Market refresh failed: {exc}")
            elapsed = time.monotonic() - started
            sleep_for = max(0.4, self.settings.poll_interval_seconds - elapsed)
            self._stop_event.wait(sleep_for)

    def refresh_market(self) -> None:
        symbols = self._next_scan_symbols()
        ltps = self.provider.get_bulk_ltp(symbols)
        selected_symbol = self.selected_symbol
        selected_quote = self.provider.get_quote(selected_symbol, self.catalog[selected_symbol])
        account_snapshot = self._refresh_account_snapshot_if_due()
        now = self._local_now()

        with self._lock:
            self._reset_daily_if_needed_locked(now.date())
            for symbol, ltp in ltps.items():
                self._apply_ltp_locked(symbol, ltp, now)
            detail_targets = self._quote_hydration_targets_locked(symbols, selected_symbol)
            history_targets = self._history_hydration_targets_locked(symbols, selected_symbol)

        detail_quotes = []
        for symbol in detail_targets:
            quote = self.provider.get_quote(symbol, self.catalog[symbol])
            if quote:
                detail_quotes.append(quote)

        self._ensure_history(selected_symbol)
        for symbol in history_targets:
            if symbol != selected_symbol:
                self._ensure_history(symbol)

        with self._lock:
            if selected_quote:
                self._apply_quote_locked(selected_quote, now)
            for quote in detail_quotes:
                if quote.symbol != selected_symbol:
                    self._apply_quote_locked(quote, now)
            if account_snapshot:
                self.account_snapshot = account_snapshot
            self._enforce_risk_limits_locked()
        self._sync_provider_status()
        self._maybe_run_ai_decision()

    def _bootstrap_symbols(self) -> List[str]:
        must_include = [self.selected_symbol]
        batch_size = max(12, self.settings.quote_bootstrap_batch_size * 4)
        if len(self.symbol_order) <= batch_size:
            bootstrap = must_include + self.symbol_order
        else:
            step = len(self.symbol_order) / float(batch_size)
            sampled = [self.symbol_order[min(len(self.symbol_order) - 1, int(index * step))] for index in range(batch_size)]
            bootstrap = must_include + sampled
        seen = set()
        ordered = []
        for symbol in bootstrap:
            if symbol in self.states and symbol not in seen:
                seen.add(symbol)
                ordered.append(symbol)
        return ordered

    def _next_scan_symbols(self) -> List[str]:
        total = len(self.symbol_order)
        if total <= self.settings.market_scan_batch_size:
            batch = list(self.symbol_order)
        else:
            start = self._scan_cursor
            end = start + self.settings.market_scan_batch_size
            if end <= total:
                batch = self.symbol_order[start:end]
            else:
                batch = self.symbol_order[start:] + self.symbol_order[: end - total]
            self._scan_cursor = end % total

        with self._lock:
            must_include = [self.selected_symbol]
            must_include.extend(state.symbol for state in self.states.values() if state.position)
            must_include.extend(self._priority_scan_symbols_locked())

        seen = set()
        ordered = []
        for symbol in batch + must_include:
            if symbol in self.states and symbol not in seen:
                seen.add(symbol)
                ordered.append(symbol)
        return ordered

    def _priority_scan_symbols_locked(self) -> List[str]:
        focus_limit = max(4, min(12, self.settings.ai_candidate_pool_size // 2 or 4))
        ranked = self._rank_market_candidates_locked(limit=focus_limit)
        return [item["state"].symbol for item in ranked]

    def _watchlist_panel_locked(self, ranked_candidates: Optional[List[Dict[str, object]]] = None) -> List[Dict[str, object]]:
        ranked_candidates = ranked_candidates if ranked_candidates is not None else self._rank_market_candidates_locked()
        panel_states: List[SymbolRuntimeState] = [item["state"] for item in ranked_candidates[: self.settings.watchlist_panel_limit]]
        seen = {state.symbol for state in panel_states}

        active_states = [state for state in self.states.values() if state.quote.ltp > 0]
        if not panel_states and not active_states:
            return [
                self.states[item["symbol"]].quote.to_dict()
                for item in self.settings.watchlist[: self.settings.watchlist_panel_limit]
            ]

        if not panel_states:
            panel_states = sorted(
                active_states,
                key=lambda item: (abs(item.quote.change_pct), item.quote.volume, item.quote.ltp),
                reverse=True,
            )[: self.settings.watchlist_panel_limit]
            seen = {state.symbol for state in panel_states}

        selected_state = self.states.get(self.selected_symbol)
        if selected_state and selected_state.symbol not in seen:
            panel_states.insert(0, selected_state)
            seen.add(selected_state.symbol)

        if len(panel_states) < self.settings.watchlist_panel_limit:
            ranked_active_states = sorted(
                active_states,
                key=lambda item: (item.quote_details_loaded, abs(item.quote.change_pct), item.quote.volume, item.quote.ltp),
                reverse=True,
            )
            for state in ranked_active_states:
                if state.symbol in seen:
                    continue
                panel_states.append(state)
                seen.add(state.symbol)
                if len(panel_states) >= self.settings.watchlist_panel_limit:
                    break

        rows = []
        metrics_lookup = {item["state"].symbol: item["metrics"] for item in ranked_candidates}
        for state in panel_states[: self.settings.watchlist_panel_limit]:
            row = state.quote.to_dict()
            metrics = metrics_lookup.get(state.symbol)
            if metrics:
                row["opportunity_score"] = round(float(metrics["score"]), 2)
                row["trend_bias"] = metrics["trend_bias"]
            rows.append(row)
        return rows

    def _full_scan_cycle_seconds_locked(self) -> int:
        total = len(self.symbol_order)
        warmed = sum(1 for state in self.states.values() if state.quote.ltp > 0)
        batch_size = min(total, self.settings.market_scan_batch_size)
        cycles = ((total + batch_size - 1) // batch_size) if batch_size else 0
        full_cycle_seconds = cycles * self.settings.poll_interval_seconds
        return full_cycle_seconds

    def _scanner_status_locked(self, ranked_candidates: Optional[List[Dict[str, object]]] = None) -> Dict[str, object]:
        total = len(self.symbol_order)
        warmed = sum(1 for state in self.states.values() if state.quote.ltp > 0)
        hydrated = sum(1 for state in self.states.values() if state.quote_details_loaded)
        real_history = sum(1 for state in self.states.values() if state.history_is_real)
        batch_size = min(total, self.settings.market_scan_batch_size)
        full_cycle_seconds = self._full_scan_cycle_seconds_locked()
        ranked_candidates = ranked_candidates if ranked_candidates is not None else self._rank_market_candidates_locked()
        return {
            "source": self.settings.watchlist_source,
            "total_symbols": total,
            "warmed_symbols": warmed,
            "hydrated_symbols": hydrated,
            "real_history_symbols": real_history,
            "eligible_symbols": len(ranked_candidates),
            "scan_batch_size": batch_size,
            "full_cycle_seconds": full_cycle_seconds,
            "focus_symbols": [item["state"].symbol for item in ranked_candidates[:6]],
        }

    def _quote_age_seconds_locked(self, state: SymbolRuntimeState) -> float:
        if not state.quote.updated_at:
            return float("inf")
        try:
            updated_at = datetime.fromisoformat(state.quote.updated_at)
        except ValueError:
            return float("inf")
        return max(0.0, (self._local_now() - updated_at).total_seconds())

    def _recent_crossover_minutes_locked(self, state: SymbolRuntimeState) -> Optional[float]:
        signal = state.latest_crossover
        if not signal or not signal.timestamp:
            return None
        try:
            timestamp = datetime.fromisoformat(signal.timestamp)
        except ValueError:
            return None
        return max(0.0, (self._local_now() - timestamp).total_seconds() / 60.0)

    def _candidate_metrics_locked(self, state: SymbolRuntimeState) -> Optional[Dict[str, object]]:
        quote = state.quote
        if (
            not state.history_is_real
            or not state.quote_details_loaded
            or quote.ltp < self.settings.market_min_price
            or quote.open <= 0
            or quote.volume <= 0
            or not state.ema9
            or not state.ema21
        ):
            return None

        turnover = quote.ltp * quote.volume
        if turnover < self.settings.market_min_turnover:
            return None

        age_seconds = self._quote_age_seconds_locked(state)
        if age_seconds > max(self._full_scan_cycle_seconds_locked() * 2, 300):
            return None

        fast_value = state.ema9[-1]["value"]
        slow_value = state.ema21[-1]["value"]
        ema_gap = fast_value - slow_value
        ema_gap_pct = abs(ema_gap) / quote.ltp * 100 if quote.ltp > 0 else 0.0
        spread_pct = ((quote.ask - quote.bid) / quote.ltp * 100) if quote.bid > 0 and quote.ask > 0 and quote.ltp > 0 else 0.0
        crossover_age = self._recent_crossover_minutes_locked(state)
        freshness_score = max(
            0.0,
            1.0 - (age_seconds / max(float(self._full_scan_cycle_seconds_locked()), 60.0)),
        ) * 12.0
        momentum_score = min(abs(quote.change_pct) / 2.5, 2.0) * 18.0
        turnover_score = min(max(math.log10(turnover + 1) - 5.8, 0.0), 2.4) * 8.0
        trend_score = min(ema_gap_pct / 0.25, 2.0) * 18.0
        alignment_score = 8.0 if ema_gap * quote.change_pct > 0 else 0.0
        crossover_bonus = 10.0 if crossover_age is not None and crossover_age <= 30.0 else 0.0
        spread_penalty = min(spread_pct / 1.0, 1.0) * 10.0 if spread_pct > 0 else 0.0
        score = momentum_score + turnover_score + trend_score + freshness_score + alignment_score + crossover_bonus - spread_penalty
        trend_bias = "bullish" if ema_gap > 0 and quote.change_pct >= 0 else "bearish" if ema_gap < 0 and quote.change_pct <= 0 else "mixed"

        reason_parts = [
            f"{abs(quote.change_pct):.2f}% move",
            f"turnover ₹{turnover / 10000000:.2f}Cr",
            f"EMA gap {ema_gap_pct:.2f}%",
        ]
        if crossover_age is not None and crossover_age <= 30.0 and state.latest_crossover:
            reason_parts.append(f"recent {state.latest_crossover.action.lower()} crossover")
        if state.position:
            reason_parts.append("open position")

        return {
            "score": score,
            "turnover": turnover,
            "ema_gap_pct": ema_gap_pct,
            "trend_bias": trend_bias,
            "freshness_seconds": age_seconds,
            "spread_pct": spread_pct,
            "recent_crossover_minutes": crossover_age,
            "rank_reason": ", ".join(reason_parts),
        }

    def _rank_market_candidates_locked(self, limit: Optional[int] = None) -> List[Dict[str, object]]:
        ranked_candidates: List[Dict[str, object]] = []
        for state in self.states.values():
            metrics = self._candidate_metrics_locked(state)
            if not metrics:
                continue
            ranked_candidates.append({"state": state, "metrics": metrics})

        ranked_candidates.sort(
            key=lambda item: (
                float(item["metrics"]["score"]),
                float(item["metrics"]["turnover"]),
                abs(item["state"].quote.change_pct),
            ),
            reverse=True,
        )
        if limit is None:
            return ranked_candidates
        return ranked_candidates[:limit]

    def _open_positions_view_locked(self, ranked_candidates: Optional[List[Dict[str, object]]] = None) -> List[Dict[str, object]]:
        ranked_candidates = ranked_candidates if ranked_candidates is not None else self._rank_market_candidates_locked()
        metrics_lookup = {item["state"].symbol: item["metrics"] for item in ranked_candidates}
        positions: List[Dict[str, object]] = []

        for state in self.states.values():
            if not state.position:
                continue

            average_price = float(state.position.average_price)
            quantity = int(state.position.quantity)
            ltp = float(state.quote.ltp or average_price)
            cost_basis = average_price * quantity
            market_value = ltp * quantity
            unrealized_pnl = market_value - cost_basis
            return_pct = ((ltp - average_price) / average_price * 100.0) if average_price > 0 else 0.0
            stop_price = average_price * (1.0 - self.settings.stop_loss_pct / 100.0)
            metrics = metrics_lookup.get(state.symbol) or {}
            ema_fast = state.ema9[-1]["value"] if state.ema9 else 0.0
            ema_slow = state.ema21[-1]["value"] if state.ema21 else 0.0
            if ema_fast > ema_slow and state.quote.change_pct >= 0:
                trend_bias = "bullish"
            elif ema_fast < ema_slow and state.quote.change_pct <= 0:
                trend_bias = "bearish"
            else:
                trend_bias = "mixed"

            positions.append(
                {
                    "symbol": state.symbol,
                    "name": state.name,
                    "quantity": quantity,
                    "average_price": round(average_price, 2),
                    "ltp": round(ltp, 2),
                    "market_value": round(market_value, 2),
                    "cost_basis": round(cost_basis, 2),
                    "unrealized_pnl": round(unrealized_pnl, 2),
                    "return_pct": round(return_pct, 2),
                    "stop_price": round(stop_price, 2),
                    "opened_at": state.position.opened_at,
                    "latest_signal": state.latest_signal.action if state.latest_signal else "",
                    "latest_crossover": state.latest_crossover.action if state.latest_crossover else "",
                    "opportunity_score": round(float(metrics.get("score") or 0.0), 2),
                    "trend_bias": metrics.get("trend_bias") or trend_bias,
                    "rank_reason": metrics.get("rank_reason") or "Tracked continuously because this is an open position.",
                }
            )

        positions.sort(key=lambda item: (abs(float(item["market_value"])), abs(float(item["unrealized_pnl"]))), reverse=True)
        return positions

    def _quote_hydration_targets_locked(self, scanned_symbols: List[str], selected_symbol: str) -> List[str]:
        if self.settings.quote_hydration_batch_size <= 0:
            return []

        now_mono = time.monotonic()
        ranked_targets = []
        for symbol in scanned_symbols:
            if symbol == selected_symbol:
                continue
            state = self.states[symbol]
            detail_age = now_mono - state.last_quote_detail_refresh_monotonic
            needs_details = (
                not state.quote_details_loaded
                or state.quote.open <= 0
                or state.quote.volume <= 0
            )
            if not needs_details and detail_age < self.settings.quote_detail_max_age_seconds:
                continue

            recent_crossover = self._recent_crossover_minutes_locked(state)
            ranked_targets.append(
                (
                    1 if state.position else 0,
                    1 if recent_crossover is not None and recent_crossover <= 30.0 else 0,
                    1 if needs_details else 0,
                    detail_age,
                    abs(state.quote.change_pct),
                    state.quote.ltp,
                    symbol,
                )
            )

        ranked_targets.sort(reverse=True)
        selected_targets = [item[-1] for item in ranked_targets[: self.settings.quote_hydration_batch_size]]
        for symbol in selected_targets:
            self.states[symbol].last_quote_detail_refresh_monotonic = now_mono
        return selected_targets

    def _history_hydration_targets_locked(self, scanned_symbols: List[str], selected_symbol: str) -> List[str]:
        if self.settings.history_hydration_batch_size <= 0:
            return []

        now_mono = time.monotonic()
        retry_cooldown = max(float(self._full_scan_cycle_seconds_locked()), 300.0)
        candidates = []
        for symbol in scanned_symbols:
            state = self.states[symbol]
            if symbol != selected_symbol and state.history_is_real:
                continue
            if state.quote.ltp < self.settings.market_min_price:
                continue
            if symbol != selected_symbol and not state.quote_details_loaded:
                continue
            if (now_mono - state.last_history_attempt_monotonic) < retry_cooldown:
                continue

            recent_crossover = self._recent_crossover_minutes_locked(state)
            candidates.append(
                (
                    1 if symbol == selected_symbol else 0,
                    1 if state.position else 0,
                    1 if recent_crossover is not None and recent_crossover <= 30.0 else 0,
                    abs(state.quote.change_pct),
                    state.quote.volume,
                    symbol,
                )
            )

        candidates.sort(reverse=True)
        selected_targets = [item[-1] for item in candidates[: self.settings.history_hydration_batch_size]]
        for symbol in selected_targets:
            self.states[symbol].last_history_attempt_monotonic = now_mono
        return selected_targets

    def _apply_ltp_locked(self, symbol: str, ltp: float, timestamp: datetime) -> None:
        state = self.states[symbol]
        quote = state.quote
        price = round(float(ltp), 2)
        quote.ltp = price
        if not quote.open:
            quote.open = price
        quote.high = max(quote.high or price, price)
        quote.low = min(quote.low or price, price)
        quote.change_pct = ((price - quote.open) / quote.open * 100) if quote.open else 0.0
        quote.updated_at = timestamp.isoformat()
        quote.provider = self.provider.label
        upsert_live_candle(state.candles, price, timestamp, self.settings.candle_interval_minutes)
        self._check_stop_loss_locked(state)
        self._refresh_strategy_locked(state)

    def _apply_quote_locked(self, quote: QuoteSnapshot, timestamp: Optional[datetime] = None) -> None:
        state = self.states[quote.symbol]
        previous_volume = state.last_total_volume
        state.last_total_volume = max(previous_volume, quote.volume)
        quote.updated_at = (timestamp or self._local_now()).isoformat()
        quote.provider = self.provider.label
        state.quote = quote
        state.quote_details_loaded = True
        state.last_quote_detail_refresh_monotonic = time.monotonic()
        volume_delta = max(0.0, quote.volume - previous_volume)
        upsert_live_candle(
            state.candles,
            quote.ltp,
            timestamp or self._local_now(),
            self.settings.candle_interval_minutes,
            volume_delta=volume_delta,
        )
        self._check_stop_loss_locked(state)
        self._refresh_strategy_locked(state)

    def _ensure_history(self, symbol: str) -> None:
        with self._lock:
            state = self.states[symbol]
            if state.history_loaded and state.history_is_real:
                return
            retry_cooldown = max(float(self._full_scan_cycle_seconds_locked()), 300.0)
            now_mono = time.monotonic()
            if (now_mono - state.last_history_attempt_monotonic) < retry_cooldown:
                return
            state.last_history_attempt_monotonic = now_mono

        candles = self.provider.get_historical_candles(symbol, self.settings.history_lookback_candles)
        min_candles = (self.settings.hyper_ema_slow if self.mode == "hyper" else 21) + 5
        is_real = len(candles) >= min_candles
        if not candles:
            candles = self._fallback_candles(symbol)

        with self._lock:
            state = self.states[symbol]
            state.candles = candles
            state.history_loaded = True
            state.history_is_real = is_real
            if state.quote.ltp:
                upsert_live_candle(
                    state.candles,
                    state.quote.ltp,
                    self._local_now(),
                    self.settings.candle_interval_minutes,
                )
            self._refresh_strategy_locked(state)

    def _fallback_candles(self, symbol: str) -> List[Candle]:
        now = self._local_now()
        bucket_size = self.settings.candle_interval_minutes * 60
        base_price = self.states[symbol].quote.ltp or 100.0
        candles = []
        for index in range(self.settings.history_lookback_candles):
            ts = int(now.timestamp() // bucket_size * bucket_size) - (self.settings.history_lookback_candles - index) * bucket_size
            candles.append(Candle(time=ts, open=base_price, high=base_price, low=base_price, close=base_price))
        return candles

    def _refresh_strategy_locked(self, state: SymbolRuntimeState) -> None:
        if self.mode == "hyper":
            fast_period = self.settings.hyper_ema_fast
            slow_period = self.settings.hyper_ema_slow
        else:
            fast_period = 9
            slow_period = 21

        if len(state.candles) < slow_period + 3:
            return

        closes = [candle.close for candle in state.candles]
        fast = ema_series(closes, fast_period)
        slow = ema_series(closes, slow_period)
        state.ema9 = series_to_points(state.candles, fast)
        state.ema21 = series_to_points(state.candles, slow)

        now_mono = time.monotonic()
        last_scan = self._last_scan_log.get(state.symbol, 0.0)
        if now_mono - last_scan >= 30:
            gap = fast[-1] - slow[-1]
            trend = "▲ bullish" if gap > 0.01 else ("▼ bearish" if gap < -0.01 else "→ flat")
            self._log_thought(
                "scan", state.symbol,
                f"EMA{fast_period} {fast[-1]:.2f} / EMA{slow_period} {slow[-1]:.2f}  gap {gap:+.2f}  {trend}"
            )
            self._last_scan_log[state.symbol] = now_mono

        action = detect_crossover(fast, slow)
        if not action:
            return

        gap_pct = abs(fast[-1] - slow[-1]) / slow[-1] * 100 if slow[-1] > 0 else 0.0
        if gap_pct < 0.1:
            self._log_thought("scan", state.symbol, f"Crossover ignored — gap {gap_pct:.4f}% is below 0.1% threshold (noise).")
            return

        signal_key = f"{action}:{state.candles[-1].time}"
        if signal_key == state.last_signal_key:
            return

        direction = "above" if action == "BUY" else "below"
        reason = f"{fast_period} EMA crossed {direction} {slow_period} EMA on 5-minute candles."
        self._log_thought(
            "signal", state.symbol,
            f"{'🟢' if action == 'BUY' else '🔴'} CROSSOVER → {action}  "
            f"EMA{fast_period} {fast[-1]:.2f} crossed {direction} EMA{slow_period} {slow[-1]:.2f}  "
            f"@ ₹{closes[-1]:.2f}"
        )
        signal = SignalEvent(
            symbol=state.symbol,
            name=state.name,
            action=action,
            price=state.candles[-1].close,
            timestamp=self._local_now().isoformat(),
            reason=reason,
            quantity=0,
            source="rule",
            confidence=1.0,
        )
        state.latest_crossover = signal
        state.last_signal_key = signal_key
        if self.ai_engine.is_enabled():
            return
        state.latest_signal = signal
        self.latest_signal = signal
        self._execute_signal_locked(state, signal)

    def _bot_capital_snapshot_locked(self) -> Dict[str, float]:
        starting_capital = max(0.0, self.settings.bot_starting_capital)
        deployed_capital = 0.0
        market_value = 0.0
        unrealized_pnl = 0.0
        open_positions = 0

        for state in self.states.values():
            if not state.position:
                continue
            open_positions += 1
            cost_basis = state.position.average_price * state.position.quantity
            last_price = state.quote.ltp or state.position.average_price
            current_value = last_price * state.position.quantity
            deployed_capital += cost_basis
            market_value += current_value
            unrealized_pnl += current_value - cost_basis

        free_cash = starting_capital + self.wallet_realized_pnl - deployed_capital
        net_equity = free_cash + market_value
        equity_floor = starting_capital * max(0.0, 1.0 - (self.settings.max_capital_loss_pct / 100.0))
        peak_equity = max(self.peak_bot_equity, net_equity, starting_capital)
        drawdown_pct = ((peak_equity - net_equity) / peak_equity * 100.0) if peak_equity > 0 else 0.0
        return_pct = ((net_equity - starting_capital) / starting_capital * 100.0) if starting_capital > 0 else 0.0
        loss_from_start_pct = (
            ((starting_capital - net_equity) / starting_capital * 100.0) if starting_capital > 0 else 0.0
        )

        return {
            "starting_capital": round(starting_capital, 2),
            "free_cash": round(free_cash, 2),
            "deployed_capital": round(deployed_capital, 2),
            "market_value": round(market_value, 2),
            "realized_pnl": round(self.wallet_realized_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "net_equity": round(net_equity, 2),
            "peak_equity": round(peak_equity, 2),
            "return_pct": round(return_pct, 2),
            "drawdown_pct": round(drawdown_pct, 2),
            "loss_from_start_pct": round(loss_from_start_pct, 2),
            "equity_floor": round(equity_floor, 2),
            "open_positions": open_positions,
        }

    def _broker_account_view_locked(self) -> Dict[str, object]:
        snapshot = self.account_snapshot if isinstance(self.account_snapshot, dict) else {}
        summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
        profile = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}
        active_segments = profile.get("active_segments") if isinstance(profile.get("active_segments"), list) else []
        errors = snapshot.get("errors") if isinstance(snapshot.get("errors"), list) else []

        if getattr(self.provider, "supports_account_data", False):
            note = "Add money in the Groww app or web account. This dashboard only reads the broker balance."
            if errors:
                note = f"{note} Account sync issue: {errors[0]}"
        else:
            note = "Broker balances are available only when Groww live mode is configured."

        return {
            "enabled": getattr(self.provider, "supports_account_data", False),
            "connected": bool(snapshot),
            "status": snapshot.get("status") or ("ok" if snapshot else "unavailable"),
            "updated_at": snapshot.get("updated_at") or "",
            "clear_cash": round(float(summary.get("clear_cash") or 0.0), 2),
            "mis_balance_available": round(float(summary.get("mis_balance_available") or 0.0), 2),
            "cnc_balance_available": round(float(summary.get("cnc_balance_available") or 0.0), 2),
            "net_margin_used": round(float(summary.get("net_margin_used") or 0.0), 2),
            "brokerage_and_charges": round(float(summary.get("brokerage_and_charges") or 0.0), 2),
            "holdings_count": int(summary.get("holdings_count") or 0),
            "holdings_cost_value": round(float(summary.get("holdings_cost_value") or 0.0), 2),
            "holdings_market_value": round(float(summary.get("holdings_market_value") or 0.0), 2),
            "holdings_unrealized_pnl": round(float(summary.get("holdings_unrealized_pnl") or 0.0), 2),
            "holdings_return_pct": round(float(summary.get("holdings_return_pct") or 0.0), 2),
            "priced_holdings_count": int(summary.get("priced_holdings_count") or 0),
            "open_positions_count": int(summary.get("open_positions_count") or 0),
            "positions_realized_pnl": round(float(summary.get("positions_realized_pnl") or 0.0), 2),
            "ucc": profile.get("ucc") or "",
            "active_segments": active_segments,
            "note": note,
        }

    def _broker_buying_power_locked(self) -> float:
        broker = self._broker_account_view_locked()
        return max(
            float(broker.get("mis_balance_available") or 0.0),
            float(broker.get("cnc_balance_available") or 0.0),
            float(broker.get("clear_cash") or 0.0),
        )

    def _max_buy_quantity_locked(self, price: float, desired_quantity: Optional[int] = None) -> int:
        if price <= 0:
            return 0

        caps = [int(self.settings.max_capital_per_trade // price)]
        capital = self._bot_capital_snapshot_locked()
        caps.append(int(max(0.0, capital["free_cash"]) // price))

        if self.mode in ("live", "hyper"):
            broker_buying_power = self._broker_buying_power_locked()
            if broker_buying_power > 0:
                caps.append(int(broker_buying_power // price))

        if desired_quantity and desired_quantity > 0:
            caps.append(int(desired_quantity))

        return max(0, min(caps)) if caps else 0

    def _buy_block_reason_locked(self, symbol: str, price: float) -> str:
        if price > self.settings.max_capital_per_trade:
            return (
                f"{symbol} BUY blocked: one share costs more than the configured "
                f"₹{self.settings.max_capital_per_trade:.0f} cap."
            )

        free_cash = self._bot_capital_snapshot_locked()["free_cash"]
        if free_cash < price:
            return (
                f"{symbol} BUY blocked: the bot wallet has only ₹{free_cash:.2f} free cash left, "
                f"which is below the share price."
            )

        if self.mode in ("live", "hyper"):
            broker_buying_power = self._broker_buying_power_locked()
            if broker_buying_power and broker_buying_power < price:
                return (
                    f"{symbol} BUY blocked: Groww buying power is ₹{broker_buying_power:.2f}, "
                    "so the broker balance cannot fund one share."
                )

        return f"{symbol} BUY blocked: the order resolved to zero quantity under the active wallet and risk limits."

    def _check_stop_loss_locked(self, state: SymbolRuntimeState) -> None:
        if not state.position or not state.quote.ltp:
            return
        stop_price = state.position.average_price * (1.0 - self.settings.stop_loss_pct / 100.0)
        if state.quote.ltp <= stop_price:
            self._log_thought(
                "stop", state.symbol,
                f"🛑 STOP LOSS  {state.symbol} dropped to ₹{state.quote.ltp:.2f}  "
                f"(entry ₹{state.position.average_price:.2f}  floor ₹{stop_price:.2f})"
            )
            signal = SignalEvent(
                symbol=state.symbol,
                name=state.name,
                action="SELL",
                price=state.quote.ltp,
                timestamp=self._local_now().isoformat(),
                reason=(
                    f"Stop loss hit at ₹{state.quote.ltp:.2f} "
                    f"(bought at ₹{state.position.average_price:.2f}, "
                    f"-{self.settings.stop_loss_pct}%)."
                ),
                quantity=state.position.quantity,
                source="stop_loss",
                confidence=1.0,
            )
            state.latest_signal = signal
            self.latest_signal = signal
            self._execute_signal_locked(state, signal)

    def _is_market_open_locked(self) -> bool:
        now = self._local_now()
        if now.weekday() >= 5:
            return False
        market_open  = now.replace(hour=9,  minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_open <= now <= market_close

    def _execute_signal_locked(self, state: SymbolRuntimeState, signal: SignalEvent) -> None:
        if self.bot_halted:
            return

        state_check = self.states.get(signal.symbol)
        if state_check and not state_check.history_is_real:
            self._log_thought("block", signal.symbol, f"No real history yet — {signal.action} signal ignored (dummy candles active).")
            return

        if not self._is_market_open_locked():
            self._log_thought("block", signal.symbol, f"Market closed — {signal.action} signal held, not executed.")
            return

        max_trades = self.settings.hyper_max_trades if self.mode == "hyper" else self.settings.max_trades_per_day
        mode_label = "HYPER" if self.mode == "hyper" else "PAPER"

        if signal.action == "BUY":
            if state.position:
                return
            quantity = self._max_buy_quantity_locked(signal.price, signal.quantity or None)
            if quantity < 1:
                reason = self._buy_block_reason_locked(signal.symbol, signal.price)
                self._push_alert("warning", reason)
                self._log_thought("block", signal.symbol, f"BUY blocked — {reason}")
                return
            if signal.quantity and signal.quantity > quantity:
                self._push_alert(
                    "warning",
                    f"{signal.symbol} BUY quantity was capped to {quantity} shares by the wallet and broker limits.",
                )
            if self.trade_count >= max_trades:
                self._halt_bot_locked("Maximum trades per day reached.")
                return

            if self.mode in ("live", "hyper"):
                outcome = self._place_live_order_locked(state, signal, quantity)
                if not outcome["accepted"]:
                    return
                execution_price = outcome["price"]
                execution_qty = outcome["quantity"]
                state.position = PositionState(
                    symbol=signal.symbol,
                    quantity=execution_qty,
                    average_price=execution_price,
                    opened_at=signal.timestamp,
                )
                self.trade_count += 1
                self.trade_log.appendleft(outcome["trade"])
                self._push_alert(
                    "warning",
                    f"{mode_label} BUY placed for {signal.symbol} at about ₹{execution_price:.2f}. Order ID: {outcome['trade'].trade_id}",
                )
                self._last_account_refresh_monotonic = -float(self.settings.account_refresh_interval_seconds)
            else:
                state.position = PositionState(
                    symbol=signal.symbol,
                    quantity=quantity,
                    average_price=signal.price,
                    opened_at=signal.timestamp,
                )
                self.trade_count += 1
                self.trade_log.appendleft(
                    TradeEvent(
                        trade_id=uuid.uuid4().hex[:10],
                        symbol=signal.symbol,
                        name=signal.name,
                        side="BUY",
                        price=signal.price,
                        quantity=quantity,
                        timestamp=signal.timestamp,
                        pnl=0.0,
                        mode=mode_label,
                        status="OPEN",
                        remark=signal.reason,
                    )
                )
                self._push_alert("info", f"{mode_label} BUY simulated for {signal.symbol} at ₹{signal.price:.2f}.")
                self._log_thought("trade", signal.symbol, f"✅ BUY {quantity} × {signal.symbol} @ ₹{signal.price:.2f}  [{mode_label}]")

        elif signal.action == "SELL":
            if not state.position:
                return
            if self.trade_count >= max_trades:
                self._halt_bot_locked("Maximum trades per day reached.")
                return

            if self.mode in ("live", "hyper"):
                outcome = self._place_live_order_locked(state, signal, state.position.quantity)
                if not outcome["accepted"]:
                    return
                pnl = (outcome["price"] - state.position.average_price) * outcome["quantity"]
                self.realized_pnl += pnl
                self.wallet_realized_pnl += pnl
                self.trade_count += 1
                trade = outcome["trade"]
                trade.pnl = pnl
                self.trade_log.appendleft(trade)
                self._push_alert(
                    "warning",
                    f"{mode_label} SELL placed for {signal.symbol} at about ₹{outcome['price']:.2f}. Order ID: {trade.trade_id}",
                )
                state.position = None
                self._last_account_refresh_monotonic = -float(self.settings.account_refresh_interval_seconds)
            else:
                pnl = (signal.price - state.position.average_price) * state.position.quantity
                self.realized_pnl += pnl
                self.wallet_realized_pnl += pnl
                self.trade_count += 1
                self.trade_log.appendleft(
                    TradeEvent(
                        trade_id=uuid.uuid4().hex[:10],
                        symbol=signal.symbol,
                        name=signal.name,
                        side="SELL",
                        price=signal.price,
                        quantity=state.position.quantity,
                        timestamp=signal.timestamp,
                        pnl=pnl,
                        mode=mode_label,
                        status="CLOSED",
                        remark=signal.reason,
                    )
                )
                pnl_sign = "+" if pnl >= 0 else ""
                self._push_alert("info", f"{mode_label} SELL simulated for {signal.symbol} at ₹{signal.price:.2f}.")
                self._log_thought("trade", signal.symbol, f"{'✅' if pnl >= 0 else '🔴'} SELL {state.position.quantity} × {signal.symbol} @ ₹{signal.price:.2f}  P&L {pnl_sign}₹{pnl:.2f}  [{mode_label}]")
                state.position = None

        self._enforce_risk_limits_locked()
        if self.trade_count >= max_trades:
            self._halt_bot_locked("Maximum trades per day reached.")

    def _place_live_order_locked(self, state: SymbolRuntimeState, signal: SignalEvent, quantity: int) -> Dict[str, object]:
        reference_id = f"braino-{signal.action.lower()}-{uuid.uuid4().hex[:10]}"
        try:
            order = self.provider.place_market_order(
                symbol=signal.symbol,
                quantity=quantity,
                side=signal.action,
                reference_id=reference_id,
            )
        except Exception as exc:
            self._push_alert("error", f"Live {signal.action} failed for {signal.symbol}: {exc}")
            self._halt_bot_locked(f"Live {signal.action} failed for {signal.symbol}. Bot halted.")
            return {"accepted": False}

        status = (order.get("order_status") or "PLACED").upper()
        filled_quantity = int(order.get("filled_quantity") or quantity)
        execution_price = float(order.get("average_fill_price") or signal.price)
        trade = TradeEvent(
            trade_id=order.get("groww_order_id") or reference_id,
            symbol=signal.symbol,
            name=signal.name,
            side=signal.action,
            price=execution_price,
            quantity=filled_quantity,
            timestamp=signal.timestamp,
            pnl=0.0,
            mode="HYPER" if self.mode == "hyper" else "LIVE",
            status=status,
            remark=order.get("remark") or "",
        )

        if status in self.LIVE_FAILED_STATUSES:
            self.trade_log.appendleft(trade)
            self._push_alert("error", f"Live {signal.action} rejected for {signal.symbol}: {trade.remark or status}")
            self._halt_bot_locked(f"Live {signal.action} rejected for {signal.symbol}. Bot halted.")
            return {"accepted": False}

        if status not in self.LIVE_FILLED_STATUSES or filled_quantity < quantity:
            self.trade_log.appendleft(trade)
            self._push_alert(
                "error",
                f"Live {signal.action} for {signal.symbol} is {status} with {filled_quantity}/{quantity} filled. Bot halted for manual review.",
            )
            self._halt_bot_locked(f"Manual review required for live {signal.action} order on {signal.symbol}.")
            return {"accepted": False}

        return {
            "accepted": True,
            "price": execution_price,
            "quantity": filled_quantity,
            "trade": trade,
        }

    def _maybe_run_ai_decision(self) -> None:
        if not self.ai_engine.is_enabled() or self.bot_halted or self._ai_request_in_flight:
            return
        if (time.monotonic() - self._last_ai_decision_monotonic) < self.settings.ai_decision_interval_seconds:
            return

        with self._lock:
            context = self._build_ai_context_locked()
            focus_preview = ", ".join(
                f"{item['symbol']}({item.get('opportunity_score', 0):.1f})"
                for item in context.get("watchlist", [])[:6]
            )
            if focus_preview:
                self._log_thought(
                    "scan",
                    "MARKET",
                    f"AI shortlist from {context.get('scanner', {}).get('eligible_symbols', 0)} eligible names → {focus_preview}",
                )
            self._ai_request_in_flight = True

        try:
            decision = self.ai_engine.decide(context)
        except Exception as exc:
            with self._lock:
                self._ai_request_in_flight = False
                self._last_ai_decision_monotonic = time.monotonic()
                self._push_alert("error", f"AI decision request failed: {exc}")
            return

        with self._lock:
            self._ai_request_in_flight = False
            self._last_ai_decision_monotonic = time.monotonic()
            self._apply_ai_decision_locked(decision)

    def _build_ai_context_locked(self) -> Dict[str, object]:
        capital = self._bot_capital_snapshot_locked()
        broker = self._broker_account_view_locked()
        ranked_candidates = self._rank_market_candidates_locked()
        candidate_limit = max(self.settings.ai_watchlist_limit, self.settings.ai_candidate_pool_size)
        selected_symbols = []
        seen = set()

        for candidate in ranked_candidates:
            state = candidate["state"]
            if len(selected_symbols) >= candidate_limit:
                break
            selected_symbols.append(state)
            seen.add(state.symbol)

        if self.selected_symbol not in seen:
            selected_symbols.append(self.states[self.selected_symbol])
            seen.add(self.selected_symbol)

        for state in self.states.values():
            if state.position and state.symbol not in seen:
                selected_symbols.append(state)
                seen.add(state.symbol)

        watchlist_context = []
        positions_context = []
        metrics_lookup = {item["state"].symbol: item["metrics"] for item in ranked_candidates}
        for state in selected_symbols:
            fast_value = state.ema9[-1]["value"] if state.ema9 else 0.0
            slow_value = state.ema21[-1]["value"] if state.ema21 else 0.0
            max_buy_qty = self._max_buy_quantity_locked(state.quote.ltp)
            metrics = metrics_lookup.get(state.symbol) or {}
            watchlist_context.append(
                {
                    "symbol": state.symbol,
                    "name": state.name,
                    "ltp": round(state.quote.ltp, 2),
                    "open": round(state.quote.open, 2),
                    "high": round(state.quote.high, 2),
                    "low": round(state.quote.low, 2),
                    "volume": round(state.quote.volume, 2),
                    "change_pct": round(state.quote.change_pct, 2),
                    "ema9": round(fast_value, 2),
                    "ema21": round(slow_value, 2),
                    "ema_gap": round(fast_value - slow_value, 2),
                    "latest_crossover": state.latest_crossover.action if state.latest_crossover else "",
                    "max_buy_quantity": max_buy_qty,
                    "opportunity_score": round(float(metrics.get("score") or 0.0), 2),
                    "turnover": round(float(metrics.get("turnover") or 0.0), 2),
                    "ema_gap_pct": round(float(metrics.get("ema_gap_pct") or 0.0), 2),
                    "trend_bias": metrics.get("trend_bias") or "",
                    "freshness_seconds": round(float(metrics.get("freshness_seconds") or 0.0), 2),
                    "rank_reason": metrics.get("rank_reason") or "",
                }
            )
            if state.position:
                positions_context.append(
                    {
                        "symbol": state.symbol,
                        "name": state.name,
                        "quantity": state.position.quantity,
                        "average_price": round(state.position.average_price, 2),
                        "ltp": round(state.quote.ltp, 2),
                        "unrealized_pnl": round(
                            (state.quote.ltp - state.position.average_price) * state.position.quantity,
                            2,
                        ),
                    }
                )

        return {
            "as_of": self._local_now().isoformat(),
            "selected_symbol": self.selected_symbol,
            "watchlist": watchlist_context,
            "positions": positions_context,
            "scanner": self._scanner_status_locked(ranked_candidates),
            "capital": capital,
            "broker": {
                "clear_cash": broker["clear_cash"],
                "mis_balance_available": broker["mis_balance_available"],
                "cnc_balance_available": broker["cnc_balance_available"],
                "holdings_count": broker["holdings_count"],
                "open_positions_count": broker["open_positions_count"],
                "positions_realized_pnl": broker["positions_realized_pnl"],
            },
            "limits": {
                "max_capital_per_trade": self.settings.max_capital_per_trade,
                "max_loss_per_day": self.settings.max_loss_per_day,
                "max_trades_per_day": self.settings.max_trades_per_day,
                "max_capital_loss_pct": self.settings.max_capital_loss_pct,
            },
            "session": {
                "trade_count": self.trade_count,
                "realized_pnl": round(self.realized_pnl, 2),
                "session_pnl": round(self._session_pnl_locked(), 2),
                "bot_halted": self.bot_halted,
                "bot_stop_reason": self.stop_reason,
                "mode": self.mode,
            },
        }

    def _apply_ai_decision_locked(self, decision: SignalEvent) -> None:
        state = self.states.get(decision.symbol) or self.states[self.selected_symbol]
        state.latest_signal = decision
        self.latest_signal = decision

        if decision.action == "HOLD":
            return

        self._execute_signal_locked(state, decision)

    def _enforce_risk_limits_locked(self) -> None:
        capital = self._bot_capital_snapshot_locked()
        self.peak_bot_equity = max(self.peak_bot_equity, capital["net_equity"])
        if capital["net_equity"] <= capital["equity_floor"]:
            self._halt_bot_locked(
                "Capital protection stop reached. "
                f"Bot equity fell to ₹{capital['net_equity']:.2f}, below the floor of ₹{capital['equity_floor']:.2f}."
            )
            return
        if self._session_pnl_locked() <= -abs(self.settings.max_loss_per_day):
            self._halt_bot_locked("Maximum daily loss reached.")

    def _session_pnl_locked(self) -> float:
        unrealized = 0.0
        for state in self.states.values():
            if state.position:
                unrealized += (state.quote.ltp - state.position.average_price) * state.position.quantity
        return self.realized_pnl + unrealized

    def _halt_bot_locked(self, reason: str) -> None:
        if self.bot_halted and self.stop_reason == reason:
            return
        self.bot_halted = True
        self.stop_reason = reason
        self._push_alert("error", reason)

    def _refresh_account_snapshot(self, force: bool = False) -> Dict[str, object]:
        if not getattr(self.provider, "supports_account_data", False):
            return {}
        if (
            not force
            and (time.monotonic() - self._last_account_refresh_monotonic)
            < self.settings.account_refresh_interval_seconds
        ):
            return {}

        snapshot = self.provider.get_account_snapshot() or {}
        self._last_account_refresh_monotonic = time.monotonic()
        if snapshot:
            with self._lock:
                self.account_snapshot = snapshot
        return snapshot

    def _refresh_account_snapshot_if_due(self) -> Dict[str, object]:
        return self._refresh_account_snapshot(force=False)

    def _reset_daily_if_needed_locked(self, current_day) -> None:
        if current_day == self.session_day:
            return
        self.session_day = current_day
        self.trade_count = 0
        self.realized_pnl = 0.0
        self.bot_halted = False
        self.stop_reason = ""
        self._push_alert("info", "Daily risk counters reset for the new session.")

    def _start_scheduler(self) -> None:
        if not BackgroundScheduler:
            self._push_alert("warning", "APScheduler is not installed, so daily token refresh is disabled.")
            return

        self._scheduler = BackgroundScheduler(timezone=self.settings.timezone)
        self._scheduler.add_job(
            self._run_token_refresh,
            "cron",
            hour=self.settings.token_refresh_hour,
            minute=self.settings.token_refresh_minute,
        )
        self._scheduler.start()

    def _run_token_refresh(self) -> None:
        success, message = self.provider.refresh_access_token()
        self._push_alert("info" if success else "warning", message)

    def _log_thought(self, level: str, symbol: str, message: str) -> None:
        self.thinking_log.appendleft(
            {
                "level": level,
                "symbol": symbol,
                "message": message,
                "timestamp": self._local_now().isoformat(),
            }
        )

    def _push_alert(self, level: str, message: str) -> None:
        with self._lock:
            self.alerts.appendleft(
                {
                    "level": level,
                    "message": message,
                    "timestamp": self._local_now().isoformat(),
                }
            )

    def _sync_provider_status(self) -> None:
        message = getattr(self.provider, "status_message", "") or f"Using {self.provider.label}."
        if message == self._last_provider_status:
            return
        level = "error" if getattr(self.provider, "error_code", "") else "info"
        self._push_alert(level, message)
        self._last_provider_status = message

    def _local_now(self) -> datetime:
        return datetime.now(self.tz)
