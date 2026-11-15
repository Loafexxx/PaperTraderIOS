"""Paper-trading ledger in AUD. Simulates limit entries with bracket exits from real market bars.

Fill rules (conservative):
  * Buy limit fills when a bar's low <= limit, at min(limit, bar open).
  * Stop fills when low <= stop, at min(stop, bar open). If stop and target touch in the same bar, the stop wins.
  * At Target 1, half the position is sold and the stop moves to breakeven; the rest sells at Target 2.
  * Cash moves at the live AUD/USD rate at the time of the fill.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import uuid
from copy import deepcopy

from . import data

DATA_DIR = os.path.expanduser("~/.local/share/papertrader")
LEDGER = os.path.join(DATA_DIR, "ledger.json")

DEFAULT_SETTINGS = {
    "risk_pct": 1.0,            # % of equity risked per trade
    "max_position_pct": 20.0,   # max % of equity in one stock
    "max_open_risk_pct": 5.0,   # total risk if every stop hits
    "max_per_sector": 2,
    "order_expiry_days": 5,
    "refresh_seconds": 120,
    "scan_size": 8,
    "max_hold_days": 42,
    "claude_path": "",
    "scan_budget": 0.0,         # A$ to put to work from a scan; 0 = all available cash
    "notify_scans": True,       # desktop notification when a scan / Claude estimate finishes
    "notify_trades": True,      # desktop notification on fills, targets and stops
    "server_enabled": False,    # phone companion server (iPhone app uses Claude through this PC)
    "server_port": 8787,
    "server_token": "",
}


def _now() -> float:
    return time.time()


def _id() -> str:
    return uuid.uuid4().hex[:10]


def new_ledger(start_cash: float = 10000.0) -> dict:
    return {
        "version": 1, "base_currency": "AUD", "starting_cash": start_cash, "cash": start_cash,
        "created": _now(), "orders": [], "positions": [], "history": [], "events": [],
        "equity_curve": [[_now(), start_cash]], "settings": deepcopy(DEFAULT_SETTINGS), "watch": [],
    }


class Ledger:
    def __init__(self, path: str = LEDGER):
        self.path = path
        self.lock = threading.RLock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            with open(path) as f:
                self.d = json.load(f)
            for k, v in DEFAULT_SETTINGS.items():
                self.d.setdefault("settings", {}).setdefault(k, v)
            for k in ("events", "watch"):
                self.d.setdefault(k, [])
        else:
            self.d = new_ledger()
            self._seed_trv()
            self.save()

    # ---------- persistence ----------
    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.d, f, indent=1)
            os.replace(tmp, self.path)

    def reset(self, start_cash: float = 10000.0, reset_settings: bool = False):
        """Wipe positions, orders, history, activity and the equity curve; start again with `start_cash` A$."""
        with self.lock:
            settings = deepcopy(DEFAULT_SETTINGS) if reset_settings else self.d.get("settings", deepcopy(DEFAULT_SETTINGS))
            if not reset_settings:
                settings["claude_path"] = self.d.get("settings", {}).get("claude_path", "")
            self.d = new_ledger(float(start_cash))
            self.d["settings"] = settings
            self.event(f"Account reset: starting fresh with A${start_cash:,.2f}.", "info")
            self.save()

    @property
    def s(self) -> dict:
        return self.d["settings"]

    def _seed_trv(self):
        """Carry over the existing TRV paper plan (0.19 sh, limit 398 / stop 387 / T1 418 / T2 440)."""
        self.d["orders"].append({
            "id": _id(), "symbol": "TRV", "name": "The Travelers Companies", "sector": "Financial",
            "created": _now(), "limit": 398.0, "stop": 387.0, "t1": 418.0, "t2": 440.0, "shares": 0.19,
            "status": "pending", "expires": _now() + 6 * 86400, "source": "imported",
            "thesis": "P&C insurer, EPS +33% this year, P/E 10.6, ROE 27.5%, low beta; higher yields lift investment "
                      "income. Imported from your existing paper plan (Alpaca 1-share proxy).",
            "last_bar_ts": 0,
        })
        self.event("Imported your TRV paper order (0.19 sh @ US$398 limit).", "info")

    # ---------- helpers ----------
    def event(self, text: str, kind: str = "info"):
        self.d["events"].append({"ts": _now(), "text": text, "kind": kind})
        self.d["events"] = self.d["events"][-300:]

    def pending(self):
        return [o for o in self.d["orders"] if o["status"] == "pending"]

    def reserved_aud(self, fx: float) -> float:
        return sum(o["shares"] * o["limit"] / fx for o in self.pending())

    def positions_value_aud(self, prices: dict, fx: float) -> float:
        return sum(p["shares"] * prices.get(p["symbol"], p["entry"]) / fx for p in self.d["positions"])

    def equity(self, prices: dict, fx: float) -> float:
        return self.d["cash"] + self.positions_value_aud(prices, fx)

    def open_risk_aud(self, fx: float) -> float:
        r = sum(max(0.0, p["entry"] - p["stop"]) * p["shares"] / fx for p in self.d["positions"])
        r += sum(max(0.0, o["limit"] - o["stop"]) * o["shares"] / fx for o in self.pending())
        return r

    def sector_count(self, sector: str) -> int:
        return sum(1 for x in self.d["positions"] + self.pending() if x.get("sector") == sector)

    # ---------- sizing ----------
    def size(self, entry: float, stop: float, fx: float, prices: dict) -> dict:
        eq = self.equity(prices, fx)
        risk_aud = eq * self.s["risk_pct"] / 100
        max_pos_aud = eq * self.s["max_position_pct"] / 100
        room_risk = max(0.0, eq * self.s["max_open_risk_pct"] / 100 - self.open_risk_aud(fx))
        avail = max(0.0, self.d["cash"] - self.reserved_aud(fx))
        per_share_risk_aud = max(1e-9, (entry - stop) / fx)
        entry_aud = entry / fx
        sh = min(min(risk_aud, room_risk) / per_share_risk_aud, max_pos_aud / entry_aud, avail / entry_aud)
        sh = max(0.0, int(sh * 10000) / 10000)
        return {"shares": sh, "cost_aud": sh * entry_aud, "risk_aud": sh * per_share_risk_aud,
                "equity": eq, "limited_by": min(
                    [("risk", min(risk_aud, room_risk) / per_share_risk_aud), ("max position", max_pos_aud / entry_aud),
                     ("cash", avail / entry_aud)], key=lambda x: x[1])[0]}

    # ---------- actions ----------
    def place_order(self, pick: dict, shares: float) -> dict:
        with self.lock:
            o = {"id": _id(), "symbol": pick["symbol"], "name": pick.get("name", pick["symbol"]),
                 "sector": pick.get("sector", ""), "created": _now(), "limit": float(pick["entry"]),
                 "stop": float(pick["stop"]), "t1": float(pick["t1"]), "t2": float(pick["t2"]),
                 "shares": float(shares), "status": "pending",
                 "expires": _now() + self.s["order_expiry_days"] * 86400 * 7 / 5,
                 "source": pick.get("source", "scanner"), "thesis": pick.get("thesis", ""),
                 "odds": pick.get("odds"), "last_bar_ts": 0}
            self.d["orders"].append(o)
            self.event(f"Order placed: buy {shares:g} {o['symbol']} @ US${o['limit']:.2f} limit "
                       f"(stop {o['stop']:.2f}, T1 {o['t1']:.2f}, T2 {o['t2']:.2f}).", "order")
            self.save()
            return o

    def cancel_order(self, oid: str):
        with self.lock:
            for o in self.d["orders"]:
                if o["id"] == oid and o["status"] == "pending":
                    o["status"] = "cancelled"
                    self.event(f"Order cancelled: {o['symbol']}.", "info")
            self.save()

    def _open(self, o: dict, price: float, ts: float, fx: float):
        cost = o["shares"] * price / fx
        self.d["cash"] -= cost
        o["status"] = "filled"
        o["filled_ts"], o["fill_price"] = ts, price
        pos = {"id": _id(), "order_id": o["id"], "symbol": o["symbol"], "name": o.get("name", ""),
               "sector": o.get("sector", ""), "shares": o["shares"], "orig_shares": o["shares"], "entry": price,
               "entry_fx": fx, "cost_aud": cost, "opened": ts, "stop": o["stop"], "orig_stop": o["stop"],
               "t1": o["t1"], "t2": o["t2"], "half_taken": False, "realized_aud": 0.0, "thesis": o.get("thesis", ""),
               "mae": price, "mfe": price, "last_bar_ts": ts, "odds": o.get("odds")}
        self.d["positions"].append(pos)
        self.event(f"FILLED: bought {o['shares']:g} {o['symbol']} @ US${price:.2f} (A${cost:,.2f}).", "fill")
        return pos

    def _sell(self, pos: dict, shares: float, price: float, ts: float, fx: float, reason: str):
        shares = min(shares, pos["shares"])
        proceeds = shares * price / fx
        cost = shares * pos["entry"] / pos["entry_fx"]
        pnl = proceeds - cost
        self.d["cash"] += proceeds
        pos["shares"] = round(pos["shares"] - shares, 8)
        pos["realized_aud"] += pnl
        kind = "win" if pnl >= 0 else "loss"
        self.event(f"SOLD {shares:g} {pos['symbol']} @ US${price:.2f} ({reason}) · P&L A${pnl:+,.2f}.", kind)
        if pos["shares"] <= 1e-6:
            self.d["positions"].remove(pos)
            total_cost = pos["cost_aud"]
            risk = (pos["entry"] - pos["orig_stop"]) * pos["orig_shares"] / pos["entry_fx"]
            self.d["history"].append({
                "id": pos["id"], "symbol": pos["symbol"], "name": pos.get("name", ""), "sector": pos.get("sector", ""),
                "opened": pos["opened"], "closed": ts, "shares": pos["orig_shares"], "entry": pos["entry"],
                "exit": price, "entry_fx": pos["entry_fx"], "exit_fx": fx, "pnl_aud": pos["realized_aud"],
                "pnl_pct": pos["realized_aud"] / total_cost * 100 if total_cost else 0.0,
                "r_multiple": pos["realized_aud"] / risk if risk > 0 else 0.0, "reason": reason,
                "mae_pct": (pos["mae"] - pos["entry"]) / pos["entry"] * 100,
                "mfe_pct": (pos["mfe"] - pos["entry"]) / pos["entry"] * 100,
                "stop": pos["orig_stop"], "t1": pos["t1"], "t2": pos["t2"], "thesis": pos.get("thesis", ""),
            })

    def sell_now(self, pid: str, price: float, fx: float):
        with self.lock:
            for p in list(self.d["positions"]):
                if p["id"] == pid:
                    self._sell(p, p["shares"], price, _now(), fx, "manual")
            self.save()

    # ---------- simulation ----------
    def process(self, intraday: dict, prices: dict, fx: float) -> list[str]:
        """intraday: symbol -> Bars (15m). Applies fills/exits for bars newer than what we've processed."""
        before = len(self.d["events"])
        with self.lock:
            now = _now()
            for o in self.pending():
                b = intraday.get(o["symbol"])
                if b is None:
                    continue
                for i in range(len(b.ts)):
                    t = float(b.ts[i])
                    if t <= max(o["created"], o.get("last_bar_ts", 0)):
                        continue
                    o["last_bar_ts"] = t
                    if b.l[i] <= o["limit"]:
                        pos = self._open(o, min(o["limit"], float(b.o[i])), t, fx)
                        pos["last_bar_ts"] = t - 1  # let the same bar be evaluated for exits
                        break
                if o["status"] == "pending" and now > o["expires"]:
                    o["status"] = "expired"
                    self.event(f"Order expired unfilled: {o['symbol']} @ US${o['limit']:.2f}.", "info")
            for p in list(self.d["positions"]):
                b = intraday.get(p["symbol"])
                if b is None:
                    continue
                for i in range(len(b.ts)):
                    t = float(b.ts[i])
                    if t <= p.get("last_bar_ts", 0) or p not in self.d["positions"]:
                        continue
                    p["last_bar_ts"] = t
                    lo, hi, op = float(b.l[i]), float(b.h[i]), float(b.o[i])
                    p["mae"], p["mfe"] = min(p["mae"], lo), max(p["mfe"], hi)
                    if lo <= p["stop"]:
                        reason = "breakeven stop" if p["half_taken"] else "stop"
                        self._sell(p, p["shares"], min(p["stop"], op), t, fx, reason)
                        continue
                    if not p["half_taken"] and hi >= p["t1"]:
                        half = round(p["shares"] / 2, 4)
                        self._sell(p, half, max(p["t1"], op), t, fx, "target 1")
                        if p in self.d["positions"]:
                            p["half_taken"] = True
                            p["stop"] = p["entry"]
                            self.event(f"{p['symbol']}: stop moved to breakeven US${p['entry']:.2f}.", "info")
                    if p in self.d["positions"] and p["half_taken"] and hi >= p["t2"]:
                        self._sell(p, p["shares"], max(p["t2"], op), t, fx, "target 2")
            eq = self.equity(prices, fx)
            curve = self.d["equity_curve"]
            if not curve or now - curve[-1][0] > 600 or abs(curve[-1][1] - eq) > 0.01 and now - curve[-1][0] > 60:
                curve.append([now, round(eq, 2)])
                self.d["equity_curve"] = curve[-5000:]
            self.save()
        return [e["text"] for e in self.d["events"][before:]]

    # ---------- stats ----------
    def stats(self) -> dict:
        h = self.d["history"]
        wins = [x for x in h if x["pnl_aud"] > 0]
        losses = [x for x in h if x["pnl_aud"] <= 0]
        gp, gl = sum(x["pnl_aud"] for x in wins), -sum(x["pnl_aud"] for x in losses)
        return {
            "trades": len(h), "win_rate": len(wins) / len(h) * 100 if h else 0.0,
            "realized": sum(x["pnl_aud"] for x in h),
            "avg_r": sum(x["r_multiple"] for x in h) / len(h) if h else 0.0,
            "profit_factor": gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0),
            "best": max((x["pnl_aud"] for x in h), default=0.0), "worst": min((x["pnl_aud"] for x in h), default=0.0),
        }

    def days_held(self, p: dict) -> float:
        return (_now() - p["opened"]) / 86400
