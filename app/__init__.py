from __future__ import annotations

import atexit
import json
import os

import requests as _http
from flask import Flask, jsonify, render_template, request

from .config import load_settings
from .services.market_provider import build_market_provider
from .services.trading_engine import TradingEngine

_COMPANY_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "company_cache.json")


def _load_company_cache() -> dict:
    try:
        with open(_COMPANY_CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_company_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(_COMPANY_CACHE_PATH), exist_ok=True)
    with open(_COMPANY_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)


def _fetch_company_info_from_openai(symbol: str, name: str, api_key: str, model: str) -> dict:
    prompt = (
        f'Provide factual information about the Indian company listed on NSE with trading symbol "{symbol}" '
        f'and company name "{name}". Return a JSON object with exactly these keys:\n'
        '"description": 2-3 sentence summary of what the company does and its main business\n'
        '"sector": the industry sector (e.g. "Energy", "Banking", "FMCG", "IT Services")\n'
        '"ceo": full name of the current CEO or Managing Director\n'
        '"founded": year the company was founded (just the year as a string)\n'
        '"headquarters": city where the headquarters is located (just the city name)\n'
        '"key_fact": one impressive fact about the company — market position, scale, or revenue\n\n'
        'Only include information you are confident is accurate. Use "—" for any field you are not sure about. '
        "Return only valid JSON, no markdown, no extra text."
    )
    resp = _http.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 350,
            "temperature": 0.1,
        },
        timeout=25,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"].strip()
    # Strip markdown code fences OpenAI sometimes adds despite instructions
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    text = text.strip()
    return json.loads(text)


def create_app() -> Flask:
    settings = load_settings()
    provider = build_market_provider(settings)
    engine = TradingEngine(settings, provider)
    engine.start()

    if settings.watchlist_source == "groww_nse_all":
        watchlist_heading = "NSE Cash Universe"
        watchlist_note = "Rotating scan across all Groww tradable NSE cash stocks"
    elif settings.watchlist_source == "custom":
        watchlist_heading = "Custom Watchlist"
        watchlist_note = "Symbols loaded from WATCHLIST_SYMBOLS"
    else:
        watchlist_heading = "Nifty 50 Watchlist"
        watchlist_note = "Polling every few seconds"

    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.secret_key
    app.extensions["trading_engine"] = engine
    atexit.register(engine.shutdown)

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            default_symbol=settings.default_symbol,
            refresh_interval=settings.poll_interval_seconds,
            provider_name=provider.label,
            watchlist_heading=watchlist_heading,
            watchlist_note=watchlist_note,
        )

    @app.route("/guide")
    def guide():
        return render_template("landing.html")

    @app.get("/api/dashboard")
    def dashboard():
        symbol = request.args.get("symbol", settings.default_symbol)
        return jsonify(engine.get_dashboard_state(symbol))

    @app.post("/api/mode")
    def set_mode():
        payload = request.get_json(silent=True) or {}
        success, message = engine.set_mode(payload.get("mode", "paper"))
        status_code = 200 if success else 400
        return jsonify({"ok": success, "message": message, "mode": engine.mode}), status_code

    @app.get("/api/company-info")
    def company_info():
        symbol = request.args.get("symbol", "").strip().upper()
        name = request.args.get("name", symbol).strip()
        if not symbol:
            return jsonify({"error": "symbol required"}), 400

        cache = _load_company_cache()
        if symbol in cache:
            return jsonify(cache[symbol])

        if not settings.openai_api_key:
            return jsonify({
                "symbol": symbol, "name": name,
                "description": "AI is not configured — add OPENAI_API_KEY to .env to enable company info.",
                "sector": "—", "ceo": "—", "founded": "—", "headquarters": "—", "key_fact": "—",
            })

        try:
            info = _fetch_company_info_from_openai(symbol, name, settings.openai_api_key, settings.openai_model)
            info.setdefault("symbol", symbol)
            info.setdefault("name", name)
            cache[symbol] = info
            _save_company_cache(cache)
            return jsonify(info)
        except Exception as exc:
            return jsonify({
                "symbol": symbol, "name": name,
                "description": f"Could not load company info: {exc}",
                "sector": "—", "ceo": "—", "founded": "—", "headquarters": "—", "key_fact": "—",
            })

    return app
