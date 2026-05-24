const body = document.body;
const refreshIntervalMs = Number(body.dataset.refreshInterval || 5) * 1000;

const state = {
  selectedSymbol: body.dataset.defaultSymbol,
  chartInitialized: false,
  isFetching: false,
  companyInfoSymbol: null,
  isRunning: false,
  intervalId: null,
};

const elements = {
  providerName: document.getElementById("provider-name"),
  asOf: document.getElementById("as-of"),
  botStatus: document.getElementById("bot-status"),
  alertBanner: document.getElementById("alert-banner"),
  flowSummary: document.getElementById("flow-summary"),
  flowUniverseCard: document.getElementById("flow-card-universe"),
  flowUniverseStatus: document.getElementById("flow-universe-status"),
  flowUniverseMeta: document.getElementById("flow-universe-meta"),
  flowScannerCard: document.getElementById("flow-card-scanner"),
  flowScannerStatus: document.getElementById("flow-scanner-status"),
  flowScannerMeta: document.getElementById("flow-scanner-meta"),
  flowRankerCard: document.getElementById("flow-card-ranker"),
  flowRankerStatus: document.getElementById("flow-ranker-status"),
  flowRankerMeta: document.getElementById("flow-ranker-meta"),
  flowDecisionCard: document.getElementById("flow-card-decision"),
  flowDecisionStatus: document.getElementById("flow-decision-status"),
  flowDecisionMeta: document.getElementById("flow-decision-meta"),
  flowRiskCard: document.getElementById("flow-card-risk"),
  flowRiskStatus: document.getElementById("flow-risk-status"),
  flowRiskMeta: document.getElementById("flow-risk-meta"),
  flowExecutionCard: document.getElementById("flow-card-execution"),
  flowExecutionStatus: document.getElementById("flow-execution-status"),
  flowExecutionMeta: document.getElementById("flow-execution-meta"),
  watchlist: document.getElementById("watchlist-list"),
  watchlistNote: document.getElementById("watchlist-note"),
  positionsList: document.getElementById("positions-list"),
  positionsCount: document.getElementById("positions-count"),
  selectedHeading: document.getElementById("selected-heading"),
  heroLtp: document.getElementById("hero-ltp"),
  heroOpen: document.getElementById("hero-open"),
  heroOpenStrip: document.getElementById("hero-open-strip"),
  heroChange: document.getElementById("hero-change"),
  statHigh: document.getElementById("stat-high"),
  statLow: document.getElementById("stat-low"),
  statVolume: document.getElementById("stat-volume"),
  statBook: document.getElementById("stat-book"),
  signalEmpty: document.getElementById("signal-empty"),
  signalBody: document.getElementById("signal-body"),
  signalAction: document.getElementById("signal-action"),
  signalSymbol: document.getElementById("signal-symbol"),
  signalPrice: document.getElementById("signal-price"),
  signalTime: document.getElementById("signal-time"),
  signalReason: document.getElementById("signal-reason"),
  capitalHeading: document.getElementById("capital-heading"),
  capitalStatus: document.getElementById("capital-status"),
  capitalFreeCash: document.getElementById("capital-free-cash"),
  capitalDeployed: document.getElementById("capital-deployed"),
  capitalEquity: document.getElementById("capital-equity"),
  capitalReturn: document.getElementById("capital-return"),
  capitalUnrealized: document.getElementById("capital-unrealized"),
  capitalDrawdown: document.getElementById("capital-drawdown"),
  capitalFloor: document.getElementById("capital-floor"),
  capitalOpenPositions: document.getElementById("capital-open-positions"),
  brokerHeading: document.getElementById("broker-heading"),
  brokerUpdated: document.getElementById("broker-updated"),
  brokerAvailCash: document.getElementById("broker-avail-cash"),
  brokerClearCash: document.getElementById("broker-clear-cash"),
  brokerMis: document.getElementById("broker-mis"),
  brokerCnc: document.getElementById("broker-cnc"),
  brokerMarginUsed: document.getElementById("broker-margin-used"),
  brokerHoldingsCount: document.getElementById("broker-holdings-count"),
  brokerHoldingsCost: document.getElementById("broker-holdings-cost"),
  brokerHoldingsCurrent: document.getElementById("broker-holdings-current"),
  brokerHoldingsPnl: document.getElementById("broker-holdings-pnl"),
  brokerHoldingsReturn: document.getElementById("broker-holdings-return"),
  brokerOpenPositions: document.getElementById("broker-open-positions"),
  brokerRealizedPnl: document.getElementById("broker-realized-pnl"),
  brokerNote: document.getElementById("broker-note"),
  limitCapital: document.getElementById("limit-capital"),
  limitStopLoss: document.getElementById("limit-stop-loss"),
  limitLoss: document.getElementById("limit-loss"),
  sessionTrades: document.getElementById("session-trades"),
  sessionPnl: document.getElementById("session-pnl"),
  thinkingLog: document.getElementById("thinking-log"),
  thinkingCount: document.getElementById("thinking-count"),
  tradeLogBody: document.getElementById("trade-log-body"),
  chart: document.getElementById("chart"),
  modeButtons: Array.from(document.querySelectorAll(".mode-button")),
  signalScanStatus: document.getElementById("signal-scan-status"),
};

