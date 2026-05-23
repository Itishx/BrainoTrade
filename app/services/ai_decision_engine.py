from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests

from .models import SignalEvent


class OpenAIDecisionEngine:
    def __init__(self, settings):
        self.settings = settings
        self.enabled = settings.ai_decision_enabled and bool(settings.openai_api_key)
        self.status_message = ""
        if not settings.ai_decision_enabled:
            self.status_message = "AI decision engine is disabled in .env."
        elif not settings.openai_api_key:
            self.status_message = "AI decision engine is enabled but OPENAI_API_KEY is missing."
        else:
            self.status_message = f"AI decision engine ready with {settings.openai_model}."

    def is_enabled(self) -> bool:
        return self.enabled

    def decide(self, context: Dict[str, Any]) -> SignalEvent:
        if not self.enabled:
            raise RuntimeError(self.status_message or "AI decision engine is not enabled.")

        payload = self._build_payload(context)
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=45,
        )
        response.raise_for_status()
        data = response.json()
        content = self._extract_message_content(data)
        parsed = json.loads(content)
        return self._to_signal(parsed, context)

    def _build_payload(self, context: Dict[str, Any]) -> Dict[str, Any]:
        schema = {
            "name": "trade_decision",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["BUY", "SELL", "HOLD"],
                    },
                    "symbol": {"type": "string"},
                    "quantity": {"type": "integer", "minimum": 0},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                    "risk_note": {"type": "string"},
                },
                "required": ["action", "symbol", "quantity", "confidence", "reason", "risk_note"],
                "additionalProperties": False,
            },
        }

        system_prompt = """
You are BrainoTrade's portfolio decision engine for Indian cash equities.
You are deciding one action for the current portfolio snapshot: BUY, SELL, or HOLD.

Rules:
- Prefer HOLD when the edge is weak or unclear.
- You may BUY only symbols from the supplied watchlist candidates.
- You may SELL only symbols that are currently held.
- The watchlist is pre-filtered for tradability and liquidity, then ranked by opportunity_score.
- Prefer higher opportunity_score candidates when they also have strong trend_bias, turnover, and fresh data.
- Respect the supplied quantity constraints and risk limits.
- Do not suggest averaging down after the daily loss limit is near breach.
- Return exactly one JSON object matching the schema.
- Keep reasons concise and concrete, using market context, momentum, EMA alignment, change, and risk.
""".strip()

        user_prompt = json.dumps(context, separators=(",", ":"), ensure_ascii=True)

        return {
            "model": self.settings.openai_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": schema,
            },
        }

    def _extract_message_content(self, data: Dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("OpenAI returned no choices.")

        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: List[str] = []
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        text_parts.append(text)
            if text_parts:
                return "".join(text_parts)
        raise RuntimeError("OpenAI returned an unexpected message format.")

    def _to_signal(self, parsed: Dict[str, Any], context: Dict[str, Any]) -> SignalEvent:
        action = (parsed.get("action") or "HOLD").upper()
        symbol = (parsed.get("symbol") or "").upper()
        quantity = int(parsed.get("quantity") or 0)
        confidence = float(parsed.get("confidence") or 0.0)
        reason = (parsed.get("reason") or "").strip()
        risk_note = (parsed.get("risk_note") or "").strip()

        watch_lookup = {item["symbol"]: item for item in context.get("watchlist", [])}
        position_lookup = {item["symbol"]: item for item in context.get("positions", [])}

        if action == "BUY" and symbol not in watch_lookup:
            raise RuntimeError(f"OpenAI selected unsupported BUY symbol: {symbol}")
        if action == "SELL" and symbol not in position_lookup:
            raise RuntimeError(f"OpenAI selected unsupported SELL symbol: {symbol}")
        if action == "HOLD":
            symbol = symbol if symbol in watch_lookup else context.get("selected_symbol", "")
            quantity = 0

        if action == "BUY":
            max_qty = int((watch_lookup[symbol].get("max_buy_quantity") or 0))
            quantity = max(0, min(quantity, max_qty))
            if quantity < 1:
                action = "HOLD"
                quantity = 0
                symbol = context.get("selected_symbol", symbol)
                reason = reason or "AI had no executable BUY size within the capital cap."
        elif action == "SELL":
            held_qty = int(position_lookup[symbol].get("quantity") or 0)
            quantity = max(0, min(quantity or held_qty, held_qty))
            if quantity < 1:
                action = "HOLD"
                quantity = 0
                symbol = context.get("selected_symbol", symbol)
                reason = reason or "AI had no position size available to sell."

        symbol_quote = watch_lookup.get(symbol) or {}
        price = float(symbol_quote.get("ltp") or symbol_quote.get("close") or 0.0)
        name = symbol_quote.get("name") or position_lookup.get(symbol, {}).get("name") or symbol or "Portfolio"
        combined_reason = reason if not risk_note else f"{reason} Risk: {risk_note}".strip()

        return SignalEvent(
            symbol=symbol or context.get("selected_symbol", ""),
            name=name,
            action=action,
            price=price,
            timestamp=context["as_of"],
            reason=combined_reason or "No additional reasoning returned.",
            quantity=quantity,
            source="ai",
            confidence=confidence,
        )
