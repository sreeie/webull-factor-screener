#!/usr/bin/env python3
"""
Option-chain premium INVERSION scanner.

An inversion = the normal strike->premium monotonicity is violated:
  CALLS: premium should FALL as strike rises; flag a higher strike whose BID
         exceeds the adjacent lower strike's bid.
  PUTS:  premium should RISE as strike rises; flag a lower strike whose BID
         exceeds the adjacent higher strike's bid.
Liquidity filter (skip dead quotes): the RICH leg must have bid > 0 AND
open_interest > 0.

Inputs (saved by the agent during the run):
  scans/opt_<TICKER>.json   -> get_option_contracts output for that underlying
  <tool-results>/*get_option_snapshot*  -> quotes {symbol,bid,ask,open_interest,imp_vol,strike_price}

Output: inversions.html (ranked by bid gap, desc) + a printed summary.
Usage:  python scripts/inversions.py
Env:    ASOF (date stamp, default today UTC)
"""
import json, glob, os, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE) if os.path.basename(HERE) == "scripts" else HERE
CWD  = os.getcwd()
OUT  = os.environ.get("OUT_DIR", CWD)
ASOF = os.environ.get("ASOF") or datetime.datetime.utcnow().strftime("%Y-%m-%d")

def scan_dirs():
    return [d for d in (os.path.join(CWD, "scans"), os.path.join(REPO, "scans")) if os.path.isdir(d)]

def tr_dirs():
    dirs = []
    env = os.environ.get("TR_DIR")
    if env and os.path.isdir(env): dirs.append(env)
    for base in (os.path.expanduser("~/.claude/projects"), "/root/.claude/projects", "/home/user/.claude/projects"):
        for p in glob.glob(base + "/**/tool-results", recursive=True):
            if p not in dirs: dirs.append(p)
    return dirs

def load_json_file(fn):
    try: raw = open(fn, encoding="utf-8").read()
    except Exception: return []
    try: d = json.loads(raw)
    except Exception: return []
    if isinstance(d, list) and d and isinstance(d[0], dict) and "text" in d[0]:
        out = []
        for blk in d:
            t = blk.get("text")
            if t:
                try: out.append(json.loads(t))
                except Exception: pass
        return out
    return [d]

def fnum(v):
    try: return float(v)
    except Exception: return None

# ---- contracts: option symbol -> {u,exp,strike,type} ----
contracts = {}
for d in scan_dirs():
    for f in glob.glob(os.path.join(d, "opt_*.json")):
        for obj in load_json_file(f):
            arr = obj if isinstance(obj, list) else (obj.get("data") if isinstance(obj, dict) else None)
            for c in (arr or []):
                s = c.get("symbol")
                if not s: continue
                contracts[s] = {"u": c.get("underlying_symbol"), "exp": c.get("expiration_date"),
                                "strike": fnum(c.get("strike_price")), "type": c.get("option_type")}

# ---- quotes: option symbol -> {bid,ask,oi,iv} ----
# option snapshots may be saved to tool-results (large) OR written by the agent to
# scans/q_*.json (small/inline batches) — read both.
quotes = {}
qfiles = []
for d in tr_dirs():
    qfiles += glob.glob(os.path.join(d, "*get_option_snapshot*"))
for d in scan_dirs():
    qfiles += glob.glob(os.path.join(d, "q_*.json"))
for f in qfiles:
    for obj in load_json_file(f):
        arr = obj if isinstance(obj, list) else (obj.get("data") if isinstance(obj, dict) else None)
        for o in (arr or []):
            s = o.get("symbol")
            if not s: continue
            quotes[s] = {"bid": fnum(o.get("bid")), "ask": fnum(o.get("ask")),
                         "oi": fnum(o.get("open_interest")) or 0, "iv": fnum(o.get("imp_vol"))}

# ---- group by (underlying, expiry, type) ----
groups = {}
for s, c in contracts.items():
    if c["strike"] is None or not c["exp"] or not c["type"]: continue
    q = quotes.get(s)
    if not q: continue
    groups.setdefault((c["u"], c["exp"], c["type"]), []).append(
        {"strike": c["strike"], "bid": q["bid"], "ask": q["ask"], "oi": q["oi"], "iv": q["iv"], "sym": s})

