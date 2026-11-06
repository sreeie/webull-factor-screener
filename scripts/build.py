#!/usr/bin/env python3
"""
Factor-screen dashboard builder (cloud-routine edition).

Reads the connector outputs that the agent saved during the run and produces
factor-screen-dashboard.html, ready to publish to the Claude artifact.

Inputs it looks for:
  scans/newlow_*.json , scans/nearlow_*.json      -> 52-wk-low pool  (Webull get_52_week_high_low)
  scans/newhigh_*.json, scans/nearhigh_*.json     -> 52-wk-high pool (Webull get_52_week_high_low)
  scans/preview_*.json                            -> swing pool      (Robinhood preview_scan)
  data/sp100.json , data/sp500.json               -> fixed index lists (in the repo)
  <tool-results>/*get_stock_snapshot*  (list of snapshot records)
  <tool-results>/*get_stock_bars*      ({result:[{symbol,result:[bars newest-first]}]})

Universes:
  sp100    = data/sp100.json (fixed)                     -> factors only
  highmom  = new/near-high pool, mcap >= $1B             -> factors only
  lows     = new/near-low pool, mcap >= $1B              -> factors + RSI + Bollinger
  sp500low = lows that are also in data/sp500.json       -> factors + RSI + Bollinger
  swing    = Robinhood preview_scan pool                 -> factors + RSI + Bollinger

Factor method (identical across tabs): percentile-rank WITHIN the tab for
Growth / Value / Quality / Income; Momentum = absolute position in the 52-wk
range; Comp = equal-weight mean. Snapshot-proxy inputs, so Growth & Quality
are proxies. Not Webull's Quant Rating; not investment advice.

Usage:  python scripts/build.py            (run from the repo root)
Env:    TR_DIR  optional explicit tool-results directory
        ASOF    optional date stamp string (default: today UTC)
"""
import json, glob, os, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE) if os.path.basename(HERE) == "scripts" else HERE
DATA = os.path.join(REPO, "data")
CWD  = os.getcwd()
OUT  = os.environ.get("OUT_DIR", CWD)
ASOF = os.environ.get("ASOF") or datetime.datetime.utcnow().strftime("%Y-%m-%d")

# ---------- locate scans/ and tool-results ----------
def scan_dirs():
    out = []
    for d in (os.path.join(CWD, "scans"), os.path.join(REPO, "scans")):
        if os.path.isdir(d) and d not in out:
            out.append(d)
    return out

def tr_dirs():
    dirs = []
    env = os.environ.get("TR_DIR")
    if env and os.path.isdir(env):
        dirs.append(env)
    for base in (os.path.expanduser("~/.claude/projects"), "/root/.claude/projects",
                 "/home/user/.claude/projects"):
        for p in glob.glob(base + "/**/tool-results", recursive=True):
            if p not in dirs:
                dirs.append(p)
    return dirs

def _load_json_file(fn):
    """Return a list of parsed JSON objects from a tool-result file.
    Handles both a raw-JSON file and the persisted [{type,text}] wrapper."""
    try:
        raw = open(fn, encoding="utf-8").read()
    except Exception:
        return []
    try:
        d = json.loads(raw)
    except Exception:
        return []
    if isinstance(d, list) and d and isinstance(d[0], dict) and "text" in d[0]:
        out = []
        for blk in d:
            t = blk.get("text")
            if t:
                try: out.append(json.loads(t))
                except Exception: pass
        return out
    return [d]

# ---------- gather connector outputs ----------
def load_scan_pool(prefixes):
    """Union of {sym:{name,exch,mv}} from scans/<prefix>_*.json, mcap >= $1B."""
    uni = {}
    for d in scan_dirs():
        for pre in prefixes:
            for f in glob.glob(os.path.join(d, pre + "_*.json")):
                for obj in _load_json_file(f):
                    rows = obj.get("data") if isinstance(obj, dict) else None
                    if rows is None and isinstance(obj, list):
                        rows = obj
                    for r in (rows or []):
                        s = r.get("symbol"); mv = r.get("market_value")
                        if not s or not mv:
                            continue
                        try: mvf = float(mv)
                        except Exception: continue
                        if mvf < 1e9 or " " in s:
                            continue
                        uni.setdefault(s, {"name": r.get("name"),
                                           "exch": r.get("exchange_code"),
                                           "mv": round(mvf / 1e9, 2)})
    return uni

def load_swing_pool():
    """{sym:{name,mcap}} from Robinhood preview_scan files (scans/preview_*.json)."""
    uni = {}
    for d in scan_dirs():
        for f in glob.glob(os.path.join(d, "preview_*.json")):
            for obj in _load_json_file(f):
                res = (((obj or {}).get("data") or {}).get("result") or {}) if isinstance(obj, dict) else {}
                for row in res.get("results", []):
                    s = row.get("ticker") or (row.get("columns") or {}).get("Symbol")
                    if not s or " " in s:
                        continue
                    cols = row.get("columns") or {}
                    mc = cols.get("Market cap")
                    try: mcap = round(float(mc) / 1e9, 2) if mc not in (None, "") else None
                    except Exception: mcap = None
                    uni.setdefault(s, {"name": cols.get("Name") or s, "mcap": mcap})
    return uni

