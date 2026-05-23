# BrainoTrade

A Flask-based real-time trading dashboard scaffold for Groww market data with:

- Nifty 50 or full Groww NSE cash universe scanning
- Live candlestick chart with EMA 9 / EMA 21 overlays
- Paper or live signal execution with trade logging
- Hardcoded risk controls
- Groww broker balance sync plus a separate bot wallet ledger
- Daily token refresh hook at 6:30 AM
- Optional OpenAI decision layer for `BUY / HOLD / SELL`

## What this build does

- Uses Groww live data if `growwapi` is installed and valid credentials are present.
- Falls back to a demo market simulator when Groww is not configured.
- Runs the EMA crossover strategy on 5-minute candles.
- Simulates orders in paper mode or places live Groww `MIS` market orders in live mode.
- Can hand portfolio decisions to an OpenAI model when `AI_DECISION_ENABLED=true` and `OPENAI_API_KEY` is configured.
- Tracks bot-level capital allocation with free cash, deployed capital, equity, return, and an 80% capital-protection stop floor.
- Pulls Groww account finances such as clear cash, MIS/CNC buying power, holdings count, and open position count when live credentials are configured.

## Important safety note

Real order placement is supported when `ALLOW_LIVE_TRADING=true` in `.env` and you switch the dashboard to `Live`. The bot sends Groww `MARKET` `MIS` orders for EMA crossover signals and halts itself if an order is rejected, partially filled, or left in a non-final state.

## Setup

1. Create a virtual environment.
2. Install dependencies.
3. Copy `.env.example` to `.env`.
4. Add your Groww credentials to `.env`.
5. Run the Flask app.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 app.py
```

Open `http://127.0.0.1:5000`.

## Environment variables

- `GROWW_API_ACCESS_TOKEN`: Use this if you only want live data with an existing access token.
- `GROWW_API_KEY` and `GROWW_API_SECRET`: Add these if you want the 6:30 AM refresh job to request a fresh access token automatically.
- `USE_DEMO_DATA=true`: Force the synthetic market simulator even if Groww is configured.
- `ALLOW_LIVE_TRADING=true`: Allows the dashboard `Live` toggle to place real Groww `MIS` market orders.
- `BOT_STARTING_CAPITAL`: The rupee amount the bot is allowed to allocate from your broker balance.
- `MAX_CAPITAL_LOSS_PCT`: Stops the bot if net bot equity falls below this percentage loss from the starting capital. `80` means a `₹1000` wallet halts at `₹200`.
- `ACCOUNT_REFRESH_INTERVAL_SECONDS`: How often the dashboard refreshes the Groww finance snapshot.
- `WATCHLIST_SOURCE`: `nifty50` or `groww_nse_all`. The latter expands the universe to all tradable Groww NSE cash stocks.
- `MARKET_SCAN_BATCH_SIZE`: How many symbols the rotating scanner refreshes per cycle when using a large universe.
- `WATCHLIST_PANEL_LIMIT`: How many symbols are shown in the visible watchlist panel.
- `AI_CANDIDATE_POOL_SIZE`: How many pre-ranked opportunities are passed into the AI decision context.
- `QUOTE_HYDRATION_BATCH_SIZE`: How many scanned symbols get a full quote refresh each cycle so open/high/low/volume stay usable.
- `HISTORY_HYDRATION_BATCH_SIZE`: How many additional symbols per cycle get 5-minute candle history loaded so they can graduate into the EMA ranker.
- `QUOTE_DETAIL_MAX_AGE_SECONDS`: How stale a full quote can get before the engine refreshes that symbol's quote details again.
- `MARKET_MIN_PRICE`: Minimum last traded price for a stock to be considered in the ranked opportunity pool.
- `MARKET_MIN_TURNOVER`: Minimum live notional turnover used to filter out illiquid names from the ranked opportunity pool.
- `AI_DECISION_ENABLED=true`: Turns on the OpenAI decision engine.
- `OPENAI_API_KEY`: Required for the OpenAI decision engine.
- `OPENAI_MODEL`: Defaults to `gpt-5.4-mini`.

## Notes on token refresh

The scheduled refresh job is wired to run every day at 6:30 AM in `Asia/Kolkata` by default. If you only provide an access token, the app cannot mint a new one on its own. For automated refresh, add the Groww API key and secret flow credentials to `.env`.

## Money flow

- Add actual funds in the Groww app or web account. This project does not custody money itself.
- The dashboard reads the reflected broker balance from Groww and shows it in the `Groww account` panel.
- The bot trades only from the app-level `BOT_STARTING_CAPITAL` wallet, so you can keep a separate automated allocation even if the broker account holds more cash.

## Universe modes

- `WATCHLIST_SOURCE=nifty50` keeps the old bundled Nifty 50 behavior.
- `WATCHLIST_SOURCE=groww_nse_all` loads the full Groww tradable NSE cash universe, then rotates through it in batches for live scanning.
- `WATCHLIST_SYMBOLS` still overrides both modes when you want a custom symbol list.
- In `groww_nse_all` mode, the visible watchlist and AI both use a ranked opportunity pool, not the raw scan order.
- The engine now separates `warmed` price-only symbols from `hydrated` quote-detail symbols and `chart-ready` history-backed symbols.
- The ranker favors liquid, fresh names with stronger movement and EMA alignment, and the scanner keeps the top focus symbols fresher while still rotating through the broader universe.

## AI decision mode

When enabled, the app sends a compact portfolio snapshot to OpenAI and expects a strict JSON decision with one action: `BUY`, `SELL`, or `HOLD`. The model can choose one symbol and a quantity, but the bot still enforces your hard risk limits before sending any Groww order.

## Project structure

```text
app/
  data/nifty50.py
  services/
    indicators.py
    market_provider.py
    models.py
    trading_engine.py
  static/
    css/styles.css
    js/dashboard.js
  templates/index.html
app.py
requirements.txt
```