def mid(r):
    if r["bid"] is not None and r["ask"] is not None: return (r["bid"] + r["ask"]) / 2
    return r["ask"] if r["ask"] is not None else r["bid"]

today = datetime.date.today()
def dte(exp):
    try:
        y, m, d = map(int, exp.split("-")); return (datetime.date(y, m, d) - today).days
    except Exception: return None

rows = []
for (u, exp, typ), legs in groups.items():
    legs.sort(key=lambda r: r["strike"])
    for i in range(len(legs) - 1):
        lo, hi = legs[i], legs[i + 1]
        bl, bh = (lo["bid"] or 0), (hi["bid"] or 0)
        if typ == "CALL":
            # higher strike richer than lower -> inversion; rich leg = hi
            if bh > bl and bh > 0 and hi["oi"] > 0:
                rows.append(dict(u=u, exp=exp, dte=dte(exp), type="CALL", rich=hi["strike"], richbid=bh,
                                 richmid=mid(hi), oi=hi["oi"], iv=hi["iv"], ref=lo["strike"], refbid=bl, gap=round(bh - bl, 2)))
        else:  # PUT: lower strike richer than higher -> inversion; rich leg = lo
            if bl > bh and bl > 0 and lo["oi"] > 0:
                rows.append(dict(u=u, exp=exp, dte=dte(exp), type="PUT", rich=lo["strike"], richbid=bl,
                                 richmid=mid(lo), oi=lo["oi"], iv=lo["iv"], ref=hi["strike"], refbid=bh, gap=round(bl - bh, 2)))

rows.sort(key=lambda r: r["gap"], reverse=True)

def g(v, nd=2):
    return "" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))

trs = "".join(
    f"<tr data-t='{r['type']}'><td class='l sym'>{r['u']}</td><td class='l'>{r['exp']}</td><td>{g(r['dte'],0)}</td>"
    f"<td class='l'><span class='tag {r['type'].lower()}'>{r['type']}</span></td>"
    f"<td>{g(r['rich'],1)}</td><td class='b'>{g(r['richbid'])}</td><td>{g(r['richmid'])}</td>"
    f"<td>{g(r['oi'],0)}</td><td>{('' if r['iv'] is None else str(round(r['iv']*100))+'%')}</td>"
    f"<td>{g(r['ref'],1)}</td><td>{g(r['refbid'])}</td><td class='b'>{g(r['gap'])}</td></tr>"
    for r in rows)

ncall = sum(1 for r in rows if r["type"] == "CALL"); nput = len(rows) - ncall
nnames = len(set(r["u"] for r in rows))