def load_snapshots():
    snap = {}
    for d in tr_dirs():
        for f in glob.glob(os.path.join(d, "*get_stock_snapshot*")):
            for obj in _load_json_file(f):
                arr = obj.get("data") if isinstance(obj, dict) else obj
                if isinstance(arr, list):
                    for r in arr:
                        if isinstance(r, dict) and r.get("symbol"):
                            snap[r["symbol"]] = r
    return snap

def load_bars():
    bars = {}
    for d in tr_dirs():
        for f in glob.glob(os.path.join(d, "*get_stock_bars*")):
            for obj in _load_json_file(f):
                for it in (obj.get("result", []) if isinstance(obj, dict) else []):
                    s = it.get("symbol"); bs = it.get("result", [])
                    if s and bs:
                        try:
                            cl = [float(b["close"]) for b in bs][::-1]
                        except Exception:
                            continue
                        if cl:
                            bars[s] = cl
    return bars

# ---------- math ----------
def fnum(r, k):
    try: return float(r.get(k))
    except Exception: return None

def inv(x):
    return 1.0 / x if (x and x > 0) else 0.0

def grow(e, t):
    if t is None or t <= 0: return -999
    if e is None or e <= 0: return 0.5
    return t / e - 1

def qual(t, b):
    if t is None or t <= 0: return -999
    return t / b if (b and b > 0) else 0.40

def rankpct(vals):
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    out = [0] * n
    for rank, i in enumerate(order):
        out[i] = (rank / (n - 1)) * 100 if n > 1 else 50
    return out

def rsi_series(cl, p=14):
    if len(cl) < p + 1: return []
    d = [cl[i + 1] - cl[i] for i in range(len(cl) - 1)]
    g = [max(x, 0) for x in d]; l = [max(-x, 0) for x in d]
    ag = sum(g[:p]) / p; al = sum(l[:p]) / p
    out = [100.0 if al == 0 else 100 - 100 / (1 + ag / al)]
    for i in range(p, len(d)):
        ag = (ag * (p - 1) + g[i]) / p; al = (al * (p - 1) + l[i]) / p
        out.append(100.0 if al == 0 else 100 - 100 / (1 + ag / al))
    return out

def rsi_cross(cl):
    rs = rsi_series(cl, 14)
    if not rs: return None
    xd = xu = 0; tail = rs[-6:]
    for i in range(1, len(tail)):
        if tail[i - 1] >= 30 and tail[i] < 30: xd = 1
        if tail[i - 1] < 30 and tail[i] >= 30: xu = 1
    return {"rsi": round(rs[-1], 1), "xd": xd, "xu": xu}

def bands(cl):
    if len(cl) < 20: return None
    w = cl[-20:]; m = sum(w) / 20
    sd = (sum((x - m) ** 2 for x in w) / 20) ** 0.5
    return {"lo": m - 2 * sd, "mid": m, "hi": m + 2 * sd}

def factors(symlist, snap):
    """Return {sym: {g,v,q,m,inc,comp,ctxpos,mcap,px,hi,lo}} ranked within symlist."""
    have = []
    for s in symlist:
        r = snap.get(s)
        if not r: continue
        px = fnum(r, "price"); hi = fnum(r, "fifty_two_wk_high"); lo = fnum(r, "fifty_two_wk_low")
        if None in (px, hi, lo) or hi <= lo: continue
        have.append((s, fnum(r, "pe_ratio"), fnum(r, "pb_ratio"), fnum(r, "ps_ratio"),
                     fnum(r, "yield") or 0.0, fnum(r, "eps"), fnum(r, "eps_ttm"),
                     fnum(r, "bps"), px, hi, lo, fnum(r, "market_value")))
    gP = rankpct([grow(h[5], h[6]) for h in have])
    vP = rankpct([inv(h[1]) + inv(h[2]) + inv(h[3]) for h in have])
    qP = rankpct([qual(h[6], h[7]) for h in have])
    iP = rankpct([h[4] for h in have])
    out = {}
    for i, h in enumerate(have):
        g = round(gP[i]); v = round(vP[i]); q = round(qP[i])
        m = round(max(0, min(1, (h[8] - h[10]) / (h[9] - h[10]))) * 100); inc = round(iP[i])
        out[h[0]] = {"g": g, "v": v, "q": q, "m": m, "inc": inc,
                     "comp": round((g + v + q + m + inc) / 5),
                     "mcap": round(h[11] / 1e9, 2) if h[11] else None,
                     "px": h[8], "hi": h[9], "lo": h[10]}
    return out