const currencyFormatter = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

const numberFormatter = new Intl.NumberFormat("en-IN", {
  maximumFractionDigits: 2,
});

const compactFormatter = new Intl.NumberFormat("en-IN", {
  notation: "compact",
  maximumFractionDigits: 1,
});

function formatCurrency(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return currencyFormatter.format(Number(value));
}

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return numberFormatter.format(Number(value));
}

function formatCompact(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return compactFormatter.format(Number(value));
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  const numeric = Number(value);
  const sign = numeric > 0 ? "+" : "";
  return `${sign}${numeric.toFixed(2)}%`;
}

function sentimentClass(value) {
  const numeric = Number(value || 0);
  if (numeric > 0) return "positive";
  if (numeric < 0) return "negative";
  return "neutral";
}

function timeAgo(isoString) {
  const seconds = Math.floor((Date.now() - new Date(isoString)) / 1000);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

function setFlowCardTone(card, tone) {
  if (!card) return;
  card.classList.remove("flow-card-live", "flow-card-warm", "flow-card-alert");
  if (tone) {
    card.classList.add(`flow-card-${tone}`);
  }
}

function setAlert(alert) {
  if (!alert) {
    elements.alertBanner.className = "alert-banner hidden";
    elements.alertBanner.textContent = "";
    return;
  }

  elements.alertBanner.className = `alert-banner ${alert.level || "info"}`;
  elements.alertBanner.textContent = `${alert.message} (${new Date(alert.timestamp).toLocaleTimeString()})`;
}

function renderMode(mode) {
  elements.modeButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
}

function renderWatchlist(items) {
  elements.watchlist.innerHTML = "";
  items.forEach((item) => {
    const score = item.opportunity_score !== undefined && item.opportunity_score !== null
      ? Number(item.opportunity_score).toFixed(1)
      : "";
    const scoreBadge = score
      ? `<span class="watch-chip watch-chip-score">Score ${score}</span>`
      : `<span class="watch-chip watch-chip-muted">Warming</span>`;
    const trendBias = item.trend_bias ? String(item.trend_bias).toLowerCase() : "";
    const trendBadge = trendBias
      ? `<span class="watch-chip watch-chip-bias ${trendBias}">${trendBias}</span>`
      : "";
    const button = document.createElement("button");
    button.type = "button";
    button.className = `watchlist-item ${item.symbol === state.selectedSymbol ? "active" : ""}`;
    const barWidth = Math.min(Math.abs(item.change_pct || 0) * 10, 100);
    const barClass = (item.change_pct || 0) >= 0 ? "positive" : "negative";
    button.innerHTML = `
      <div class="watchlist-top">
        <div class="symbol-stack">
          <span class="symbol-name">${item.name}</span>
          <span class="symbol-code">${item.symbol}</span>
        </div>
        <span class="watch-price">${formatCurrency(item.ltp)}</span>
      </div>
      <div class="watchlist-bottom">
        <div class="watchlist-signal-strip">
          <span class="watch-change ${sentimentClass(item.change_pct)}">${formatPercent(item.change_pct)}</span>
          ${scoreBadge}
          ${trendBadge}
        </div>
      </div>
      <div class="watch-change-bar-wrap">
        <div class="watch-change-bar ${barClass}" style="width:${barWidth}%"></div>
      </div>
    `;
    button.addEventListener("click", () => {
      state.selectedSymbol = item.symbol;
      fetchDashboard();
    });
    elements.watchlist.appendChild(button);
  });
}

function renderPositions(positions) {
  if (!elements.positionsList || !elements.positionsCount) {
    return;
  }

  elements.positionsCount.textContent = positions.length
    ? `${positions.length} active ${positions.length === 1 ? "position" : "positions"}`
    : "No open positions";

  if (!positions.length) {
    elements.positionsList.innerHTML = `<div class="positions-empty">No open positions yet. Bought stocks will stay under active watch here.</div>`;
    return;
  }

  elements.positionsList.innerHTML = "";
  positions.forEach((position) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `position-row ${position.symbol === state.selectedSymbol ? "active" : ""}`;
    const signalTag = position.latest_signal || position.latest_crossover || "HOLD";
    button.innerHTML = `
      <div class="position-row-top">
        <div class="position-symbol-stack">
          <span class="position-symbol">${position.symbol}</span>
          <span class="position-name">${position.name}</span>
        </div>
        <span class="position-pnl ${sentimentClass(position.unrealized_pnl)}">${formatCurrency(position.unrealized_pnl)}</span>
      </div>
      <div class="position-grid">
        <div><span class="position-label">Qty</span><span class="position-value">${formatNumber(position.quantity)}</span></div>
        <div><span class="position-label">Entry</span><span class="position-value">${formatCurrency(position.average_price)}</span></div>
        <div><span class="position-label">LTP</span><span class="position-value">${formatCurrency(position.ltp)}</span></div>
        <div><span class="position-label">Stop</span><span class="position-value">${formatCurrency(position.stop_price)}</span></div>
      </div>
      <div class="position-row-bottom">
        <div class="position-chip-strip">
          <span class="watch-chip watch-chip-score">${formatPercent(position.return_pct)}</span>
          <span class="watch-chip watch-chip-bias ${String(position.trend_bias || "mixed").toLowerCase()}">${position.trend_bias || "mixed"}</span>
          <span class="watch-chip watch-chip-muted">${signalTag}</span>
        </div>
        <span class="position-market-value">${formatCurrency(position.market_value)}</span>
      </div>
    `;
    button.addEventListener("click", () => {
      state.selectedSymbol = position.symbol;
      fetchDashboard();
    });
    elements.positionsList.appendChild(button);
  });
}

