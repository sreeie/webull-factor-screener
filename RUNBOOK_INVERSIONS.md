# RUNBOOK_INVERSIONS — option premium-inversion scan (S&P 500 lows)

You are a scheduled cloud agent. Scan the option chains of the ~91 S&P-500-low
names for **premium inversions** and publish a ranked results page. Work from the
repo root (your cwd). Read-only market data + publishing one results artifact only;
place NO orders and touch NO account/watchlist.

Tools: `mcp__Webull__get_stock_snapshot`, `mcp__Webull__get_option_contracts`,
`mcp__Webull__get_option_snapshot` (load via ToolSearch). Large outputs auto-save to
a tool-results file whose path the result prints.

## Step 0 — prep
```
mkdir -p scans
```
Read `data/scan_sp500low.json` — the list of ~91 tickers to scan.

## Step 1 — underlying prices
Call `get_stock_snapshot` once (or twice) with all the tickers as a comma string
(`category:"US_STOCK", extend_hour_required:false, overnight_required:false`, ≤100 per call).
From the result build a price map `ticker -> price`. (The result auto-saves; read it with jq/python.)

## Step 2 — option contracts per ticker (strikes within ~30% of spot, ALL expirations)
For each ticker T with price P, call:
```
get_option_contracts {category:"US_OPTION", underlying_symbols:T,
  strike_price_gte: round(P*0.7), strike_price_lte: round(P*1.3), page_size:1000}
```
The result prints a saved PATH — copy it to `scans/opt_<T>.json`:
```
cp "<PATH>" scans/opt_<T>.json
```
Do all ~91. Skip a ticker only if it errors (note which).

## Step 3 — option quotes
Collect EVERY option `symbol` from all `scans/opt_*.json`. Call `get_option_snapshot`
in batches of **≤20 symbols** (comma string, `category:"US_OPTION"`). **Save each batch's
JSON array to `scans/q_<i>.json`** (i = 1,2,3,…): these batches are small and usually come
back INLINE rather than as a saved file, so you must write them yourself (if a batch did
save to a PATH, `cp` it to `scans/q_<i>.json` instead). The scanner reads `scans/q_*.json`.
This is the bulk of the work — keep going batch after batch until every symbol is quoted;
on a rate limit, wait briefly and continue.

## Step 4 — detect + build the page
```
python scripts/inversions.py
```
It reads `scans/opt_*.json` + the tool-results quotes, flags inversions (calls: a higher
strike whose bid tops the adjacent lower strike; puts: a lower strike whose bid tops the
adjacent higher strike) keeping only the rich leg with **bid > 0 and open interest > 0**,
and writes `inversions.html`. It prints the count and the top rows.

## Step 5 — publish the results page (NEW artifact)
Publish `inversions.html` as a brand-new artifact (no url → creates a new one):
```
Artifact {action:"publish", file_path:"<absolute path to inversions.html>",
          icon:"chart", title:"Option Premium Inversions"}
```
No `capabilities` needed — the page is static (no live calls). Report the new artifact URL.

## Step 6 — report
Summarize: tickers scanned, option symbols quoted, inversions found (calls / puts), the
top 5 by bid gap, and the published artifact URL. If you had to stop early (time/limits),
still run Step 4–5 on what you collected and say the scan was partial and how far it got.

Note: inversions are usually stale/mispriced wing quotes rather than free money — the
liquidity filter removes the worst, but the results are a watchlist to verify live, not
trade signals. Not investment advice.
