#!/usr/bin/env python3
"""
Build a self-contained CPO comps dashboard from the per-VIN inventory history.

Reads data/inventory_history.json (maintained by snapshot_inventory.py) and
emits a single standalone HTML file with the data embedded, rendering the
negotiation comps dashboard client-side: KPIs, a price-vs-mileage scatter with
a fitted market line, a value-ladder by model year, a deal-ranked comps table
(with days-on-lot and price-drop pulled from the history), and the accruing
per-trim price trend.

By default it writes a full standalone HTML document to docs/index.html, which
GitHub Pages serves live (the alert workflow rebuilds and commits it every run,
so the hosted page stays current with no manual step). Pass --artifact-out to
also emit a body-fragment (no <html>/<head>/<body> wrappers) suitable for
publishing as a Claude Artifact.

Run snapshot_inventory.py first (it refreshes the history and the trend point),
then this. Usage:
  python3 build_dashboard.py                          # -> docs/index.html (Pages)
  python3 build_dashboard.py --artifact-out ../dashboard.html   # + artifact fragment
"""
import argparse
import datetime as dt
import json
import os

# Wraps the body-fragment TEMPLATE into a full document for direct hosting.
PAGE_OPEN = (
    '<!doctype html>\n<html lang="en">\n<head>\n'
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    '<meta name="robots" content="noindex">\n'
    '<title>Outback CPO — Negotiation Comps</title>\n'
    '<style>*{box-sizing:border-box}html,body{margin:0;padding:0}</style>\n'
    '</head>\n<body>\n'
)
PAGE_CLOSE = '\n</body>\n</html>\n'