# ---------- assemble ----------
def main():
    snap = load_snapshots(); bars = load_bars()
    sp100_syms = json.load(open(os.path.join(DATA, "sp100.json")))
    sp500 = set(json.load(open(os.path.join(DATA, "sp500.json"))))
    lowpool = load_scan_pool(["newlow", "nearlow"])
    highpool = load_scan_pool(["newhigh", "nearhigh"])
    swingpool = load_swing_pool()

    def base_rec(s, name, exch, fac, ctxtype, in_sp):
        f = fac.get(s)
        rec = {"sym": s, "name": name, "exch": exch,
               "mcap": (f["mcap"] if f and f["mcap"] else None),
               "comp": f["comp"] if f else None, "g": f["g"] if f else None,
               "v": f["v"] if f else None, "q": f["q"] if f else None,
               "m": f["m"] if f else None, "inc": f["inc"] if f else None,
               "ctx": 0.0, "sp": 1 if in_sp else 0}
        if f:
            px, hi, lo = f["px"], f["hi"], f["lo"]
            if ctxtype == "high": rec["ctx"] = round((px - hi) / hi * 100, 1)
            elif ctxtype == "low": rec["ctx"] = round((px - lo) / lo * 100, 1)
        return rec

    def add_rsi_bb(rec, s):
        cl = bars.get(s)
        if cl:
            c = rsi_cross(cl)
            if c: rec["rsi"] = c["rsi"]; rec["xd"] = c["xd"]; rec["xu"] = c["xu"]
            b = bands(cl)
            if b:
                rec["blo"] = round(b["lo"], 2); rec["bmid"] = round(b["mid"], 2); rec["bhi"] = round(b["hi"], 2)
                f = facmap_px.get(s)
                px = f if f is not None else cl[-1]
                if b["hi"] > b["lo"]:
                    rec["pb"] = round((px - b["lo"]) / (b["hi"] - b["lo"]) * 100, 1)
        rec.setdefault("rsi", None); rec.setdefault("xd", 0); rec.setdefault("xu", 0)
        rec.setdefault("pb", None); rec.setdefault("blo", None); rec.setdefault("bmid", None); rec.setdefault("bhi", None)
        return rec

    # sp100
    f100 = factors(sp100_syms, snap)
    sp100 = [base_rec(s, (snap.get(s, {}).get("name") or s), "", f100, "high", s in sp500) for s in sp100_syms]
    # fix name/exch: snapshot has no name; keep ticker as name fallback, exch blank
    sp100.sort(key=lambda x: (x["comp"] is None, -(x["comp"] or 0)))

    # highmom
    fhigh = factors(list(highpool), snap)
    highmom = [base_rec(s, highpool[s]["name"] or s, highpool[s]["exch"] or "", fhigh, "high", s in sp500) for s in highpool]
    highmom.sort(key=lambda x: (x["comp"] is None, -(x["comp"] or 0)))

    # lows (+ sp500low) -- need px for %B
    flow = factors(list(lowpool), snap)
    global facmap_px
    facmap_px = {s: flow[s]["px"] for s in flow}
    lows = []
    for s in lowpool:
        rec = base_rec(s, lowpool[s]["name"] or s, lowpool[s]["exch"] or "", flow, "low", s in sp500)
        add_rsi_bb(rec, s); lows.append(rec)
    lows.sort(key=lambda x: (x["comp"] is None, -(x["comp"] or 0)))
    sp500low = [r for r in lows if r["sp"]]

    # swing
    fsw = factors(list(swingpool), snap)
    facmap_px = {s: fsw[s]["px"] for s in fsw}
    swing = []
    for s in swingpool:
        rec = base_rec(s, swingpool[s]["name"] or s, "", fsw, None, s in sp500)
        if swingpool[s]["mcap"] and not rec["mcap"]:
            rec["mcap"] = swingpool[s]["mcap"]
        add_rsi_bb(rec, s)
        # swing ctx = % vs 20d avg (band mid)
        if rec["bmid"]:
            f = fsw.get(s)
            px = f["px"] if f else (bars.get(s, [None])[-1])
            if px is not None:
                rec["ctx"] = round((px / rec["bmid"] - 1) * 100, 1)
        if rec["comp"] is None and rec["rsi"] is None:
            continue
        swing.append(rec)
    swing.sort(key=lambda x: (x["comp"] is None, -(x["comp"] or 0)))

    DS = {"sp100": sp100, "highmom": highmom, "lows": lows, "sp500low": sp500low, "swing": swing}
    html = render(DS, ASOF)
    outp = os.path.join(OUT, "factor-screen-dashboard.html")
    open(outp, "w", encoding="utf-8").write(html)
    print("WROTE", outp, len(html), "bytes")
    for k in ("sp100", "highmom", "lows", "sp500low", "swing"):
        arr = DS[k]; sc = sum(1 for r in arr if r.get("comp") is not None)
        print(f"  {k}: {len(arr)} names, {sc} scored")
    print("lows xd<30:", sum(1 for r in lows if r.get("xd")),
          " xu>30:", sum(1 for r in lows if r.get("xu")),
          " | swing xd:", sum(1 for r in swing if r.get("xd")),
          " xu:", sum(1 for r in swing if r.get("xu")))

# ---------- HTML (template lives in template.html next to this script) ----------
def render(DS, asof):
    tpl = open(os.path.join(HERE, "template.html"), encoding="utf-8").read()
    return tpl.replace("__DATA__", json.dumps(DS, separators=(",", ":"))).replace("__ASOF__", asof)

if __name__ == "__main__":
    main()
