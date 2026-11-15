"""Built-in market scanner. Mirrors the workflow used with Claude's trading skills:
trend filter (above 50/200-day averages, not over-extended) -> fundamentals (FinViz) ->
earnings-date check -> sector strength -> trade plan with stop/targets -> model odds.
Also runs the keyless market-breadth / uptrend / sector skills for a market posture read.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import data, forecast

SKILLS = os.path.expanduser("~/.claude/skills")
CACHE = os.path.expanduser("~/.cache/papertrader")


def _run_skill(cmd: list[str], pattern: str, max_age_h: float = 6) -> dict | None:
    os.makedirs(CACHE, exist_ok=True)
    files = sorted(glob.glob(os.path.join(CACHE, pattern)), key=os.path.getmtime)
    if files and (dt.datetime.now().timestamp() - os.path.getmtime(files[-1])) < max_age_h * 3600:
        return json.load(open(files[-1]))
    try:
        subprocess.run(cmd, cwd=CACHE, capture_output=True, timeout=180, check=False)
    except Exception:
        return None
    files = sorted(glob.glob(os.path.join(CACHE, pattern)), key=os.path.getmtime)
    return json.load(open(files[-1])) if files else None


def market_posture(force: bool = False) -> dict:
    """Composite read from market-breadth-analyzer, uptrend-analyzer and sector-analyst (all keyless)."""
    age = 0 if force else 6
    out = {"breadth": None, "uptrend": None, "sectors": {}, "label": "Unknown", "ceiling_pct": None, "detail": ""}
    b = _run_skill(["python3", f"{SKILLS}/market-breadth-analyzer/scripts/market_breadth_analyzer.py",
                    "--detail-url", "https://tradermonty.github.io/market-breadth-analysis/market_breadth_data.csv",
                    "--summary-url", "https://tradermonty.github.io/market-breadth-analysis/market_breadth_summary.csv",
                    "--output-dir", "."], "market_breadth_2*.json", age)
    u = _run_skill(["python3", f"{SKILLS}/uptrend-analyzer/scripts/uptrend_analyzer.py", "--output-dir", "."],
                   "uptrend_analysis_*.json", age)
    s = _run_skill(["python3", f"{SKILLS}/sector-analyst/scripts/analyze_sector_rotation.py", "--json", "--save",
                    "--output-dir", "."], "sector_rotation_*.json", age)
    if b:
        out["breadth"] = b["composite"]["composite_score"]
        out["breadth_zone"] = b["composite"]["zone"]
    if u:
        out["uptrend"] = u["composite"]["composite_score"]
        out["uptrend_zone"] = u["composite"]["zone"]
    if s:
        out["sectors"] = {r["sector"]: r for r in s.get("ranking", [])}
    scores = [x for x in (out["breadth"], out["uptrend"]) if x is not None]
    if scores:
        avg = sum(scores) / len(scores)
        # Same mapping exposure-coach uses (without its missing-input haircut).
        if avg >= 65:
            out["label"], out["ceiling_pct"] = "Risk-on", min(100, int(70 + (avg - 65) * 1.3))
        elif avg >= 50:
            out["label"], out["ceiling_pct"] = "Selective", int(50 + (avg - 50) * 1.3)
        elif avg >= 35:
            out["label"], out["ceiling_pct"] = "Cautious", int(30 + (avg - 35) * 1.3)
        else:
            out["label"], out["ceiling_pct"] = "Defensive", int(10 + max(0, avg - 20) * 1.3)
        out["detail"] = (f"Breadth {out['breadth']:.0f} ({out.get('breadth_zone', '')}) · "
                         f"Uptrend {out['uptrend']:.0f} ({out.get('uptrend_zone', '')})") if len(scores) == 2 else ""
    return out


def _tech(sym: str) -> dict | None:
    try:
        b = data.history(sym, "1y", "1d", ttl=900)
    except Exception:
        return None
    c, h, l, v = b.c, b.h, b.l, b.v
    if len(c) < 210:
        return None
    px = float(c[-1])
    s20, s50, s200 = forecast.sma(c, 20), forecast.sma(c, 50), forecast.sma(c, 200)
    a = forecast.atr(h, l, c)
    r = forecast.rsi(c)
    avgvol = float(np.mean(v[-50:]))
    mom3 = px / float(c[-63]) - 1
    ext = px / s50 - 1
    d = {"symbol": sym, "name": b.name, "price": px, "prev": float(c[-2]), "sma20": s20, "sma50": s50,
         "sma200": s200, "atr": a, "rsi": r, "avgvol": avgvol, "mom3": mom3, "ext50": ext,
         "lo10": float(np.min(l[-10:])), "lo20": float(np.min(l[-20:])), "hi20": float(np.max(h[-20:])),
         "hi60": float(np.max(h[-60:])), "hi1y": float(np.max(h)), "closes": c}
    ok = (px > 5 and avgvol > 1e6 and px > s50 and px > s200 and s50 > s200 * 0.98 and 38 <= r <= 72
          and ext < 0.12 and mom3 > -0.02)
    if not ok:
        return None
    # Technical score: trend quality, momentum, not too extended, near highs.
    d["tscore"] = (min(mom3, 0.3) * 100 + (px / s200 - 1) * 40 - max(0, ext - 0.06) * 200
                   - abs(r - 58) * 0.4 + (1 - (d["hi1y"] - px) / d["hi1y"]) * 15)
    return d


def _plan(d: dict) -> dict:
    px, a = d["price"], d["atr"]
    entry = round(min(px, max(d["sma20"], px - 0.75 * a)), 2)
    stop = round(min(d["lo10"], entry - 1.5 * a) - 0.15 * a, 2)
    stop = round(max(stop, entry - 3.5 * a), 2)
    risk = entry - stop
    t1 = round(max(d["hi20"], entry + 1.6 * risk), 2)
    t2 = round(max(d["hi1y"] if d["hi1y"] > t1 * 1.01 else 0, entry + 3.0 * risk), 2)
    return {"entry": entry, "stop": stop, "t1": t1, "t2": t2, "rr1": (t1 - entry) / risk, "rr2": (t2 - entry) / risk,
            "stop_pct": -risk / entry * 100, "t1_pct": (t1 - entry) / entry * 100, "t2_pct": (t2 - entry) / entry * 100}


class Cancelled(Exception):
    pass


def scan(progress=lambda pct, msg: None, posture: dict | None = None, top_n: int = 8, max_per_sector: int = 2,
         exclude: set | None = None, should_stop=lambda: False) -> list[dict]:
    exclude = exclude or set()

    def check():
        if should_stop():
            raise Cancelled()
    syms = [s for s in data.SECTOR_OF if s not in exclude]
    progress(2, f"Checking trends on {len(syms)} liquid US stocks…")
    techs = []
    ex = ThreadPoolExecutor(max_workers=10)
    try:
        for i, d in enumerate(ex.map(_tech, syms)):
            check()
            if d:
                techs.append(d)
            if i % 8 == 0:
                progress(2 + int(48 * i / len(syms)), f"Trend filter: {i}/{len(syms)} · {len(techs)} passing")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    techs.sort(key=lambda x: x["tscore"], reverse=True)
    shortlist = techs[:28]
    progress(52, f"{len(techs)} in healthy uptrends. Reading fundamentals for the top {len(shortlist)}…")

    sectors = (posture or {}).get("sectors", {})
    today = dt.datetime.now(data.NY).date()

    def fund(d):
        try:
            fv = data.finviz(d["symbol"])
        except Exception:
            fv = {}
        d["fv"] = fv
        return d

    picks = []
    ex = ThreadPoolExecutor(max_workers=4)
    try:
        for i, d in enumerate(ex.map(fund, shortlist)):
            check()
            progress(52 + int(38 * (i + 1) / len(shortlist)), f"Fundamentals: {d['symbol']}")
            fv = d["fv"]
            pe, fpe = data.pnum(fv.get("P/E")), data.pnum(fv.get("Forward P/E"))
            eps_y, eps_q = data.pnum(fv.get("EPS this Y")), data.pnum(fv.get("EPS Q/Q"))
            roe, beta = data.pnum(fv.get("ROE")), data.pnum(fv.get("Beta"))
            earn = data.next_earnings(fv.get("Earnings", ""), today)
            d["earnings"] = earn.isoformat() if earn else None
            reasons_out = []
            if fv:
                if not (0 < fpe < 25 or (np.isnan(fpe) and 0 < pe < 30)):
                    reasons_out.append("valuation")
                if not (pe < 35 or np.isnan(pe)):
                    reasons_out.append("P/E")
                if not (eps_y > 0 and eps_q > 0):
                    reasons_out.append("EPS growth")
            if earn and 0 <= (earn - today).days <= 14:
                reasons_out.append(f"earnings {earn:%b %d}")
            if reasons_out:
                continue
            sector = data.SECTOR_OF.get(d["symbol"], fv.get("Sector", ""))
            srank = sectors.get(sector, {}).get("rank")
            strend = sectors.get(sector, {}).get("trend")
            plan = _plan(d)
            fc = forecast.simulate(d["closes"], 30, 3000, plan["entry"], plan["stop"], plan["t1"], plan["t2"])
            qscore = (min(eps_y if not np.isnan(eps_y) else 0, 40) * 0.3 + (min(roe, 40) * 0.2 if not np.isnan(roe) else 0)
                      + (max(0, 25 - fpe) * 0.5 if not np.isnan(fpe) else 0))
            sscore = (12 - srank) * 1.5 + (3 if strend == "Up" else 0) if srank else 0
            score = d["tscore"] + qscore + sscore + fc.exp_return_pct * 4
            bullets = [
                f"Uptrend: {d['price'] / d['sma200'] * 100 - 100:+.1f}% vs 200-day, {d['mom3'] * 100:+.1f}% over 3 months",
                f"RSI {d['rsi']:.0f}, {d['ext50'] * 100:+.1f}% from 50-day average",
            ]
            if fv:
                bullets.append(f"Fwd P/E {fv.get('Forward P/E', '-')}, EPS this yr {fv.get('EPS this Y', '-')}, ROE {fv.get('ROE', '-')}")
            if srank:
                bullets.append(f"Sector rank #{srank}/11 ({sector}, trend {strend})")
            picks.append({
                **plan, "symbol": d["symbol"], "name": d["name"], "sector": sector, "price": d["price"],
                "prev": d["prev"], "score": score, "rsi": d["rsi"], "atr": d["atr"], "earnings": d["earnings"],
                "beta": beta, "fpe": fpe, "bullets": bullets, "source": "scanner",
                "odds": {"stop": fc.p_stop, "t1": fc.p_t1, "t2": fc.p_t2, "none": fc.p_none, "exp_pct": fc.exp_return_pct},
                "thesis": "; ".join(bullets),
            })
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    picks.sort(key=lambda x: x["score"], reverse=True)
    out, per = [], {}
    for p in picks:
        if per.get(p["sector"], 0) >= max_per_sector or p["rr1"] < 1.2:
            continue
        per[p["sector"]] = per.get(p["sector"], 0) + 1
        out.append(p)
        if len(out) >= top_n:
            break
    progress(100, f"Done · {len(out)} ideas from {len(techs)} uptrending stocks")
    return out