def days_between(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def comp_rows(history, today, year, trims):
    """Flatten the per-VIN history into active, priced comp rows for the
    dashboard, restricted to the target model year and watched trims, and
    computing days-on-lot and price-drop that a single snapshot can't carry."""
    trims = set(trims)
    rows = []
    for e in history["vehicles"].values():
        if not e.get("active"):
            continue
        if e.get("year") != year or e.get("trim") not in trims:
            continue
        price = e.get("currentPrice") or 0
        if price <= 0 or e.get("currentMileage") is None:
            continue
        ph = e.get("priceHistory") or []
        first_price = ph[0]["price"] if ph else price
        rows.append({
            "vin": e["vin"], "year": e["year"], "trim": e["trim"],
            "price": price, "mileage": e["currentMileage"],
            "dealer": e["dealer"], "distance": e.get("distance"),
            "driveText": e.get("driveText"), "color": e.get("color"),
            "url": e.get("url"), "sticker": e.get("sticker"),
            "daysOnLot": days_between(e["firstSeen"], today),
            "priceDrop": price - first_price,          # negative == dealer cut price
            "priceDropCount": max(len(ph) - 1, 0),
        })
    return rows


def flatten_trend(history):
    """meta.trend {trim: [{date,n,median,min,max}]} -> flat rows the client uses."""
    out = []
    for trim, series in history["meta"]["trend"].items():
        for p in series:
            out.append({"date": p["date"], "trim": trim, "median_price": p["median"],
                        "n": p["n"], "min_price": p.get("min"), "max_price": p.get("max")})
    return out


TEMPLATE = r"""<title>Outback CPO — Negotiation Comps</title>
<style>
  .comps-root {
    color-scheme: light;
    --surface-0: #eef1f5;   /* page ground */
    --surface-1: #ffffff;   /* card / chart surface */
    --surface-2: #f5f7fa;   /* inset */
    --border:    #d7dde6;
    --text-1:    #10151c;
    --text-2:    #4a5563;
    --text-3:    #8a95a3;
    --accent:    #1f6feb;   /* subaru-ish signal blue */
    --accent2:   #e0602a;   /* second trend line (Limited XT) */
    --good:      #128a5f;   /* below market — good for the buyer */
    --bad:       #d23b3b;   /* above market */
    --grid:      #eaeef3;
    /* model-year sequential blue ramp (light->dark == older->newer) */
    --yr-1: #86b6ef; --yr-2: #5598e7; --yr-3: #3987e5;
    --yr-4: #256abf; --yr-5: #184f95; --yr-6: #0d366b;
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    --mono: ui-monospace, "SF Mono", "Cascadia Code", Menlo, Consolas, monospace;
    font-family: var(--font);
    color: var(--text-1);
    background: var(--surface-0);
    line-height: 1.45;
    -webkit-font-smoothing: antialiased;
    min-height: 100vh;
    padding: clamp(16px, 3vw, 40px);
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) .comps-root {
      color-scheme: dark;
      --surface-0: #0c0f14; --surface-1: #141922; --surface-2: #1b212c;
      --border: #29313d; --text-1: #f2f5f8; --text-2: #aeb8c4; --text-3: #6f7987;
      --accent: #4b93ff; --accent2: #ef8a5c; --good: #2ec27e; --bad: #f2635f; --grid: #202632;
      --yr-1: #9ec5f4; --yr-2: #6da7ec; --yr-3: #3987e5;
      --yr-4: #256abf; --yr-5: #1c5cab; --yr-6: #104281;
    }
  }
  :root[data-theme="dark"] .comps-root {
    color-scheme: dark;
    --surface-0: #0c0f14; --surface-1: #141922; --surface-2: #1b212c;
    --border: #29313d; --text-1: #f2f5f8; --text-2: #aeb8c4; --text-3: #6f7987;
    --accent: #4b93ff; --good: #2ec27e; --bad: #f2635f; --grid: #202632;
    --yr-1: #9ec5f4; --yr-2: #6da7ec; --yr-3: #3987e5;
    --yr-4: #256abf; --yr-5: #1c5cab; --yr-6: #104281;
  }
  .comps-root * { box-sizing: border-box; }
  .wrap { max-width: 1180px; margin: 0 auto; display: flex; flex-direction: column; gap: 20px; }

  header.hd { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 16px; }
  .hd h1 { font-size: clamp(20px, 2.6vw, 30px); font-weight: 700; letter-spacing: -0.02em; margin: 0; text-wrap: balance; }
  .hd .sub { color: var(--text-2); font-size: 13.5px; }
  .hd .sub b { color: var(--text-1); font-weight: 600; }

  .card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 18px 20px; }
  .card > h2 { margin: 0 0 2px; font-size: 15px; font-weight: 650; letter-spacing: -0.01em; }
  .card > .cap { margin: 0 0 14px; color: var(--text-3); font-size: 12.5px; }

  /* controls */
  .controls { display: flex; flex-wrap: wrap; gap: 14px 20px; align-items: flex-end; }
  .ctl { display: flex; flex-direction: column; gap: 5px; }
  .ctl label { font-size: 11px; font-weight: 650; letter-spacing: .05em; text-transform: uppercase; color: var(--text-3); }
  .ctl select, .ctl input[type=number] {
    font: inherit; font-size: 14px; color: var(--text-1); background: var(--surface-2);
    border: 1px solid var(--border); border-radius: 8px; padding: 7px 9px; min-width: 120px;
  }
  .ctl .rangeval { font-variant-numeric: tabular-nums; font-size: 12.5px; color: var(--text-2); }
  .ctl input[type=range] { width: 170px; accent-color: var(--accent); }
  button.reset { font: inherit; font-size: 13px; color: var(--text-2); background: var(--surface-2);
    border: 1px solid var(--border); border-radius: 8px; padding: 7px 12px; cursor: pointer; }
  button.reset:hover { color: var(--text-1); border-color: var(--text-3); }

  /* kpis */
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
  .kpi { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
  .kpi .k { font-size: 11px; font-weight: 650; letter-spacing: .05em; text-transform: uppercase; color: var(--text-3); }
  .kpi .v { font-size: clamp(20px, 2.4vw, 27px); font-weight: 700; letter-spacing: -0.02em; margin-top: 3px;
    font-variant-numeric: tabular-nums; }
  .kpi .v small { font-size: 14px; font-weight: 600; color: var(--text-2); }
  .kpi .note { font-size: 12px; color: var(--text-2); margin-top: 2px; }

  .grid2 { display: grid; grid-template-columns: 1.35fr 1fr; gap: 20px; }
  @media (max-width: 820px) { .grid2 { grid-template-columns: 1fr; } }

  svg { display: block; width: 100%; height: auto; overflow: visible; }
  .axis line, .axis path { stroke: var(--grid); }
  .axis text { fill: var(--text-3); font-size: 11px; }
  .gridline { stroke: var(--grid); stroke-width: 1; }
  .marketline { stroke: var(--accent); stroke-width: 2; stroke-dasharray: 5 4; fill: none; }
  .dot { stroke: var(--surface-1); stroke-width: 1.5; cursor: pointer; transition: r .1s; }
  .dot.hl { stroke: var(--text-1); stroke-width: 2; }
  .bar { rx: 3; }
  .barlabel { fill: var(--text-2); font-size: 11px; font-variant-numeric: tabular-nums; }
  .trendline { fill: none; stroke: var(--accent); stroke-width: 2; }
  .trenddot { fill: var(--accent); stroke: var(--surface-1); stroke-width: 1.5; }

  .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 12px; font-size: 12px; color: var(--text-2); }
  .legend .it { display: inline-flex; align-items: center; gap: 6px; }
  .legend .sw { width: 11px; height: 11px; border-radius: 3px; display: inline-block; }

  /* table */
  .tablewrap { overflow-x: auto; }
  table.comps { width: 100%; border-collapse: collapse; font-size: 13px; }
  table.comps th, table.comps td { padding: 8px 10px; text-align: left; white-space: nowrap; border-bottom: 1px solid var(--border); }
  table.comps th { font-size: 11px; letter-spacing: .04em; text-transform: uppercase; color: var(--text-3);
    font-weight: 650; cursor: pointer; user-select: none; position: sticky; top: 0; background: var(--surface-1); }
  table.comps th.num, table.comps td.num { text-align: right; font-variant-numeric: tabular-nums; }
  table.comps th:hover { color: var(--text-1); }
  table.comps th .arr { color: var(--accent); }
  table.comps tbody tr:hover { background: var(--surface-2); }
  table.comps tr.target td { background: color-mix(in srgb, var(--accent) 10%, var(--surface-1)); }
  .delta { font-weight: 650; font-variant-numeric: tabular-nums; }
  .delta.good { color: var(--good); }
  .delta.bad { color: var(--bad); }
  .drop { font-size: 11px; font-weight: 650; font-variant-numeric: tabular-nums; margin-top: 1px; }
  .drop.cut { color: var(--good); }
  .drop.up { color: var(--bad); }
  .stale { color: var(--bad); font-weight: 650; }
  .freshness { display: flex; align-items: center; gap: 8px; font-size: 13px; border-radius: 10px; padding: 9px 13px; }
  .freshness .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex: none; }
  .freshness.ok { color: var(--text-2); background: var(--surface-1); border: 1px solid var(--border); }
  .freshness.ok .dot { background: var(--good); }
  .freshness.warn { color: var(--text-1); font-weight: 600;
    background: color-mix(in srgb, var(--bad) 13%, var(--surface-1));
    border: 1px solid color-mix(in srgb, var(--bad) 45%, var(--border)); }
  .freshness.warn .dot { background: var(--bad); }
  .freshness b { font-variant-numeric: tabular-nums; }
  .yrchip { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 6px; vertical-align: baseline; }
  a.lk { color: var(--accent); text-decoration: none; font-weight: 600; }
  a.lk:hover { text-decoration: underline; }
  td.dealer { white-space: normal; min-width: 150px; }

  /* tooltip */
  .tt { position: fixed; z-index: 50; pointer-events: none; background: var(--surface-1); color: var(--text-1);
    border: 1px solid var(--border); border-radius: 9px; padding: 9px 11px; font-size: 12.5px; line-height: 1.5;
    box-shadow: 0 6px 24px rgba(0,0,0,.18); max-width: 260px; opacity: 0; transition: opacity .08s; }
  .tt.on { opacity: 1; }
  .tt .ttt { font-weight: 700; margin-bottom: 2px; }
  .tt .ttd { color: var(--text-2); }
  .tt .ttv { font-variant-numeric: tabular-nums; }

  .foot { color: var(--text-3); font-size: 12px; text-align: center; padding-top: 6px; }
  .foot code { font-family: var(--mono); font-size: 11.5px; }
  .empty { color: var(--text-3); font-size: 13.5px; padding: 24px 0; text-align: center; }
</style>

<div class="comps-root">
<div class="wrap">
  <header class="hd">
    <h1>Outback CPO — Negotiation Comps</h1>
    <div class="sub">__SUBTITLE__</div>
  </header>

  <div id="freshness"></div>

  <div class="card">
    <div class="controls" id="controls">
      <div class="ctl">
        <label for="f-trim">Trim</label>
        <select id="f-trim"></select>
      </div>
      <div class="ctl">
        <label for="f-dist">Max drive distance <span class="rangeval" id="v-dist"></span></label>
        <input type="range" id="f-dist" min="20" max="300" step="10">
      </div>
      <div class="ctl">
        <label for="f-price">Max price <span class="rangeval" id="v-price"></span></label>
        <input type="range" id="f-price" min="0" max="60000" step="1000">
      </div>
      <button class="reset" id="reset">Reset</button>
    </div>
  </div>

  <div class="kpis" id="kpis"></div>

  <div class="grid2">
    <div class="card">
      <h2>Price vs. mileage</h2>
      <p class="cap" id="scatter-cap"></p>
      <div id="scatter"></div>
      <div class="legend" id="scatter-legend"></div>
    </div>
    <div class="card">
      <h2>Touring XT vs. Limited XT</h2>
      <p class="cap">Median asking price by trim — the premium for the top badge.</p>
      <div id="ladder"></div>
    </div>
  </div>

  <div class="card">
    <h2>Comps <span id="comps-count" style="color:var(--text-3);font-weight:500"></span></h2>
    <p class="cap">Ranked by deal delta — how far each listing sits above (<span style="color:var(--bad)">+</span>)
       or below (<span style="color:var(--good)">−</span>) the fitted market price for its mileage &amp; year.
       Click a header to re-sort.</p>
    <div class="tablewrap">
      <table class="comps">
        <thead><tr id="thead"></tr></thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>Market trend</h2>
    <p class="cap" id="trend-cap"></p>
    <div id="trend"></div>
  </div>

  <div class="foot">
    Comps are certified pre-owned only, pulled from subaru.com. Market line is an ordinary least-squares
    fit of price on mileage &amp; model year within the selected trim (shown when ≥ 6 comps). Days-on-lot and
    price changes accrue from the first snapshot forward — a car on the lot before tracking began shows its
    days since <b>__TRACKING_SINCE__</b>, not its true age. Refresh with
    <code>python3 scripts/snapshot_inventory.py &amp;&amp; python3 scripts/build_dashboard.py</code>.
  </div>
</div>
</div>

<div class="tt" id="tt"></div>

<script>
const DATA = __DATA__;
const HISTORY = __HISTORY__;
const META = __META__;

const $ = s => document.querySelector(s);
const fmt$ = n => '$' + Math.round(n).toLocaleString();
const fmtK = n => n.toLocaleString();
const YEAR = META.year;
const WATCH = META.watch || ['Touring XT', 'Limited XT'];

// ---- trim color (Touring = blue, Limited = orange); resolved to hex so SVG
//      presentation attributes work everywhere ----
const cssv = v => getComputedStyle($('.comps-root')).getPropertyValue(v).trim();
const TRIM_VAR = {'Touring XT':'--accent', 'Limited XT':'--accent2'};
const trimColor = t => cssv(TRIM_VAR[t] || '--accent');

// ---- OLS: price ~ 1 + mileage + year (drops year if only one present) ----
function fitMarket(rows) {
  const n = rows.length;
  if (n < 6) return null;
  const uy = [...new Set(rows.map(r=>r.year))];
  const ut = [...new Set(rows.map(r=>r.trim))];       // trim dummy so pooling
  const useYear = uy.length > 1, useTrim = ut.length > 1;  // Touring+Limited
  // design cols: 1, mileage, [year], [trim dummies vs ut[0]]
  const cols = r => {
    const c = [1, r.mileage];
    if (useYear) c.push(r.year);
    if (useTrim) for (let i=1;i<ut.length;i++) c.push(r.trim===ut[i] ? 1 : 0);
    return c;
  };
  const X = rows.map(cols), y = rows.map(r=>r.price), p = X[0].length;
  if (n < p + 1) return null;
  const XtX = Array.from({length:p}, ()=>new Array(p).fill(0));
  const Xty = new Array(p).fill(0);
  for (let i=0;i<n;i++){ for(let a=0;a<p;a++){ Xty[a]+=X[i][a]*y[i]; for(let b=0;b<p;b++) XtX[a][b]+=X[i][a]*X[i][b]; } }
  const b = gsolve(XtX, Xty);
  if (!b) return null;
  const pred = r => cols(r).reduce((s,v,i)=>s+v*b[i], 0);
  return { b, useYear, useTrim, ut, pred, perK: b[1]*1000, perYr: useYear ? b[2] : 0 };
}
function gsolve(A, bv){ // Gaussian elimination with partial pivoting
  const n=A.length; const M=A.map((r,i)=>[...r,bv[i]]);
  for(let c=0;c<n;c++){
    let piv=c; for(let r=c+1;r<n;r++) if(Math.abs(M[r][c])>Math.abs(M[piv][c])) piv=r;
    if(Math.abs(M[piv][c])<1e-12) return null;
    const t=M[c]; M[c]=M[piv]; M[piv]=t;
    const pv=M[c][c];
    for(let r=0;r<n;r++){ if(r===c) continue; const f=M[r][c]/pv; for(let k=c;k<=n;k++) M[r][k]-=f*M[c][k]; }
  }
  return M.map((r,i)=>r[n]/r[i]);
}

// ---- state / filters ----
const priced = DATA.filter(r => (r.price||0) > 0 && r.mileage != null);
const DIST_MAX = 300;                          // miles the buyer will travel
const maxPrice = Math.ceil(Math.max(...priced.map(r=>r.price))/1000)*1000;
const trimCounts = {};
priced.forEach(r => trimCounts[r.trim]=(trimCounts[r.trim]||0)+1);
const trimMatch = r => state.trim==='both' ? WATCH.includes(r.trim) : r.trim===state.trim;
const trimLabel = () => state.trim==='both' ? 'Touring XT + Limited XT' : state.trim;

const state = { trim: 'both', dist: DIST_MAX, price: maxPrice, sort:'delta', dir:1 };

function currentRows(){
  return priced.filter(r =>
    trimMatch(r) &&
    (r.distance==null || r.distance<=state.dist) &&
    r.price<=state.price);
}

// ---- controls setup ----
function buildControls(){
  const ts = $('#f-trim');
  const cBoth = (trimCounts['Touring XT']||0) + (trimCounts['Limited XT']||0);
  ts.innerHTML =
    `<option value="both">Both XT (${cBoth})</option>` +
    `<option value="Touring XT">Touring XT (${trimCounts['Touring XT']||0})</option>` +
    `<option value="Limited XT">Limited XT (${trimCounts['Limited XT']||0})</option>`;
  ts.value = state.trim;
  const fd=$('#f-dist'); fd.max=DIST_MAX; fd.value=state.dist;
  const fp=$('#f-price'); fp.max=maxPrice; fp.value=state.price;
  ts.onchange=()=>{state.trim=ts.value; render();};
  fd.oninput=()=>{state.dist=+fd.value; render();};
  fp.oninput=()=>{state.price=+fp.value; render();};
  $('#reset').onclick=()=>{ Object.assign(state,{trim:'both',dist:DIST_MAX,price:maxPrice});
    ts.value=state.trim; fd.value=state.dist; fp.value=state.price; render(); };
}

// ---- KPIs ----
function median(a){ const s=[...a].sort((x,y)=>x-y); const m=s.length>>1; return s.length%2?s[m]:(s[m-1]+s[m])/2; }
function renderKpis(rows, fit){
  const el=$('#kpis');
  if(!rows.length){ el.innerHTML=`<div class="kpi"><div class="k">Comps</div><div class="v">0</div><div class="note">No listings match these filters.</div></div>`; return; }
  const prices=rows.map(r=>r.price), miles=rows.map(r=>r.mileage);
  const cheapest=rows.reduce((a,b)=>b.price<a.price?b:a);
  const closest=rows.reduce((a,b)=>(b.distance??1e9)<(a.distance??1e9)?b:a);
  const tiles=[];
  tiles.push(['Comps', rows.length, trimLabel()]);
  tiles.push(['Median price', fmt$(median(prices)), `range ${fmt$(Math.min(...prices))}–${fmt$(Math.max(...prices))}`]);
  tiles.push(['Median mileage', fmtK(Math.round(median(miles)))+' mi', `${fmtK(Math.min(...miles))}–${fmtK(Math.max(...miles))} mi`]);
  tiles.push(['Cheapest', fmt$(cheapest.price), `${fmtK(cheapest.mileage)} mi · ${cheapest.dealer}`]);
  tiles.push(['Closest', (closest.distance!=null?closest.distance+' mi':'—'), `${fmt$(closest.price)} · ${closest.dealer}`]);
  const cuts=rows.filter(r=>r.priceDrop<0), ups=rows.filter(r=>r.priceDrop>0);
  const stale=rows.filter(r=>r.daysOnLot>=21).length;
  tiles.push(['Median days on lot', Math.round(median(rows.map(r=>r.daysOnLot)))+'<small> days</small>',
    stale? `${stale} sitting 21+ days` : 'none sitting 21+ days']);
  const moved=cuts.length+ups.length;
  const changeNote = moved
    ? `${cuts.length}↓ cut · ${ups.length}↑ raised` + (cuts.length?` · deepest −${fmt$(Math.max(...cuts.map(c=>-c.priceDrop)))}`:'')
    : 'none yet — accrues over time';
  tiles.push(['Price changes', moved, changeNote]);
  if(fit){
    tiles.push(['Depreciation', '−'+fmt$(Math.abs(fit.perK)), 'per 1,000 mi']);
  }
  el.innerHTML=tiles.map(([k,v,note])=>`<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="note">${note}</div></div>`).join('');
}

// ---- scatter ----
function renderScatter(rows, fit){
  const host=$('#scatter'); const cap=$('#scatter-cap');
  const W=560,H=340, m={t:14,r:16,b:44,l:64};
  if(rows.length<2){ host.innerHTML=`<div class="empty">Need at least 2 comps to plot.</div>`; cap.textContent=''; $('#scatter-legend').innerHTML=''; return; }
  const xs=rows.map(r=>r.mileage), ys=rows.map(r=>r.price);
  let x0=Math.min(...xs), x1=Math.max(...xs), y0=Math.min(...ys), y1=Math.max(...ys);
  const xp=(x1-x0||1)*0.06, yp=(y1-y0||1)*0.08; x0-=xp;x1+=xp;y0-=yp;y1+=yp;
  x0=Math.max(0,x0); y0=Math.max(0,y0);
  const px=v=>m.l+(v-x0)/(x1-x0)*(W-m.l-m.r);
  const py=v=>H-m.b-(v-y0)/(y1-y0)*(H-m.t-m.b);
  const xticks=niceTicks(x0,x1,5), yticks=niceTicks(y0,y1,5);
  const xdec = (x1-x0) < 8000 ? 1 : 0;   // narrow mileage band -> show a decimal
  let s=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Price versus mileage scatter">`;
  s+=`<g class="axis">`;
  yticks.forEach(t=>{ s+=`<line class="gridline" x1="${m.l}" x2="${W-m.r}" y1="${py(t)}" y2="${py(t)}"/>`;
    s+=`<text x="${m.l-8}" y="${py(t)+4}" text-anchor="end">${'$'+(t/1000).toFixed(0)+'k'}</text>`; });
  xticks.forEach(t=>{ s+=`<text x="${px(t)}" y="${H-m.b+18}" text-anchor="middle">${(t/1000).toFixed(xdec)+'k'}</text>`; });
  s+=`<text x="${(m.l+W-m.r)/2}" y="${H-4}" text-anchor="middle" fill="var(--text-3)">Mileage</text>`;
  s+=`</g>`;
  // market line(s) — one per trim, each in that trim's color, so each price
  // tier gets its own reference (Limited XT isn't wrongly flagged "below market")
  if(fit){
    const lineTrims = fit.useTrim ? fit.ut : [rows[0].trim];
    lineTrims.forEach(tr=>{
      const ly0=fit.pred({mileage:x0, trim:tr});
      const ly1=fit.pred({mileage:x1, trim:tr});
      s+=`<line class="marketline" x1="${px(x0)}" y1="${py(ly0)}" x2="${px(x1)}" y2="${py(ly1)}" style="stroke:${trimColor(tr)}"/>`;
    });
    cap.innerHTML = `Dashed line${fit.useTrim?'s (one per trim)':''} = fitted market price. Points below are priced under market.`;
  } else {
    cap.textContent = `Only ${rows.length} comp${rows.length===1?'':'s'} so far — too few to fit a market line yet (accrues over time).`;
  }
  // dots colored by trim
  rows.forEach((r,i)=>{
    s+=`<circle class="dot" data-i="${i}" cx="${px(r.mileage)}" cy="${py(r.price)}" r="6.5" fill="${trimColor(r.trim)}"/>`;
  });
  s+=`</svg>`;
  host.innerHTML=s;
  // legend by trim present
  const tl=[...new Set(rows.map(r=>r.trim))];
  $('#scatter-legend').innerHTML = tl.map(t=>`<span class="it"><span class="sw" style="background:${trimColor(t)}"></span>${t}</span>`).join('');
  // hover
  host.querySelectorAll('.dot').forEach(c=>{
    c.addEventListener('mousemove',e=>{ const r=rows[+c.dataset.i]; const d=fit?r.price-fit.pred(r):null;
      showTip(e, `<div class="ttt">${r.year} ${r.trim}</div>`+
        `<div class="ttv">${fmt$(r.price)} · ${fmtK(r.mileage)} mi</div>`+
        `<div class="ttd">${r.dealer}</div>`+
        `<div class="ttd">${r.distance!=null?r.distance+' mi · '+(r.driveText||''):''}</div>`+
        `<div class="ttd">${r.daysOnLot}d on lot${r.priceDrop<0?` · ↓${fmt$(-r.priceDrop)}`:r.priceDrop>0?` · ↑${fmt$(r.priceDrop)}`:''}</div>`+
        (d!=null?`<div class="ttv" style="color:${d<=0?'var(--good)':'var(--bad)'}">${d<=0?'−':'+'}${fmt$(Math.abs(d))} vs market</div>`:''));
      c.setAttribute('r','8'); c.classList.add('hl'); });
    c.addEventListener('mouseleave',()=>{ hideTip(); c.setAttribute('r','6.5'); c.classList.remove('hl'); });
  });
}

// ---- value ladder ----
function renderLadder(){
  const host=$('#ladder');
  // Always both trims for comparison, regardless of the trim filter.
  const meds=WATCH.map(t=>{
    const ps=priced.filter(r=>r.trim===t).map(r=>r.price);
    return ps.length ? {t, m:median(ps), n:ps.length} : null;
  }).filter(Boolean);
  if(!meds.length){ host.innerHTML=`<div class="empty">No ${YEAR} XT comps listed yet.</div>`; return; }
  const W=440, rowH=52, H=meds.length*rowH+20, m={l:96,r:78,t:8,b:8};
  const maxV=Math.max(...meds.map(d=>d.m));
  const bw=W-m.l-m.r;
  let s=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Median price by trim">`;
  meds.forEach((d,i)=>{
    const y=m.t+i*rowH+8, h=rowH-22, w=Math.max(2,d.m/maxV*bw);
    s+=`<text x="${m.l-10}" y="${y+h/2+4}" text-anchor="end" class="barlabel" style="fill:var(--text-1);font-weight:650">${d.t}</text>`;
    s+=`<rect class="bar" x="${m.l}" y="${y}" width="${w}" height="${h}" rx="3" fill="${trimColor(d.t)}"/>`;
    s+=`<text x="${m.l+w+8}" y="${y+h/2+4}" class="barlabel" style="fill:var(--text-1);font-weight:650">${fmt$(d.m)}</text>`;
    s+=`<text x="${m.l+w+8}" y="${y+h/2+4}" dx="${(fmt$(d.m).length)*7.4+6}" class="barlabel">n=${d.n}</text>`;
  });
  if(meds.length===2){
    const gap=Math.abs(meds[0].m-meds[1].m);
    s+=`<text x="${m.l}" y="${H-2}" class="barlabel">Touring commands ${fmt$(gap)} over Limited</text>`;
  }
  s+=`</svg>`;
  host.innerHTML=s;
}

// ---- comps table ----
const COLS=[
  {k:'delta', label:'Δ market', num:true},
  {k:'trim', label:'Trim', num:false},
  {k:'price', label:'Price', num:true},
  {k:'mileage', label:'Mileage', num:true},
  {k:'daysOnLot', label:'Days', num:true},
  {k:'distance', label:'Dist', num:true},
  {k:'dealer', label:'Dealer', num:false},
  {k:'links', label:'', num:false},
];
function renderTable(rows, fit){
  $('#comps-count').textContent = `· ${rows.length} listing${rows.length===1?'':'s'}`;
  const withDelta = rows.map(r=>({...r, delta: fit? r.price-fit.pred(r): null}));
  const dir=state.dir, key=state.sort;
  withDelta.sort((a,b)=>{
    let av=a[key], bv=b[key];
    if(key==='links'){av=0;bv=0;}
    if(key==='delta' && av==null) av=Infinity;
    if(key==='delta' && bv==null) bv=Infinity;
    if(typeof av==='string') return dir*av.localeCompare(bv);
    return dir*((av??Infinity)-(bv??Infinity));
  });
  $('#thead').innerHTML = COLS.map(c=>{
    const arr = state.sort===c.k ? `<span class="arr">${state.dir>0?'▲':'▼'}</span>` : '';
    return `<th class="${c.num?'num':''}" data-k="${c.k}">${c.label} ${arr}</th>`;
  }).join('');
  $('#thead').querySelectorAll('th').forEach(th=>{
    th.onclick=()=>{ const k=th.dataset.k; if(k==='links') return;
      if(state.sort===k) state.dir*=-1; else { state.sort=k; state.dir = (k==='trim'||k==='dealer')?1:1; }
      render(); };
  });
  if(!withDelta.length){ $('#tbody').innerHTML=`<tr><td colspan="${COLS.length}"><div class="empty">No listings match these filters.</div></td></tr>`; return; }
  const cheapest=Math.min(...withDelta.map(r=>r.price));
  $('#tbody').innerHTML = withDelta.map(r=>{
    let deltaCell='<span class="delta" style="color:var(--text-3)">—</span>';
    if(r.delta!=null){ const good=r.delta<=0; deltaCell=`<span class="delta ${good?'good':'bad'}">${good?'−':'+'}${fmt$(Math.abs(r.delta))}</span>`; }
    const links=`<a class="lk" href="${r.url}" target="_blank" rel="noopener">listing</a>`+
                (r.sticker?` · <a class="lk" href="${r.sticker}" target="_blank" rel="noopener">sticker</a>`:'');
    const chTitle = `${r.priceDropCount} price change${r.priceDropCount===1?'':'s'} since first seen (${r.daysOnLot}d ago)`;
    const drop = r.priceDrop<0
      ? `<div class="drop cut" title="${chTitle}">↓${fmt$(-r.priceDrop)}</div>`
      : r.priceDrop>0
      ? `<div class="drop up" title="${chTitle}">↑${fmt$(r.priceDrop)}</div>` : '';
    const days = r.daysOnLot>=21 ? `<span class="stale">${r.daysOnLot}d</span>`
               : `${r.daysOnLot}d`;
    return `<tr class="${r.price===cheapest?'target':''}">
      <td class="num">${deltaCell}</td>
      <td><span class="yrchip" style="background:${trimColor(r.trim)}"></span>${r.trim}</td>
      <td class="num">${fmt$(r.price)}${drop}</td>
      <td class="num">${fmtK(r.mileage)}</td>
      <td class="num">${days}</td>
      <td class="num">${r.distance!=null?r.distance:'—'}</td>
      <td class="dealer">${r.dealer}${r.driveText?`<br><span style="color:var(--text-3);font-size:11.5px">~${r.driveText}</span>`:''}</td>
      <td>${links}</td>
    </tr>`;
  }).join('');
}

// ---- trend (one line per selected trim; "both" overlays two) ----
function renderTrend(){
  const host=$('#trend'); const cap=$('#trend-cap');
  const trimsSel = state.trim==='both' ? WATCH : [state.trim];
  const seriesByKey = trimsSel
    .map(t=>({key:t, pts: HISTORY.filter(h=>h.trim===`${YEAR} ${t}`).sort((a,b)=>a.date<b.date?-1:1)}))
    .filter(s=>s.pts.length);
  const maxLen = Math.max(0, ...seriesByKey.map(s=>s.pts.length));
  if(maxLen<=1){
    const latest = seriesByKey.map(s=>`${s.key}: ${s.pts.length?fmt$(s.pts[s.pts.length-1].median_price):'—'}`).join(' · ');
    cap.innerHTML = `The trend accrues each time a snapshot runs. Latest median — ${latest || 'no data yet'}.`;
    host.innerHTML = `<div class="empty">Trend chart appears once there are ≥ 2 daily snapshots.</div>`;
    return;
  }
  cap.textContent = `Median asking price over time — ${trimLabel()}.`;
  const allDates=[...new Set([].concat(...seriesByKey.map(s=>s.pts.map(p=>p.date))))].sort();
  const W=900,H=240,m={t:16,r:18,b:34,l:64};
  const allY=[].concat(...seriesByKey.map(s=>s.pts.map(p=>p.median_price)));
  let y0=Math.min(...allY), y1=Math.max(...allY); const yp=(y1-y0||1)*0.15; y0-=yp;y1+=yp;
  const xi=d=>allDates.indexOf(d);
  const px=i=>m.l+(allDates.length<=1?0.5:i/(allDates.length-1))*(W-m.l-m.r);
  const py=v=>H-m.b-(v-y0)/(y1-y0)*(H-m.t-m.b);
  const yticks=niceTicks(y0,y1,4);
  let s=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Median price trend"><g class="axis">`;
  yticks.forEach(t=>{ s+=`<line class="gridline" x1="${m.l}" x2="${W-m.r}" y1="${py(t)}" y2="${py(t)}"/><text x="${m.l-8}" y="${py(t)+4}" text-anchor="end">$${(t/1000).toFixed(0)}k</text>`; });
  allDates.forEach((d,i)=>{ s+=`<text x="${px(i)}" y="${H-m.b+18}" text-anchor="middle">${d.slice(5)}</text>`; });
  s+=`</g>`;
  seriesByKey.forEach(ser=>{
    const col=trimColor(ser.key);
    s+=`<path fill="none" stroke="${col}" stroke-width="2" d="${ser.pts.map((p,j)=>(j?'L':'M')+px(xi(p.date))+' '+py(p.median_price)).join(' ')}"/>`;
    ser.pts.forEach(p=> s+=`<circle cx="${px(xi(p.date))}" cy="${py(p.median_price)}" r="4" fill="${col}" stroke="var(--surface-1)" stroke-width="1.5"/>`);
  });
  s+=`</svg>`;
  host.innerHTML=s;
  if(seriesByKey.length>1){
    host.innerHTML += `<div class="legend">` +
      seriesByKey.map(ser=>`<span class="it"><span class="sw" style="background:${trimColor(ser.key)}"></span>${ser.key}</span>`).join('') +
      `</div>`;
  }
}

// ---- helpers ----
function niceTicks(lo,hi,n){ const span=hi-lo||1; const step0=span/n; const mag=Math.pow(10,Math.floor(Math.log10(step0)));
  const norm=step0/mag; const step=(norm<1.5?1:norm<3?2:norm<7?5:10)*mag;
  const start=Math.ceil(lo/step)*step; const out=[]; for(let v=start;v<=hi+1e-9;v+=step) out.push(v); return out; }
const tt=$('#tt');
function showTip(e,html){ tt.innerHTML=html; tt.classList.add('on');
  let x=e.clientX+14, y=e.clientY+14; const r=tt.getBoundingClientRect();
  if(x+r.width>innerWidth-8) x=e.clientX-r.width-14; if(y+r.height>innerHeight-8) y=e.clientY-r.height-14;
  tt.style.left=x+'px'; tt.style.top=y+'px'; }
function hideTip(){ tt.classList.remove('on'); }

// ---- freshness (compares last snapshot to the viewer's live clock, so it
//      still flags staleness even if the whole pipeline has stopped) ----
function renderFreshness(){
  const el=$('#freshness'); const snap=META.date;
  if(!snap){ el.className=''; el.innerHTML=''; return; }
  const days=Math.floor((Date.now()-Date.parse(snap+'T12:00:00'))/86400000);
  if(days<=1){
    const when=days<=0?'today':'yesterday';
    el.className='freshness ok';
    el.innerHTML=`<span class="dot"></span><span>Data current — last updated <b>${snap}</b> (${when}).</span>`;
  } else {
    el.className='freshness warn';
    el.innerHTML=`<span class="dot"></span><span>⚠ Data may be stale — last successful update was <b>${snap}</b>, `+
      `<b>${days}</b> days ago. The scraper looks like it has stopped or its API calls are failing; `+
      `numbers below are frozen as of that date.</span>`;
  }
}

// ---- master render ----
function render(){
  renderFreshness();
  const rows=currentRows();
  const fit=fitMarket(rows);
  renderKpis(rows,fit);
  renderScatter(rows,fit);
  renderLadder();
  renderTable(rows,fit);
  renderTrend();
  $('#v-dist').textContent = state.dist>=DIST_MAX? 'any' : '≤ '+state.dist+' mi';
  $('#v-price').textContent = state.price>=maxPrice? 'any' : '≤ '+fmt$(state.price);
}
buildControls();
render();
</script>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--history", default=os.path.join(here, "..", "data", "inventory_history.json"))
    ap.add_argument("--out", default=os.path.join(here, "..", "docs", "index.html"),
                    help="full standalone page for GitHub Pages (default: docs/index.html)")
    ap.add_argument("--artifact-out", default=None,
                    help="also write a body-fragment here for publishing as a Claude Artifact")
    ap.add_argument("--year", type=int, default=2026, help="model year to focus on")
    ap.add_argument("--watch-trims", nargs="+", default=["Touring XT", "Limited XT"])
    args = ap.parse_args()

    with open(args.history) as f:
        history = json.load(f)
    hmeta = history.get("meta", {})
    zipc, radius = hmeta.get("zip", "44107"), hmeta.get("radius", 200)
    today = dt.date.today().isoformat()

    rows = comp_rows(history, today, args.year, args.watch_trims)
    trend = flatten_trend(history)
    tracking_since = hmeta.get("trackingSince", today)
    last_snap = hmeta.get("lastSnapshot", today)

    subtitle = (f"<b>{args.year}</b> {' / '.join(args.watch_trims)} · CPO within <b>{radius} mi</b> of <b>{zipc}</b> · "
                f"<b>{len(rows)}</b> live · snapshot <b>{last_snap}</b> · tracking since <b>{tracking_since}</b>")
    meta = {"year": args.year, "watch": args.watch_trims, "zip": zipc, "radius": radius,
            "date": last_snap, "trackingSince": tracking_since}

    fragment = (TEMPLATE
                .replace("__DATA__", json.dumps(rows))
                .replace("__HISTORY__", json.dumps(trend))
                .replace("__META__", json.dumps(meta))
                .replace("__SUBTITLE__", subtitle)
                .replace("__TRACKING_SINCE__", tracking_since))

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    page = PAGE_OPEN + fragment + PAGE_CLOSE
    with open(args.out, "w") as f:
        f.write(page)
    print(f"Wrote Pages document to {args.out} ({len(page):,} bytes, {len(rows)} active vehicles embedded)")

    if args.artifact_out:
        with open(args.artifact_out, "w") as f:
            f.write(fragment)
        print(f"Wrote artifact fragment to {args.artifact_out} ({len(fragment):,} bytes)")


if __name__ == "__main__":
    main()