HTML = f"""<meta name="claude:viewport-fit-sides" content="cover"><title>Option Premium Inversions</title>
<style>
 :root{{--bg:#f4f3f0;--surface:#fff;--ink:#1a1a17;--soft:#5f5e58;--faint:#8c8a82;--line:rgba(26,26,23,.12);--ls:rgba(26,26,23,.22);--accent:#3b5bd6;--green:#1b9e59;--danger:#c0392b;--fn:"IBM Plex Mono",ui-monospace,monospace;--fu:"Inter",system-ui,sans-serif}}
 @media(prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#16161a;--surface:#201f24;--ink:#edecea;--soft:#a9a7a0;--faint:#76746d;--line:rgba(255,255,255,.12);--ls:rgba(255,255,255,.22);--accent:#8ea3f0;--green:#3fae6e;--danger:#e2605f;color-scheme:dark}}}}
 *{{box-sizing:border-box}} body{{background:var(--bg);color:var(--ink);font-family:var(--fu);margin:0;line-height:1.5}}
 .wrap{{max-width:1100px;margin:0 auto;padding:1.6rem max(16px,env(safe-area-inset-left)) 3rem}}
 h1{{font-size:1.4rem;margin:0 0 .3rem}} .sub{{color:var(--soft);font-size:.86rem;margin:0 0 1rem;max-width:80ch}}
 .tiles{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:1rem}} .tile{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:.6rem .8rem}}
 .tile .l{{font-size:.62rem;text-transform:uppercase;letter-spacing:.06em;color:var(--faint)}} .tile .v{{font-size:1.1rem;font-weight:600;font-family:var(--fn)}}
 .controls{{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin-bottom:.7rem}}
 input,select{{height:32px;padding:0 .6rem;border:1px solid var(--ls);border-radius:8px;background:var(--surface);color:var(--ink);font:inherit}}
 .tablewrap{{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--surface)}}
 table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}
 th{{position:sticky;top:0;background:var(--surface);font-size:.64rem;text-transform:uppercase;letter-spacing:.03em;color:var(--soft);text-align:right;padding:.5rem .55rem;border-bottom:1px solid var(--ls);white-space:nowrap}}
 td{{padding:.34rem .55rem;border-bottom:1px solid var(--line);font-family:var(--fn);font-size:.78rem;text-align:right;white-space:nowrap}}
 th.l,td.l{{text-align:left}} td.sym{{font-family:var(--fu);font-weight:600}} td.b{{font-weight:600}}
 .tag{{font-family:var(--fu);font-size:.62rem;font-weight:600;padding:.08rem .35rem;border-radius:5px}} .tag.call{{background:rgba(27,158,89,.16);color:var(--green)}} .tag.put{{background:rgba(192,57,43,.14);color:var(--danger)}}
 .meth{{margin-top:1rem;font-size:.78rem;color:var(--soft);line-height:1.55}}
</style>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Inter:wght@400;500;600&display=swap">
<div class="wrap">
 <h1>Option Premium Inversions &mdash; S&amp;P 500 lows</h1>
 <p class="sub">Strikes where the normal premium ladder is violated, on live Webull quotes ({ASOF}). <b>Calls</b>: a higher strike whose bid tops the adjacent lower strike. <b>Puts</b>: a lower strike whose bid tops the adjacent higher strike. Only the richer leg with <b>bid &gt; 0 and open interest &gt; 0</b> is kept (dead one-sided quotes filtered out). Ranked by the bid gap. Live Webull data; not investment advice.</p>
 <div class="tiles">
  <div class="tile"><div class="l">Inversions</div><div class="v">{len(rows)}</div></div>
  <div class="tile"><div class="l">Calls</div><div class="v">{ncall}</div></div>
  <div class="tile"><div class="l">Puts</div><div class="v">{nput}</div></div>
  <div class="tile"><div class="l">Names</div><div class="v">{nnames}</div></div>
 </div>
 <div class="controls">
  <input id="q" type="text" placeholder="Filter ticker">
  <select id="t"><option value="">calls + puts</option><option value="CALL">calls only</option><option value="PUT">puts only</option></select>
 </div>
 <div class="tablewrap"><table><thead><tr>
  <th class="l">Ticker</th><th class="l">Expiry</th><th>DTE</th><th class="l">Type</th>
  <th>Rich strike</th><th>Rich bid</th><th>Rich mid</th><th>OI</th><th>IV</th>
  <th>vs strike</th><th>vs bid</th><th>Gap</th></tr></thead><tbody id="b">{trs}</tbody></table></div>
 <div class="meth"><p>An inversion often means a stale or mispriced wing quote rather than a free lunch &mdash; always check the live two-sided market and spreads before acting. Rich bid/mid are per share (&times;100 = per contract).</p></div>
</div>
<script>
 const b=document.getElementById('b'),q=document.getElementById('q'),t=document.getElementById('t');
 function f(){{const qq=q.value.trim().toUpperCase(),tt=t.value;[...b.rows].forEach(r=>{{const sym=r.cells[0].textContent.toUpperCase();r.style.display=((!qq||sym.includes(qq))&&(!tt||r.dataset.t===tt))?'':'none';}});}}
 q.addEventListener('input',f);t.addEventListener('change',f);
</script>"""

open(os.path.join(OUT, "inversions.html"), "w", encoding="utf-8").write(HTML)
print("WROTE inversions.html", len(HTML), "bytes")
print("inversions:", len(rows), " calls:", ncall, " puts:", nput, " names:", nnames,
      " | contracts seen:", len(contracts), " quotes seen:", len(quotes))
for r in rows[:8]:
    print(f"  {r['u']} {r['exp']} {r['type']} K{r['rich']} bid {r['richbid']} vs K{r['ref']} bid {r['refbid']} gap {r['gap']}")
