# RUNBOOK — daily factor-screen re-screen

You are a scheduled cloud agent. Your job: re-screen all five tabs on current
Webull/Robinhood data and republish the existing Claude dashboard artifact.
Work from the repo root (this is your cwd). Follow every step in order. The
connectors are already attached (Webull, Robinhood). Large connector outputs
auto-save to a tool-results file and the tool result prints the saved PATH —
you will copy some of those into `scans/`.

**Do not place any order, create/modify any watchlist, or write to any
account. Read-only market data + publish the artifact only.**

Fixed values:
- Artifact to update: `https://claude.ai/artifact/HnDCoQauUpxpookf4qYbfM`
- Invalid tickers to always skip: `BK ET DDS SUN TCBI OBK RCI IBOC CECO DJT KVUE`
  and any symbol containing a space or a dot (e.g. `BRK B`, `PBR.A`).

---

## Step 0 — prep
```
mkdir -p scans
```

## Step 1 — universe scans (Webull `mcp__Webull__get_52_week_high_low`)
Common args: `category:"US_STOCK", sort_by:"MARKET_VALUE", direction:"DESC", page_size:"200"`.
After each call the result prints `Output has been saved to <PATH>`. Copy that file into `scans/`:
```
cp "<PATH>" scans/<name>.json
```
Make these calls and copies (page through until `has_more` is false OR the page's
smallest `market_value` drops below 1e9 — i.e. $1B):
- `rank_type:"NEW_LOW"`  page 1 → `scans/newlow_1.json`, page 2 → `scans/newlow_2.json` (usually enough)
- `rank_type:"NEAR_LOW"` page 1 → `scans/nearlow_1.json`
- `rank_type:"NEW_HIGH"` page 1 → `scans/newhigh_1.json`, page 2 → `scans/newhigh_2.json`
- `rank_type:"NEAR_HIGH"` pages 1..5 → `scans/nearhigh_1.json` … (keep going while the last page is still ≥ $1B; it usually ends around page 5)

## Step 2 — swing scan (Robinhood `mcp__Robinhood__preview_scan`), 3 market-cap buckets
Same filters each call, only the market-cap band changes:
```
filters:[
  {"filter_type":"FILTER_TYPE_INSTRUMENT_TYPE","predicate":"=","values":["STOCK"]},
  {"filter_type":"FILTER_TYPE_RSI","interval":"1d","length":14,"predicate":"BETWEEN","values":["30","45"]},
  {"expression":"pricePosInBollinger(candleCount=20, candlePeriod=\"1d\", session=\"all\")","predicate":"<=","values":["0"],"display_title":"%B"},
  {"filter_type":"FILTER_TYPE_MARKET_CAP","predicate":"BETWEEN","values":[LO,HI]}
]
```
Buckets (LO,HI): `["10000000000","10000000000000"]`, `["3000000000","10000000000"]`, `["1000000000","3000000000"]`.
Save each result to `scans/preview_1.json`, `scans/preview_2.json`, `scans/preview_3.json`
(if it saved to a tool-results PATH, `cp` it; if it came back inline, write the JSON to that file yourself).

## Step 3 — snapshots (Webull `mcp__Webull__get_stock_snapshot`)
Collect every unique symbol from: all `scans/*.json` pools **plus** `data/sp100.json`.
Drop the invalid tickers / spaces / dots listed above. Call in batches of **≤100**
symbols passed as a **comma-separated string**:
```
{"symbols":"AAA,BBB,...", "category":"US_STOCK", "extend_hour_required":false, "overnight_required":false}
```
Outputs auto-save to tool-results (no copy needed — build.py reads them). If a batch
errors `INVALID_SYMBOL [X]`, remove X and retry that one batch.

## Step 4 — daily bars (Webull `mcp__Webull__get_stock_bars`)
Collect unique symbols from the **low** scans (`newlow_*`,`nearlow_*`) **plus** the
**swing** scans (`preview_*`) — these are the tabs that show RSI/Bollinger. Drop
invalid/dotted. Call in batches of **≤20** symbols passed as a **JSON array**:
```
{"symbols":["AAA","BBB",...], "category":"US_STOCK", "timespan":"D", "count":40}
```
Outputs auto-save to tool-results. (If two parallel calls ever collide on one file,
just re-fetch the missing batch; build.py will tell you which symbols lack bars if
you re-run it.)

## Step 5 — build the dashboard
```
python scripts/build.py
```
It prints per-tab counts. Sanity check (orders of magnitude, not exact):
sp100 ≈ 99 · highmom a few hundred · lows a few hundred · sp500low ≈ 90–110 ·
swing a few dozen. If a tab is 0, a scan or snapshot batch is missing — re-fetch and re-run.
Output file: `factor-screen-dashboard.html` in the repo root.

## Step 6 — publish to the existing artifact
Read then publish (reading first is required before updating an artifact this
session hasn't touched):
```
Artifact {action:"read",    url:"https://claude.ai/artifact/HnDCoQauUpxpookf4qYbfM"}
Artifact {action:"publish", url:"https://claude.ai/artifact/HnDCoQauUpxpookf4qYbfM",
          file_path:"<absolute path to factor-screen-dashboard.html>",
          label:"Daily re-screen"}
```
Do **not** pass `capabilities` — the stored `mcp: Webull` declaration carries forward.

## Step 7 — report
Finish with a short summary: the per-tab counts, the lows/swing cross counts, and the
published artifact version id. If any step failed, say which and what you tried.

---
Method note (unchanged): factors are percentile-ranked WITHIN each tab for
Growth/Value/Quality/Income; Momentum is absolute position in the 52-wk range;
Comp is the equal-weight mean. Snapshot-proxy inputs → Growth & Quality are
proxies. This is a transparent reconstruction, **not** Webull's Quant Rating,
and **not** investment advice.