function renderScanner(scanner) {
  if (!scanner) return;

  const total    = scanner.total_symbols || 0;
  const warmed   = scanner.warmed_symbols || 0;
  const eligible = scanner.eligible_symbols || 0;

  if (elements.signalScanStatus) {
    elements.signalScanStatus.textContent = total > 0
      ? `Scanning ${formatNumber(total)} symbols · ${formatNumber(eligible)} eligible`
      : "Scanning…";
  }

  // ── Scanner status bar ─────────────────────────────────────────
  const scBatch    = document.getElementById("sc-batch");
  const scTotal    = document.getElementById("sc-total");
  const scWarmed   = document.getElementById("sc-warmed");
  const scHydrated = document.getElementById("sc-hydrated");
  const scHistory  = document.getElementById("sc-history");
  const scEligible = document.getElementById("sc-eligible");
  const scCycle    = document.getElementById("sc-cycle");
  const scFocus    = document.getElementById("sc-focus");

  if (scBatch)    scBatch.textContent    = formatNumber(scanner.scan_batch_size || 0);
  if (scTotal)    scTotal.textContent    = formatNumber(total);
  if (scWarmed)   scWarmed.textContent   = formatNumber(warmed);
  if (scHydrated) scHydrated.textContent = formatNumber(scanner.hydrated_symbols || 0);
  if (scHistory)  scHistory.textContent  = formatNumber(scanner.real_history_symbols || 0);
  if (scEligible) scEligible.textContent = formatNumber(eligible);
  if (scCycle) {
    const mins = scanner.full_cycle_seconds ? (scanner.full_cycle_seconds / 60).toFixed(1) : "0.0";
    scCycle.textContent = mins;
  }
  if (scFocus) {
    const syms = Array.isArray(scanner.focus_symbols) ? scanner.focus_symbols : [];
    scFocus.innerHTML = syms.length
      ? syms.map(s => `<span class="scanner-chip">${s}</span>`).join("")
      : `<span style="color:var(--ink-muted)">—</span>`;
  }

  // ── Watchlist note (simplified) ────────────────────────────────
  if (!elements.watchlistNote) return;
  if (scanner.source === "custom") {
    elements.watchlistNote.textContent = `${formatNumber(total)} custom symbols`;
  } else if (scanner.source === "groww_nse_all") {
    elements.watchlistNote.textContent = `${formatNumber(total)} NSE cash stocks`;
  } else {
    elements.watchlistNote.textContent = "Polling every few seconds";
  }
}

