"""Independent checks on Claude's picks using live data (shared by the desktop app and the phone companion server)."""
from __future__ import annotations

import datetime as dt

import numpy as np

from . import claude_bridge, data, forecast


def verify_claude_picks(raw_picks: list, max_per_sector: int, cancelled=lambda: False):
    """Independent checks on Claude's picks using live data. Returns (picks, dropped_reasons)."""
    picks, dropped, per = [], [], {}
    today = dt.datetime.now(data.NY).date()
    for raw in raw_picks:
        if cancelled():
            raise claude_bridge.Cancelled()
        sym = str(raw.get("symbol", "")).upper().strip()
        try:
            b = data.history(sym, "1y", "1d", ttl=600)
            e, s_, t1, t2 = (float(raw[k]) for k in ("entry", "stop", "t1", "t2"))
        except Exception as ex:  # noqa: BLE001
            dropped.append(f"{sym or '?'}: no data ({type(ex).__name__})")
            continue
        live = float(b.price)
        a = forecast.atr(b.h, b.l, b.c)
        if not (s_ < e < t1 <= t2):
            dropped.append(f"{sym}: levels out of order")
            continue
        if abs(e / live - 1) > 0.05:
            dropped.append(f"{sym}: entry {e:.2f} is {abs(e / live - 1) * 100:.0f}% from live {live:.2f} (stale?)")
            continue
        rr1 = (t1 - e) / (e - s_)
        if rr1 < 1.2:
            dropped.append(f"{sym}: reward:risk {rr1:.1f} < 1.2")
            continue
        try:
            fv = data.finviz(sym)
            ed = data.next_earnings(fv.get("Earnings", ""), today)
        except Exception:  # noqa: BLE001
            fv, ed = {}, None
        if ed and 0 <= (ed - today).days <= 14:
            dropped.append(f"{sym}: FinViz shows earnings {ed:%b %d}")
            continue
        sector = raw.get("sector") or data.SECTOR_OF.get(sym, "")
        if per.get(sector, 0) >= max_per_sector:
            dropped.append(f"{sym}: sector limit")
            continue
        per[sector] = per.get(sector, 0) + 1
        pb, pbase, pbull = (max(0.0, float(raw.get(k) or 0)) for k in ("p_bear", "p_base", "p_bull"))
        tot = pb + pbase + pbull
        if tot > 1:
            pb, pbase, pbull = pb / tot, pbase / tot, pbull / tot
        fc = forecast.simulate(b.c, int(raw.get("horizon_days") or 30), 3000, e, s_, t1, t2)
        warn = []
        if np.isfinite(a) and (e - s_) < 1.0 * a:
            warn.append(f"⚠ stop is only {(e - s_) / a:.1f}× ATR from entry (easily hit by normal noise)")
        if raw.get("price_as_of"):
            try:
                age = (today - dt.date.fromisoformat(raw["price_as_of"])).days
                if age > 5:
                    warn.append(f"⚠ Claude's price data was {age} days old")
            except ValueError:
                pass
        ev = [x for x in (raw.get("evidence") or []) if x][:3]
        picks.append({
            "symbol": sym, "name": raw.get("name") or b.name, "sector": sector, "price": live, "prev": float(b.c[-2]),
            "entry": e, "stop": s_, "t1": t1, "t2": t2, "rr1": rr1, "rr2": (t2 - e) / (e - s_),
            "stop_pct": (s_ - e) / e * 100, "t1_pct": (t1 - e) / e * 100, "t2_pct": (t2 - e) / e * 100,
            "earnings": (ed.isoformat() if ed else raw.get("earnings")),
            "bullets": [raw.get("thesis", "")] + ev + [f"Kill switch: {raw.get('kill', '')}"] + warn,
            "thesis": raw.get("thesis", ""), "source": "claude", "atr": a,
            "odds": {"stop": fc.p_stop, "t1": fc.p_t1, "t2": fc.p_t2, "none": fc.p_none, "exp_pct": fc.exp_return_pct},
            "claude_odds": {"bear": pb, "base": pbase, "bull": pbull, "confidence": raw.get("confidence")},
        })
    return picks, dropped
