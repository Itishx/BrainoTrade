from __future__ import annotations

import atexit

from flask import Flask, jsonify, render_template, request

from .config import load_settings
from .services.market_provider import build_market_provider
from .services.trading_engine import TradingEngine


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

    return app