function renderSystemFlow(data) {
  const scanner = data.scanner || {};
  const provider = data.provider || {};
  const decisionEngine = data.decision_engine || {};
  const limits = data.limits || {};
  const session = data.session || {};
  const capital = data.capital || {};
  const latestSignal = data.latest_signal || null;
  const focusSymbols = Array.isArray(scanner.focus_symbols) ? scanner.focus_symbols : [];
  const cycleMinutes = scanner.full_cycle_seconds
    ? (Number(scanner.full_cycle_seconds) / 60).toFixed(1)
    : "0.0";
  const summaryMode = decisionEngine.enabled ? "AI" : "EMA";
  const tradeMode = String(data.mode || "paper").toUpperCase();

  if (elements.flowSummary) {
    elements.flowSummary.textContent =
      `Universe -> Scanner -> Ranker -> ${summaryMode} -> Risk Check -> ${tradeMode} ${tradeMode === "PAPER" ? "Simulation" : "Execution"} -> P&L`;
  }

  if (scanner.source === "groww_nse_all") {
    elements.flowUniverseStatus.textContent = `${formatNumber(scanner.total_symbols)} tradable NSE cash stocks loaded from Groww`;
    elements.flowUniverseMeta.textContent = `Provider: ${provider.name || "Broker"} • rotating live universe`;
  } else if (scanner.source === "custom") {
    elements.flowUniverseStatus.textContent = `${formatNumber(scanner.total_symbols)} custom symbols loaded`;
    elements.flowUniverseMeta.textContent = `Provider: ${provider.name || "Broker"} • custom watchlist mode`;
  } else {
    elements.flowUniverseStatus.textContent = `${formatNumber(scanner.total_symbols)} Nifty 50 names loaded`;
    elements.flowUniverseMeta.textContent = `Provider: ${provider.name || "Broker"} • core watchlist mode`;
  }
  setFlowCardTone(elements.flowUniverseCard, "live");

  elements.flowScannerStatus.textContent =
    `${formatNumber(scanner.warmed_symbols || 0)} warmed • ${formatNumber(scanner.hydrated_symbols || 0)} hydrated • ` +
    `${formatNumber(scanner.real_history_symbols || 0)} chart-ready`;
  elements.flowScannerMeta.textContent =
    `${formatNumber(scanner.scan_batch_size || 0)} symbols per cycle • ~${cycleMinutes} min/full pass`;
  setFlowCardTone(
    elements.flowScannerCard,
    (scanner.hydrated_symbols || 0) > 0 ? "live" : (scanner.warmed_symbols || 0) > 0 ? "warm" : ""
  );

  elements.flowRankerStatus.textContent = `${formatNumber(scanner.eligible_symbols || 0)} eligible opportunities right now`;
  elements.flowRankerMeta.textContent = focusSymbols.length
    ? `Current focus: ${focusSymbols.slice(0, 4).join(", ")}`
    : "Waiting for liquid symbols with fresh quotes and EMA history";
  setFlowCardTone(
    elements.flowRankerCard,
    (scanner.eligible_symbols || 0) > 0 ? "live" : (scanner.hydrated_symbols || 0) > 0 ? "warm" : ""
  );

  if (decisionEngine.enabled) {
    elements.flowDecisionStatus.textContent = `OpenAI is deciding BUY / HOLD / SELL from the ranked shortlist`;
    elements.flowDecisionMeta.textContent = `${decisionEngine.model || "AI model"} • ${formatNumber(scanner.eligible_symbols || 0)} candidates visible`;
    setFlowCardTone(elements.flowDecisionCard, "live");
  } else {
    elements.flowDecisionStatus.textContent = `EMA 9 / 21 crossover engine is making the trade calls`;
    elements.flowDecisionMeta.textContent = `5-minute candles • rule-based strategy active`;
    setFlowCardTone(elements.flowDecisionCard, "warm");
  }

  if (data.bot && data.bot.halted) {
    elements.flowRiskStatus.textContent = `Bot halted: ${data.bot.stop_reason}`;
    elements.flowRiskMeta.textContent =
      `${formatCurrency(limits.max_capital_per_trade)} per trade • ${formatCurrency(limits.max_loss_per_day)} daily loss cap`;
    setFlowCardTone(elements.flowRiskCard, "alert");
  } else {
    elements.flowRiskStatus.textContent = `Wallet, broker balance, and protection limits are checked before every order`;
    elements.flowRiskMeta.textContent =
      `${formatCurrency(limits.max_capital_per_trade)} per trade • ${Number(limits.max_capital_loss_pct || 0).toFixed(0)}% floor • ${formatCurrency(capital.free_cash)} free cash`;
    setFlowCardTone(elements.flowRiskCard, "live");
  }

  if (latestSignal && latestSignal.action && latestSignal.action !== "HOLD") {
    elements.flowExecutionStatus.textContent =
      `${tradeMode === "PAPER" ? "Simulating" : "Placing"} ${latestSignal.action} ${latestSignal.symbol} when risk checks pass`;
    elements.flowExecutionMeta.textContent =
      `${formatNumber(session.trade_count || 0)} trades today • session ${formatCurrency(session.session_pnl || 0)}`;
  } else {
    elements.flowExecutionStatus.textContent =
      tradeMode === "PAPER"
        ? "Paper mode simulates fills and updates the bot wallet"
        : `${tradeMode} mode can send Groww MIS market orders`;
    elements.flowExecutionMeta.textContent =
      `${formatNumber(session.trade_count || 0)} trades today • session ${formatCurrency(session.session_pnl || 0)}`;
  }
  setFlowCardTone(elements.flowExecutionCard, tradeMode === "PAPER" ? "warm" : "live");
}

async function fetchCompanyInfo(symbol, name) {
  const sectorEl  = document.getElementById("company-sector");
  const nameEl    = document.getElementById("company-display-name");
  const ceoEl     = document.getElementById("company-ceo");
  const foundedEl = document.getElementById("company-founded");
  const hqEl      = document.getElementById("company-hq");
  const descEl    = document.getElementById("company-description");
  const factEl    = document.getElementById("company-key-fact");

  if (descEl) descEl.textContent = "Loading company information…";
  if (sectorEl) sectorEl.textContent = "Loading…";
  if (nameEl) nameEl.textContent = name;

  try {
    const resp = await fetch(`/api/company-info?symbol=${encodeURIComponent(symbol)}&name=${encodeURIComponent(name)}`);
    const info = await resp.json();
    if (sectorEl)  sectorEl.textContent  = info.sector       || "—";
    if (nameEl)    nameEl.textContent    = info.name         || name;
    if (ceoEl)     ceoEl.textContent     = info.ceo          || "—";
    if (foundedEl) foundedEl.textContent = info.founded      || "—";
    if (hqEl)      hqEl.textContent      = info.headquarters || "—";
    if (descEl)    descEl.textContent    = info.description  || "—";
    if (factEl)    factEl.textContent    = info.key_fact     || "";
  } catch {
    if (descEl) descEl.textContent = "Could not load company information.";
  }
}

