# Webull / Robinhood factor-screen dashboard

Daily multi-tab stock screen that reconstructs a Webull-style factor view
(Growth / Value / Quality / Momentum / Income) across five universes and
publishes a single Claude artifact dashboard.

**Tabs:** S&P 100 · US high-momentum (52-wk highs) · US 52-wk lows ·
S&P 500 lows · Swing setups (RSI recovered 30–45 at/below the lower Bollinger band).
The low tabs and the swing tab also carry RSI(14) with cross arrows and
Bollinger Bands(20, 2) / %B.

## How it runs

It is driven by a **scheduled Claude cloud agent** (a routine). Each weekday
morning the agent:
1. clones this repo,
2. follows [`RUNBOOK.md`](RUNBOOK.md) — calls the Webull 52-week-high/low screens,
   the Robinhood swing scanner, then Webull snapshots and daily bars,
3. runs [`scripts/build.py`](scripts/build.py) to compute the factors / RSI /
   Bollinger and emit `factor-screen-dashboard.html`,
4. republishes the existing Claude artifact.

Market data comes from the user's connected Webull and Robinhood connectors
(reached through Claude); no API keys or account tokens live in this repo.

## Files
- `RUNBOOK.md` — the exact procedure the agent executes.
- `scripts/build.py` — compute + HTML builder (reads `scans/` + the run's tool-results).
- `scripts/template.html` — the dashboard page (tabs, heatmap, live Refresh button).
- `data/sp100.json`, `data/sp500.json` — fixed index constituents.

## Method & disclaimer
Factors are percentile-ranked within each tab (Growth/Value/Quality/Income);
Momentum is absolute 52-wk range position; Comp is the equal-weight mean.
Inputs are fast snapshot proxies, so Growth and Quality are approximations.
This is a transparent reconstruction, **not** Webull's proprietary Quant Rating,
and **not** investment advice.