function renderSelected(selected) {
  elements.selectedHeading.textContent = `${selected.name} (${selected.symbol})`;
  elements.heroLtp.textContent = formatCurrency(selected.ltp);
  elements.heroOpen.textContent = formatCurrency(selected.open);
  if (elements.heroOpenStrip) elements.heroOpenStrip.textContent = formatCurrency(selected.open);
  elements.heroChange.textContent = formatPercent(selected.change_pct);
  elements.heroChange.className = `metric-value ${sentimentClass(selected.change_pct)}`;

  elements.statHigh.textContent = formatCurrency(selected.high);
  elements.statLow.textContent = formatCurrency(selected.low);
  elements.statVolume.textContent = formatCompact(selected.volume);
  elements.statBook.textContent = `${formatCurrency(selected.bid)} / ${formatCurrency(selected.ask)}`;

  if (selected.symbol !== state.companyInfoSymbol) {
    state.companyInfoSymbol = selected.symbol;
    fetchCompanyInfo(selected.symbol, selected.name);
  }
}

function renderSignal(signal) {
  if (!signal) {
    elements.signalEmpty.classList.remove("hidden");
    elements.signalBody.classList.add("hidden");
    return;
  }

  elements.signalEmpty.classList.add("hidden");
  elements.signalBody.classList.remove("hidden");
  elements.signalAction.textContent = signal.action;
  elements.signalAction.className = `signal-badge signal-badge-large ${signal.action.toLowerCase()}`;
  elements.signalSymbol.textContent = signal.symbol;
  elements.signalPrice.textContent = formatCurrency(signal.price);
  const source = signal.source ? signal.source.toUpperCase() : "RULE";
  const confidence =
    signal.confidence !== undefined && signal.confidence !== null
      ? ` • ${Math.round(Number(signal.confidence) * 100)}% confidence`
      : "";
  elements.signalTime.textContent = `${timeAgo(signal.timestamp)} • ${new Date(signal.timestamp).toLocaleTimeString()} • ${source}${confidence}`;
  elements.signalReason.textContent = signal.reason;
}

function renderCapital(capital, limits) {
  elements.capitalHeading.textContent = formatCurrency(capital.starting_capital);
  elements.capitalStatus.textContent = `Stop after ${Number(limits.max_capital_loss_pct || 0).toFixed(0)}% capital loss`;

  elements.capitalFreeCash.textContent = formatCurrency(capital.free_cash);
  elements.capitalDeployed.textContent = formatCurrency(capital.deployed_capital);
  elements.capitalEquity.textContent = formatCurrency(capital.net_equity);
  elements.capitalReturn.textContent = formatPercent(capital.return_pct);
  elements.capitalUnrealized.textContent = formatCurrency(capital.unrealized_pnl);
  elements.capitalDrawdown.textContent = formatPercent(capital.drawdown_pct);
  elements.capitalFloor.textContent = formatCurrency(capital.equity_floor);
  elements.capitalOpenPositions.textContent = formatNumber(capital.open_positions);

  elements.capitalEquity.className = `risk-value ${sentimentClass(capital.net_equity - capital.starting_capital)}`;
  elements.capitalReturn.className = `risk-value ${sentimentClass(capital.return_pct)}`;
  elements.capitalUnrealized.className = `risk-value ${sentimentClass(capital.unrealized_pnl)}`;
  elements.capitalDrawdown.className = `risk-value ${sentimentClass(-Number(capital.drawdown_pct || 0))}`;
}

function renderBroker(broker) {
  elements.brokerHeading.textContent = broker.ucc ? `UCC ${broker.ucc}` : "Broker sync";
  elements.brokerUpdated.textContent = broker.updated_at
    ? `Updated ${new Date(broker.updated_at).toLocaleTimeString()}`
    : "Waiting for account data";
  if (elements.brokerAvailCash) elements.brokerAvailCash.textContent = formatCurrency(broker.clear_cash);
  elements.brokerClearCash.textContent = formatCurrency(broker.clear_cash);
  elements.brokerMis.textContent = formatCurrency(broker.mis_balance_available);
  elements.brokerCnc.textContent = formatCurrency(broker.cnc_balance_available);
  elements.brokerMarginUsed.textContent = formatCurrency(broker.net_margin_used);
  elements.brokerHoldingsCount.textContent = formatNumber(broker.holdings_count);
  elements.brokerHoldingsCost.textContent = formatCurrency(broker.holdings_cost_value);
  if (elements.brokerHoldingsCurrent) elements.brokerHoldingsCurrent.textContent = formatCurrency(broker.holdings_market_value);
  elements.brokerHoldingsPnl.textContent = formatCurrency(broker.holdings_unrealized_pnl);
  elements.brokerHoldingsReturn.textContent = formatPercent(broker.holdings_return_pct);
  elements.brokerOpenPositions.textContent = formatNumber(broker.open_positions_count);
  elements.brokerRealizedPnl.textContent = formatCurrency(broker.positions_realized_pnl);
  if (elements.brokerHoldingsCurrent) elements.brokerHoldingsCurrent.className = `risk-value ${sentimentClass(broker.holdings_market_value - broker.holdings_cost_value)}`;
  elements.brokerHoldingsPnl.className = `risk-value ${sentimentClass(broker.holdings_unrealized_pnl)}`;
  elements.brokerHoldingsReturn.className = `risk-value ${sentimentClass(broker.holdings_return_pct)}`;
  elements.brokerRealizedPnl.className = `risk-value ${sentimentClass(broker.positions_realized_pnl)}`;

  const segments = Array.isArray(broker.active_segments) && broker.active_segments.length
    ? ` Active: ${broker.active_segments.join(", ")}.`
    : "";
  const pricingNote =
    broker.holdings_count && broker.priced_holdings_count !== undefined
      ? ` Priced ${broker.priced_holdings_count}/${broker.holdings_count} holdings on live LTP.`
      : "";
  elements.brokerNote.textContent = `${broker.note || ""}${segments}${pricingNote}`.trim();
}

function renderSession(data) {
  const haltedLabel = data.bot.halted ? `Halted: ${data.bot.stop_reason}` : "Running";
  elements.botStatus.textContent = haltedLabel;
  elements.providerName.textContent = data.provider.name;
  elements.asOf.textContent = new Date(data.as_of).toLocaleTimeString();

  elements.limitCapital.textContent = formatCurrency(data.limits.max_capital_per_trade);
  elements.limitStopLoss.textContent = `${data.limits.stop_loss_pct}%`;
  elements.limitLoss.textContent = formatCurrency(data.limits.max_loss_per_day);
  elements.sessionTrades.textContent = `${data.session.trade_count} / ${data.limits.max_trades_per_day}`;
  elements.sessionPnl.textContent = formatCurrency(data.session.session_pnl);
  elements.sessionPnl.className = `risk-value ${sentimentClass(data.session.session_pnl)}`;
}

function renderThinking(entries) {
  elements.thinkingCount.textContent = `${entries.length} entries`;
  if (!entries.length) {
    elements.thinkingLog.innerHTML = `<div class="thinking-empty">Waiting for bot activity…</div>`;
    return;
  }
  const wasAtBottom =
    elements.thinkingLog.scrollTop + elements.thinkingLog.clientHeight >= elements.thinkingLog.scrollHeight - 8;

  elements.thinkingLog.innerHTML = entries
    .map((e) => {
      const t = new Date(e.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      return `<div class="thought-row level-${e.level}">
        <span class="thought-time">${t}</span>
        <span class="thought-sym">${e.symbol || "—"}</span>
        <span class="thought-msg">${e.message}</span>
      </div>`;
    })
    .join("");

  if (wasAtBottom) elements.thinkingLog.scrollTop = 0;
}

function renderTrades(trades) {
  if (!trades.length) {
    elements.tradeLogBody.innerHTML = `<tr><td colspan="8" class="empty-row">No trades simulated yet.</td></tr>`;
    return;
  }

  elements.tradeLogBody.innerHTML = trades
    .map(
      (trade) => `
        <tr>
          <td>${new Date(trade.timestamp).toLocaleTimeString()}</td>
          <td>${trade.symbol}</td>
          <td class="${trade.side === "BUY" ? "positive" : "negative"}">${trade.side}</td>
          <td>${formatCurrency(trade.price)}</td>
          <td>${trade.quantity}</td>
          <td class="${sentimentClass(trade.pnl)}">${formatCurrency(trade.pnl)}</td>
          <td>${trade.status}</td>
          <td>${trade.mode}</td>
        </tr>
      `
    )
    .join("");
}

function renderMoneyToday(trades) {
  const el = document.getElementById("money-today-breakdown");
  if (!el) return;

  if (!trades.length) {
    el.innerHTML = '<div class="money-today-empty">No trades yet today.</div>';
    return;
  }

  const buyPrices = {};
  const cards = trades.map((trade) => {
    if (trade.side === "BUY") buyPrices[trade.symbol] = trade.price;
    return trade;
  });

  el.innerHTML = cards
    .map((trade) => {
      const isSell = trade.side === "SELL";
      const sideClass = trade.side === "BUY" ? "buy" : "sell";
      const pnlClass = sentimentClass(trade.pnl);
      const boughtAt = isSell && buyPrices[trade.symbol] ? buyPrices[trade.symbol] : null;
      const t = new Date(trade.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });

      return `
        <div class="trade-breakdown-card">
          <div class="breakdown-card-top">
            <span class="signal-badge ${sideClass}">${trade.side}</span>
            <span class="breakdown-symbol">${trade.symbol}</span>
          </div>
          ${trade.side === "BUY" ? `<div class="breakdown-meta">Bought <span class="breakdown-price">${formatCurrency(trade.price)}</span></div>` : ""}
          ${isSell && boughtAt ? `<div class="breakdown-meta">Bought <span class="breakdown-price">${formatCurrency(boughtAt)}</span></div>` : ""}
          ${isSell ? `<div class="breakdown-meta">Sold <span class="breakdown-price">${formatCurrency(trade.price)}</span></div>` : ""}
          ${isSell && trade.pnl !== null && trade.pnl !== undefined ? `<div class="breakdown-pnl ${pnlClass}">${formatCurrency(trade.pnl)}</div>` : ""}
          <div class="breakdown-time">${t}</div>
        </div>
      `;
    })
    .join("");
}

function renderChart(selected) {
  if (!window.Plotly) {
    return;
  }

  if (!selected.candles.length) {
    return;
  }

  const dates = selected.candles.map((candle) => new Date(candle.time * 1000));
  const candleTrace = {
    type: "candlestick",
    x: dates,
    open: selected.candles.map((candle) => candle.open),
    high: selected.candles.map((candle) => candle.high),
    low: selected.candles.map((candle) => candle.low),
    close: selected.candles.map((candle) => candle.close),
    increasing: { line: { color: "#5fb572" }, fillcolor: "#5fb572" },
    decreasing: { line: { color: "#d96b6b" }, fillcolor: "#d96b6b" },
    name: "Price",
  };

  const ema9 = {
    type: "scatter",
    mode: "lines",
    x: selected.ema9.map((point) => new Date(point.time * 1000)),
    y: selected.ema9.map((point) => point.value),
    line: { color: "#72b3d1", width: 1.5 },
    name: "EMA 9",
  };

  const ema21 = {
    type: "scatter",
    mode: "lines",
    x: selected.ema21.map((point) => new Date(point.time * 1000)),
    y: selected.ema21.map((point) => point.value),
    line: { color: "#5fb572", width: 1.5 },
    name: "EMA 21",
  };

  const layout = {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    margin: { t: 12, r: 24, b: 28, l: 46 },
    showlegend: false,
    font: { family: "JetBrains Mono, monospace", color: "#ece8e0" },
    xaxis: {
      gridcolor: "rgba(255,255,255,0.04)",
      color: "#6a6e76",
      rangeslider: { visible: false },
    },
    yaxis: {
      gridcolor: "rgba(255,255,255,0.04)",
      color: "#6a6e76",
      side: "right",
    },
  };

  const config = {
    displayModeBar: false,
    responsive: true,
  };

  Plotly.react(elements.chart, [candleTrace, ema9, ema21], layout, config);
}

async function setMode(mode) {
  try {
    const response = await fetch("/api/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const result = await response.json();
    if (!response.ok) {
      setAlert({
        level: "warning",
        message: result.message,
        timestamp: new Date().toISOString(),
      });
    }
    renderMode(result.mode || "paper");
    fetchDashboard();
  } catch (error) {
    setAlert({
      level: "error",
      message: `Unable to switch mode: ${error.message}`,
      timestamp: new Date().toISOString(),
    });
  }
}

async function fetchDashboard() {
  if (state.isFetching) {
    return;
  }

  state.isFetching = true;
  try {
    const response = await fetch(`/api/dashboard?symbol=${encodeURIComponent(state.selectedSymbol)}`);
    const data = await response.json();

    renderMode(data.mode);
    renderSystemFlow(data);
    renderWatchlist(data.watchlist);
    renderPositions(data.positions || []);
    renderScanner(data.scanner);
    renderSelected(data.selected);
    renderSignal(data.latest_signal);
    renderCapital(data.capital, data.limits);
    renderBroker(data.broker);
    renderSession(data);
    renderThinking(data.thinking || []);
    renderTrades(data.trades);
    renderMoneyToday(data.trades);
    renderChart(data.selected);
    setAlert(data.alerts[0]);
  } catch (error) {
    setAlert({
      level: "error",
      message: `Dashboard refresh failed: ${error.message}`,
      timestamp: new Date().toISOString(),
    });
  } finally {
    state.isFetching = false;
  }
}

// ── Trading controls ────────────────────────────────────────────

const startBtn = document.getElementById("start-btn");
const stopBtn  = document.getElementById("stop-btn");
const tradingDot   = document.getElementById("trading-dot");
const tradingLabel = document.getElementById("trading-status-label");

function setTradingUI(running) {
  if (running) {
    startBtn.classList.add("hidden");
    stopBtn.classList.remove("hidden");
    tradingDot.className = "trading-dot running";
    tradingLabel.textContent = "Live";
  } else {
    startBtn.classList.remove("hidden");
    stopBtn.classList.add("hidden");
    tradingDot.className = "trading-dot idle";
    tradingLabel.textContent = "Idle";
  }
}

function startTrading() {
  if (state.isRunning) return;
  state.isRunning = true;
  setTradingUI(true);
  fetchDashboard();
  state.intervalId = setInterval(fetchDashboard, refreshIntervalMs);
}

function stopTrading() {
  if (!state.isRunning) return;
  state.isRunning = false;
  clearInterval(state.intervalId);
  state.intervalId = null;
  setTradingUI(false);
  tradingDot.className = "trading-dot stopped";
  tradingLabel.textContent = "Stopped";
}

startBtn.addEventListener("click", startTrading);
stopBtn.addEventListener("click", stopTrading);

// ── Auto-schedule 9:15 AM → 3:20 PM IST ────────────────────────

function getIST() {
  const now = new Date();
  const istMs = now.getTime() + now.getTimezoneOffset() * 60000 + 5.5 * 3600000;
  return new Date(istMs);
}

function msUntil(h, m, fromIST) {
  const target = new Date(fromIST);
  target.setHours(h, m, 0, 0);
  return target - fromIST;
}

function fmtCountdown(ms) {
  if (ms <= 0) return "";
  const totalSec = Math.floor(ms / 1000);
  const hh = Math.floor(totalSec / 3600);
  const mm = Math.floor((totalSec % 3600) / 60);
  const ss = totalSec % 60;
  return `in ${hh > 0 ? hh + "h " : ""}${mm}m ${ss}s`;
}

const countdownEl = document.getElementById("schedule-countdown");

function tickSchedule() {
  const ist = getIST();
  const h = ist.getHours(), m = ist.getMinutes();
  const cur  = h * 60 + m;
  const open = 9 * 60 + 15;
  const close = 15 * 60 + 20;

  if (cur < open) {
    const ms = msUntil(9, 15, ist);
    if (countdownEl) countdownEl.textContent = "Opens " + fmtCountdown(ms);
    setTimeout(() => { startTrading(); tickSchedule(); }, ms);
  } else if (cur >= open && cur < close) {
    const ms = msUntil(15, 20, ist);
    if (countdownEl) countdownEl.textContent = "Closes " + fmtCountdown(ms);
    if (!state.isRunning) startTrading();
    setTimeout(() => { stopTrading(); tickSchedule(); }, ms);
  } else {
    const msToMidnight = msUntil(24, 0, ist);
    const msToOpen = msToMidnight + (9 * 60 + 15) * 60000;
    if (countdownEl) countdownEl.textContent = "Tomorrow " + fmtCountdown(msToOpen);
    setTimeout(() => { startTrading(); tickSchedule(); }, msToOpen);
  }
}

// Update countdown display every second without re-scheduling
setInterval(() => {
  const ist = getIST();
  const h = ist.getHours(), m = ist.getMinutes(), s = ist.getSeconds();
  const cur  = h * 60 + m;
  const open = 9 * 60 + 15;
  const close = 15 * 60 + 20;

  if (!countdownEl) return;
  if (cur < open) {
    const ms = msUntil(9, 15, ist) - s * 1000;
    countdownEl.textContent = "Opens " + fmtCountdown(ms);
  } else if (cur >= open && cur < close) {
    const ms = msUntil(15, 20, ist) - s * 1000;
    countdownEl.textContent = "Closes " + fmtCountdown(ms);
  } else {
    countdownEl.textContent = "Market closed";
  }
}, 1000);

// Mode buttons (control bar)
elements.modeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setMode(button.dataset.mode);
  });
});

// ── Token update panel ───────────────────────────────────────────
(function () {
  const tokenBtn      = document.getElementById("token-btn");
  const tokenPanel    = document.getElementById("token-panel");
  const tokenInput    = document.getElementById("token-input");
  const tokenSubmit   = document.getElementById("token-submit");
  const tokenCancel   = document.getElementById("token-cancel");
  const tokenFeedback = document.getElementById("token-feedback");

  if (!tokenBtn) return;

  tokenBtn.addEventListener("click", () => {
    const open = tokenPanel.classList.toggle("hidden");
    tokenBtn.classList.toggle("active", !open);
    if (!open) tokenInput.focus();
  });

  tokenCancel.addEventListener("click", () => {
    tokenPanel.classList.add("hidden");
    tokenBtn.classList.remove("active");
    tokenFeedback.textContent = "";
  });

  tokenSubmit.addEventListener("click", async () => {
    const token = tokenInput.value.trim();
    if (!token) { tokenFeedback.textContent = "Paste a token first."; tokenFeedback.className = "token-feedback negative"; return; }
    tokenFeedback.textContent = "Applying…";
    tokenFeedback.className = "token-feedback neutral";
    try {
      const res  = await fetch("/api/update-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ access_token: token }),
      });
      const data = await res.json();
      tokenFeedback.textContent = data.message;
      tokenFeedback.className   = "token-feedback " + (data.ok ? "positive" : "negative");
      if (data.ok) {
        tokenInput.value = "";
        setTimeout(() => {
          tokenPanel.classList.add("hidden");
          tokenBtn.classList.remove("active");
          tokenFeedback.textContent = "";
          fetchDashboard();
        }, 1600);
      }
    } catch {
      tokenFeedback.textContent = "Request failed — is the server running?";
      tokenFeedback.className   = "token-feedback negative";
    }
  });
})();

// Single fetch on load so the dashboard shows current state immediately
// (polling only starts when Start Trading is clicked)
fetchDashboard();

// Kick off the auto-schedule logic
tickSchedule();
