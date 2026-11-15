"""Paper Trader: a Serpantinum-themed paper-trading desk that works with Claude."""
from __future__ import annotations

import csv
import datetime as dt
import os
import subprocess
import sys
import time
import traceback

import numpy as np
from PySide6.QtCore import QObject, QRect, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from . import claude_bridge, data, engine, forecast, notify, scanner, server
from .charts import EquityChart, PriceChart
from .theme import P, mix, stylesheet
from .widgets import (AnimButton, AnimNumber, Chip, FadeStack, OddsBar, PriceTrack, PulseDot, SlidingIndicator,
                      Spinner, StatCard, Toast, UsageBar, card, confirm_button, entrance, ghost_button, icon_font, label,
                      mono, primary_button, view_button)

APP_NAME = "Paper Trader"
CANCELLED = "__cancelled__"
ICONS = {"dash": "\U000F056E", "scan": "\U000F0349", "trades": "\U000F012A", "hist": "\U000F02DA",
         "set": "\U000F0493", "claude": "\U000F06A9", "refresh": "\U000F0450", "logo": "\U000F04C5"}


def aud(v, sign=False):
    s = f"{abs(v):,.2f}"
    pre = ("+" if v >= 0 else "−") if sign else ("−" if v < 0 else "")
    return f"{pre}A${s}"


def usd(v):
    return f"US${v:,.2f}"


def pct(v, sign=True):
    return f"{v:+.2f}%" if sign else f"{v:.2f}%"


def pnl_color(v):
    return P.green if v >= 0 else P.red


# ---------------------------------------------------------------- background jobs
class _Sig(QObject):
    done = Signal(object)
    err = Signal(str)
    prog = Signal(int, str)


class Job(QRunnable):
    def __init__(self, fn, sig: _Sig):
        super().__init__()
        self.fn, self.sig = fn, sig

    def _emit(self, signal, *args):
        try:
            signal.emit(*args)
        except RuntimeError:  # window already closed
            pass

    def run(self):
        try:
            res = self.fn(lambda p, m: self._emit(self.sig.prog, int(p), str(m)))
            self._emit(self.sig.done, res)
        except (scanner.Cancelled, claude_bridge.Cancelled):
            self._emit(self.sig.err, CANCELLED)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._emit(self.sig.err, f"{type(e).__name__}: {e}")


def clear_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w:
            w.deleteLater()
        elif it.layout():
            clear_layout(it.layout())


def scroll_area(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sa.setWidget(inner)
    return sa


def kv(title, value, color=None, px=15):
    w = QWidget()
    l = QVBoxLayout(w)
    l.setContentsMargins(0, 0, 0, 0)
    l.setSpacing(1)
    l.addWidget(label(title, px=11, color=P.faint, bold=True))
    v = QLabel(value)
    v.setFont(mono(px, QFont.DemiBold))
    if color:
        v.setStyleSheet(f"color:{color}")
    l.addWidget(v)
    return w


# ---------------------------------------------------------------- dialogs
class ConfirmDialog(QDialog):
    def __init__(self, mw, pick: dict):
        super().__init__(mw)
        self.mw, self.pick = mw, pick
        self.setWindowTitle(f"Confirm paper order · {pick['symbol']}")
        self.setMinimumWidth(520)
        sz = mw.ledger.size(pick["entry"], pick["stop"], mw.fx, mw.prices)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)
        lay.addWidget(label(f"Buy {pick['symbol']} · {pick.get('name', '')}", role="h2"))
        lay.addWidget(label("Paper order only. Nothing is sent to a real broker.", px=12, color=P.faint))
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        for i, (k, v, c) in enumerate([("Limit entry", usd(pick["entry"]), None),
                                       ("Stop loss", f"{usd(pick['stop'])} ({pick['stop_pct']:.1f}%)", P.red),
                                       ("Target 1 (sell half)", f"{usd(pick['t1'])} (+{pick['t1_pct']:.1f}%)", P.green),
                                       ("Target 2", f"{usd(pick['t2'])} (+{pick['t2_pct']:.1f}%)", P.primary)]):
            grid.addWidget(kv(k, v, c, 14), i // 2, i % 2)
        lay.addLayout(grid)
        row = QHBoxLayout()
        row.addWidget(label("Shares", bold=True))
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(4)
        self.spin.setRange(0.0001, 100000)
        self.spin.setSingleStep(0.01)
        planned = pick.get("alloc_shares") or 0
        self.spin.setValue(max(planned or sz["shares"], 0.0001))
        self.spin.setFont(mono(15))
        row.addWidget(self.spin)
        hint = (f"budget plan {planned:g} · max allowed {sz['shares']:g}" if planned else
                f"suggested {sz['shares']:g} (limited by {sz['limited_by']})")
        row.addWidget(label(hint, px=12, color=P.faint))
        row.addStretch(1)
        lay.addLayout(row)
        self.summary = label("", px=13, wrap=True)
        self.summary.setFont(mono(13))
        lay.addWidget(self.summary)
        warns = []
        post = mw.posture or {}
        if post.get("label") in ("Cautious", "Defensive"):
            warns.append(f"Market posture is {post['label']} (exposure ceiling ~{post.get('ceiling_pct')}%). "
                         f"Consider a smaller size.")
        if mw.ledger.sector_count(pick.get("sector", "")) >= mw.ledger.s["max_per_sector"]:
            warns.append(f"You already have {mw.ledger.s['max_per_sector']} trades in {pick.get('sector')}.")
        if pick.get("earnings"):
            try:
                ed = dt.date.fromisoformat(pick["earnings"])
                days = (ed - dt.date.today()).days
                if 0 <= days <= 45:
                    warns.append(f"Earnings on {ed:%b %d} ({days} days), inside a typical holding period.")
            except ValueError:
                pass
        if warns:
            w = label("\n".join("⚠  " + x for x in warns), px=12, color=P.primary, wrap=True)
            w.setStyleSheet(f"background:{P.primary_container};color:{P.on_primary_container};border-radius:{P.radius}px;"
                            f"padding:10px;")
            lay.addWidget(w)
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = ghost_button("Cancel")
        cancel.clicked.connect(self.reject)
        ok = confirm_button()
        ok.setText("Place paper order")
        ok.setMinimumWidth(190)
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)
        self.spin.valueChanged.connect(self._update)
        self._update()
        entrance(self, 0, 10, 300)

    def _update(self):
        sh, p, fx = self.spin.value(), self.pick, self.mw.fx
        cost = sh * p["entry"] / fx
        loss = sh * (p["entry"] - p["stop"]) / fx
        g1 = sh * (p["t1"] - p["entry"]) / fx
        g2 = sh * 0.5 * ((p["t1"] - p["entry"]) + (p["t2"] - p["entry"])) / fx
        eq = self.mw.ledger.equity(self.mw.prices, fx)
        self.summary.setText(f"Cost  {aud(cost)}  ({cost / eq * 100:.1f}% of equity)   ·   AUD/USD {fx:.4f}\n"
                             f"Max loss at stop  {aud(-loss)}  ({loss / eq * 100:.2f}% of equity)\n"
                             f"Gain at T1 (all)  {aud(g1, True)}   ·   half at T1 + half at T2  {aud(g2, True)}")

    def shares(self):
        return self.spin.value()


class DetailDialog(QDialog):
    """Chart of history + model forecast + plan levels, with an optional Claude estimate overlay."""

    def __init__(self, mw, symbol: str, plan: dict | None = None, title_extra: str = "", exits: list | None = None):
        super().__init__(mw)
        self.mw, self.symbol, self.plan, self.exits = mw, symbol, plan or {}, exits or []
        self.bars = None
        self.fc = None
        self.claude = None
        self.setWindowTitle(f"{symbol} · chart & outlook")
        self.resize(1180, 720)
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(16)
        left = QVBoxLayout()
        head = QHBoxLayout()
        self.title = label(symbol, role="h1")
        self.sub = label(title_extra, px=13, color=P.faint)
        tbox = QVBoxLayout()
        tbox.addWidget(self.title)
        tbox.addWidget(self.sub)
        head.addLayout(tbox)
        head.addStretch(1)
        self.price_lbl = QLabel("")
        self.price_lbl.setFont(mono(22, QFont.Bold))
        head.addWidget(self.price_lbl)
        left.addLayout(head)
        cframe, cl = card(pad=8)
        self.chart = PriceChart()
        cl.addWidget(self.chart)
        left.addWidget(cframe, 1)
        legend = label("━ price   ┅ 50-day   ┄ 200-day   ▒ model range (10–90% / 25–75%)   ┅ model median   "
                       "● Claude estimate", px=11, color=P.faint)
        left.addWidget(legend)
        root.addLayout(left, 3)

        side = QVBoxLayout()
        side.setSpacing(12)
        sf, sl = card()
        sl.addWidget(label("MODEL OUTLOOK · 30 trading days", px=11, color=P.faint, bold=True))
        self.model_txt = label("Loading…", px=13, wrap=True)
        sl.addWidget(self.model_txt)
        self.odds_holder = QVBoxLayout()
        sl.addLayout(self.odds_holder)
        sl.addWidget(label("Random-walk model on recent volatility with almost no drift: it shows the range of "
                           "plausible outcomes, not a forecast you can rely on.", px=11, color=P.faint, wrap=True))
        side.addWidget(sf)
        cf, cl2 = card()
        hh = QHBoxLayout()
        ic = QLabel(ICONS["claude"])
        ic.setFont(icon_font(20))
        ic.setStyleSheet(f"color:{P.tertiary}")
        hh.addWidget(ic)
        hh.addWidget(label("Claude estimate", role="h2"))
        hh.addStretch(1)
        self.spin = Spinner()
        hh.addWidget(self.spin)
        cl2.addLayout(hh)
        self.claude_txt = QTextBrowser()
        self.claude_txt.setOpenExternalLinks(True)
        self.claude_txt.setMinimumHeight(200)
        self.claude_txt.setHtml(f"<span style='color:{P.faint}'>Ask Claude to analyse {symbol} with your trading skills "
                                f"(fundamentals, trend, news, earnings date) and estimate bear/base/bull outcomes.</span>")
        cl2.addWidget(self.claude_txt, 1)
        brow = QHBoxLayout()
        brow.addStretch(1)
        self.stop_btn = AnimButton("Cancel", mix(P.red, P.bg, 0.78), P.red, mix(P.red, P.bg, 0.6), icon="\U000F0156")
        self.stop_btn.clicked.connect(self.cancel_claude)
        self.stop_btn.hide()
        brow.addWidget(self.stop_btn)
        self.ask_btn = AnimButton("Ask Claude", P.tertiary, "#06261d", mix(P.tertiary, "#ffffff", 0.2), icon=ICONS["claude"])
        self.ask_btn.clicked.connect(self.ask_claude)
        brow.addWidget(self.ask_btn)
        cl2.addLayout(brow)
        self._handle = None
        self._run_id = 0
        side.addWidget(cf, 1)
        root.addLayout(side, 2)
        mw.run(lambda pr: (data.history(symbol, "1y", "1d", ttl=600)), self._loaded, lambda e: self.model_txt.setText(e))

    def _loaded(self, bars):
        self.bars = bars
        extra = self.sub.text() if self.sub.text() and self.sub.text() != bars.name else ""
        self.sub.setText(f"{bars.name}{' · ' + extra if extra else ''}")
        chg = (bars.price / bars.c[-2] - 1) * 100 if len(bars.c) > 1 else 0
        self.price_lbl.setText(f"{usd(bars.price)}  <span style='font-size:14px;color:{pnl_color(chg)}'>{chg:+.2f}%</span>")
        self.price_lbl.setTextFormat(Qt.RichText)
        p = self.plan
        self.fc = forecast.simulate(bars.c, 30, 4000, p.get("entry"), p.get("stop"), p.get("t1"), p.get("t2"))
        a, r = forecast.atr(bars.h, bars.l, bars.c), forecast.rsi(bars.c)
        fc = self.fc
        txt = (f"Median in 30 days: <b>{usd(fc.p50[-1])}</b><br>"
               f"Likely range (25–75%): {usd(fc.p25[-1])} – {usd(fc.p75[-1])}<br>"
               f"Wide range (10–90%): {usd(fc.p10[-1])} – {usd(fc.p90[-1])}<br>"
               f"Volatility {fc.vol_annual * 100:.0f}%/yr · ATR {a:.2f} ({a / bars.price * 100:.1f}%) · RSI {r:.0f}")
        if p.get("stop"):
            txt += (f"<br><br>Plan odds (first touch): stop <b style='color:{P.red}'>{fc.p_stop * 100:.0f}%</b> · "
                    f"T1 <b style='color:{P.green}'>{fc.p_t1 * 100:.0f}%</b> · T2 <b style='color:{P.primary}'>"
                    f"{fc.p_t2 * 100:.0f}%</b> · neither {fc.p_none * 100:.0f}%<br>Model expected return "
                    f"{fc.exp_return_pct:+.2f}% per trade")
            self.odds_holder.addWidget(OddsBar({"stop": fc.p_stop, "t1": fc.p_t1, "t2": fc.p_t2, "none": fc.p_none}))
        self.model_txt.setText(txt)
        self.model_txt.setTextFormat(Qt.RichText)
        self._draw()

    def _draw(self):
        if not self.bars:
            return
        marks = []
        for e in self.exits:
            marks.append(e)
        if self.claude:
            hd = int(self.claude.get("horizon_days") or 30)
            x = self.bars.ts[-1] + hd * 86400 * 7 / 5
            for key, col in (("bear", P.red), ("base", P.tertiary), ("bull", P.green)):
                v = self.claude.get(key) or {}
                if v.get("price"):
                    marks.append({"x": x, "y": float(v["price"]), "color": col,
                                  "text": f" {key} {usd(float(v['price']))} · {float(v.get('prob', 0)) * 100:.0f}%"})
        self.chart.show_data(self.bars.ts, self.bars.c, self.fc, self.plan or None, marks=marks)

    def ask_claude(self):
        if not claude_bridge.find_claude(self.mw.ledger.s.get("claude_path", "")):
            self.claude_txt.setHtml(self.mw.claude_help_html())
            return
        price = self.bars.price if self.bars else 0
        p = self.plan or {}
        prompt = claude_bridge.ESTIMATE_PROMPT.format(symbol=self.symbol, name=(self.bars.name if self.bars else ""),
                                                      price=price, entry=p.get("entry", price),
                                                      stop=p.get("stop", price * 0.95), t1=p.get("t1", price * 1.05),
                                                      t2=p.get("t2", price * 1.1), today=dt.date.today().isoformat(),
                                                      data_howto=claude_bridge._DATA_HOWTO)
        self._run_id += 1
        rid = self._run_id
        self._handle = claude_bridge.RunHandle()
        h = self._handle
        self._set_running(True)
        self.claude_txt.setHtml(f"<span style='color:{P.faint}'>Claude is researching {self.symbol} and checking its "
                                f"numbers… (usually 1–4 minutes). Cancel any time; closing this window also stops it."
                                f"</span>")
        self.mw.run(lambda pr: claude_bridge.ask(prompt, self.mw.ledger.s.get("claude_path", ""), 900, 40, handle=h),
                    lambda j: rid == self._run_id and self._claude_done(j),
                    lambda e: rid == self._run_id and self._claude_err(e))

    def _set_running(self, on):
        (self.spin.start() if on else self.spin.stop())
        self.ask_btn.setEnabled(not on)
        self.stop_btn.setVisible(on)

    def cancel_claude(self):
        if self._handle:
            self._handle.cancel()
        QTimer.singleShot(3000, self.mw.refresh_usage)
        self._run_id += 1
        self._set_running(False)
        self.claude_txt.setHtml(f"<span style='color:{P.faint}'>Cancelled. Claude was stopped, so it isn't using any "
                                f"more of your usage.</span>")

    def done(self, r):
        if self._handle and self.stop_btn.isVisible():
            self._handle.cancel()
        super().done(r)

    def closeEvent(self, e):
        if self._handle and self.stop_btn.isVisible():
            self._handle.cancel()
        super().closeEvent(e)

    def _claude_err(self, e):
        self._set_running(False)
        self.mw.refresh_usage()
        if e == CANCELLED:
            return
        self.mw.notify(f"Claude estimate failed · {self.symbol}", str(e)[:300], urgency="critical")
        self.claude_txt.setHtml(f"<b style='color:{P.red}'>Claude error</b><br>{e}")

    def _claude_done(self, j):
        self._set_running(False)
        self.mw.refresh_usage()
        self.mw.notify(f"Claude estimate ready · {self.symbol}",
                       f"{str(j.get('verdict', '')).upper()} · {j.get('summary', '')[:220]}")
        self.claude = j
        risks = "".join(f"<li>{x}</li>" for x in j.get("key_risks", []))
        row = lambda k, c: (f"<tr><td style='color:{c};padding-right:12px'><b>{k.title()}</b></td>"
                            f"<td>{usd(float((j.get(k) or {}).get('price', 0)))}</td>"
                            f"<td style='padding-left:10px'>{float((j.get(k) or {}).get('prob', 0)) * 100:.0f}%</td></tr>")
        self.claude_txt.setHtml(
            f"<p><b style='color:{P.primary}'>{j.get('verdict', '').upper()}</b> · confidence "
            f"{j.get('confidence', '?')} · horizon {j.get('horizon_days', '?')} trading days</p>"
            f"<p>{j.get('summary', '')}</p><table>{row('bull', P.green)}{row('base', P.tertiary)}{row('bear', P.red)}</table>"
            f"<p><b>Earnings:</b> {j.get('earnings') or 'unknown'}<br><b>Support:</b> {j.get('support')} · "
            f"<b>Resistance:</b> {j.get('resistance')}</p><p><b>Key risks</b><ul>{risks}</ul></p>"
            f"<p><b>Evidence</b><ul>{''.join(f'<li>{x}</li>' for x in j.get('evidence', []))}</ul></p>"
            f"<p><b>Plan changes:</b> {j.get('plan_changes', '')}</p>"
            + (f"<p style='color:{P.primary}'><b>Couldn't verify:</b> {', '.join(map(str, j.get('unverified', [])))}</p>"
               if j.get('unverified') else "")
            + f"<p style='color:{P.faint}'>Price data as of {j.get('price_as_of', '?')}.</p>"
            f"<p style='color:{P.faint}'>Estimates, not guarantees.</p>")
        self._draw()


# ---------------------------------------------------------------- pages
class Page(QWidget):
    title = ""

    def __init__(self, mw):
        super().__init__()
        self.mw = mw

    def render(self):
        pass


class DashboardPage(Page):
    title = "Dashboard"

    def __init__(self, mw):
        super().__init__(mw)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.c_eq = StatCard("Equity", lambda v: aud(v))
        self.c_cash = StatCard("Cash", lambda v: aud(v))
        self.c_inv = StatCard("Invested", lambda v: aud(v))
        self.c_open = StatCard("Open P&L", lambda v: aud(v, True))
        self.c_real = StatCard("Realised P&L", lambda v: aud(v, True))
        self.c_risk = StatCard("Open risk", lambda v: aud(v))
        for i, c in enumerate((self.c_eq, self.c_cash, self.c_inv, self.c_open, self.c_real, self.c_risk)):
            row.addWidget(c)
            entrance(c, 60 * i)
        lay.addLayout(row)
        mid = QHBoxLayout()
        mid.setSpacing(14)
        ef, el = card()
        h = QHBoxLayout()
        h.addWidget(label("Account equity", role="h2"))
        h.addStretch(1)
        self.ret_chip = Chip("")
        h.addWidget(self.ret_chip)
        el.addLayout(h)
        self.eq_chart = EquityChart()
        self.eq_chart.setMinimumHeight(260)
        el.addWidget(self.eq_chart, 1)
        mid.addWidget(ef, 3)
        right = QVBoxLayout()
        right.setSpacing(14)
        pf, pl = card()
        ph = QHBoxLayout()
        ph.addWidget(label("Market posture", role="h2"))
        ph.addStretch(1)
        self.post_spin = Spinner()
        ph.addWidget(self.post_spin)
        rb = ghost_button("", ICONS["refresh"])
        rb.setToolTip("Re-run the breadth, uptrend and sector skills")
        rb.clicked.connect(lambda: mw.load_posture(force=True))
        ph.addWidget(rb)
        pl.addLayout(ph)
        self.post_lbl = label("…", px=22, bold=True)
        pl.addWidget(self.post_lbl)
        self.post_detail = label("Running market-breadth-analyzer, uptrend-analyzer and sector-analyst…", px=12,
                                 color=P.faint, wrap=True)
        pl.addWidget(self.post_detail)
        self.post_bar = QProgressBar()
        self.post_bar.setTextVisible(False)
        self.post_bar.setRange(0, 100)
        pl.addWidget(self.post_bar)
        self.sector_lbl = label("", px=12, color=P.muted, wrap=True)
        pl.addWidget(self.sector_lbl)
        right.addWidget(pf)
        af, al = card()
        al.addWidget(label("Activity", role="h2"))
        self.feed = QVBoxLayout()
        self.feed.setSpacing(6)
        fw = QWidget()
        fw.setLayout(self.feed)
        sa = scroll_area(fw)
        sa.setMinimumHeight(180)
        al.addWidget(sa, 1)
        right.addWidget(af, 1)
        mid.addLayout(right, 2)
        lay.addLayout(mid, 1)
        self._feed_n = -1

    def render(self):
        mw, L = self.mw, self.mw.ledger
        fx = mw.fx
        with L.lock:
            eq = L.equity(mw.prices, fx)
            inv = L.positions_value_aud(mw.prices, fx)
            open_pnl = sum(p["shares"] * (mw.prices.get(p["symbol"], p["entry"]) / fx - p["entry"] / p["entry_fx"])
                           for p in L.d["positions"])
            st = L.stats()
            risk = L.open_risk_aud(fx)
            curve = list(L.d["equity_curve"])
            events = list(L.d["events"])
            start = L.d["starting_cash"]
            cash = L.d["cash"]
        self.c_eq.num.set_value(eq)
        self.c_eq.sub.setText(f"{(eq / start - 1) * 100:+.2f}% since start")
        self.c_cash.num.set_value(cash)
        self.c_cash.sub.setText(f"{len(L.pending())} pending order(s)")
        self.c_inv.num.set_value(inv)
        self.c_inv.sub.setText(f"{len(L.d['positions'])} position(s)")
        self.c_open.num.set_value(open_pnl, pnl_color(open_pnl))
        self.c_real.num.set_value(st["realized"], pnl_color(st["realized"]))
        self.c_real.sub.setText(f"{st['trades']} closed · win {st['win_rate']:.0f}%")
        self.c_risk.num.set_value(risk)
        self.c_risk.sub.setText(f"{risk / eq * 100:.2f}% if every stop hits")
        r = (eq / start - 1) * 100
        self.ret_chip.set(f"{r:+.2f}%", mix(pnl_color(r), P.bg, 0.75), pnl_color(r))
        self.eq_chart.show_curve(curve, start)
        if len(events) != self._feed_n:
            self._feed_n = len(events)
            clear_layout(self.feed)
            cols = {"fill": P.primary, "win": P.green, "loss": P.red, "order": P.tertiary, "error": P.red}
            for i, e in enumerate(reversed(events[-40:])):
                t = dt.datetime.fromtimestamp(e["ts"]).strftime("%a %d %b %H:%M")
                w = label(f"<span style='color:{P.faint};font-family:\"{P.mono}\"'>{t}</span>&nbsp;&nbsp;"
                          f"<span style='color:{cols.get(e['kind'], P.fg)}'>●</span> {e['text']}", px=12, wrap=True)
                w.setTextFormat(Qt.RichText)
                self.feed.addWidget(w)
                if i < 8:
                    entrance(w, 40 * i, 10, 300)
            self.feed.addStretch(1)
        self.render_posture()

    def render_posture(self):
        post = self.mw.posture
        if not post:
            return
        col = {"Risk-on": P.green, "Selective": P.primary, "Cautious": P.primary, "Defensive": P.red}.get(post["label"], P.fg)
        self.post_lbl.setText(f"{post['label']} · exposure ≤ {post.get('ceiling_pct') or '?'}%")
        self.post_lbl.setStyleSheet(f"color:{col};font-size:22px;font-weight:700")
        self.post_detail.setText(post.get("detail", ""))
        self.post_bar.setValue(int(post.get("ceiling_pct") or 0))
        secs = sorted(post.get("sectors", {}).values(), key=lambda r: r["rank"])
        if secs:
            strong = ", ".join(s["sector"] for s in secs[:3])
            weak = ", ".join(s["sector"] for s in secs[-3:])
            self.sector_lbl.setText(f"Strong: {strong}\nWeak: {weak}")


class PickCard(QFrame):
    def __init__(self, mw, pick: dict):
        super().__init__()
        self.mw, self.pick = mw, pick
        self.setObjectName("card")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(22)
        # identity
        a = QVBoxLayout()
        a.setSpacing(4)
        t = QLabel(pick["symbol"])
        t.setFont(mono(24, QFont.Black))
        t.setStyleSheet(f"color:{P.primary}")
        a.addWidget(t)
        a.addWidget(label(pick.get("name", "")[:34], px=12, color=P.muted))
        chips = QHBoxLayout()
        chips.setSpacing(6)
        chips.addWidget(Chip(pick.get("sector", "") or "—"))
        if pick.get("source") == "claude":
            chips.addWidget(Chip("Claude", mix(P.tertiary, P.bg, 0.7), P.tertiary))
        chips.addStretch(1)
        a.addLayout(chips)
        chg = (pick["price"] / pick.get("prev", pick["price"]) - 1) * 100 if pick.get("prev") else 0
        pr = label(f"{usd(pick['price'])} <span style='color:{pnl_color(chg)}'>{chg:+.2f}%</span>", px=14)
        pr.setTextFormat(Qt.RichText)
        pr.setFont(mono(14))
        a.addWidget(pr)
        a.addWidget(label(f"≈ {aud(pick['price'] / mw.fx)} · earnings {pick.get('earnings') or '?'}", px=11, color=P.faint))
        a.addStretch(1)
        aw = QWidget()
        aw.setLayout(a)
        aw.setFixedWidth(210)
        lay.addWidget(aw)
        # thesis + plan
        b = QVBoxLayout()
        b.setSpacing(8)
        for x in pick.get("bullets", [])[:5]:
            b.addWidget(label("•  " + x, px=12, color=P.muted, wrap=True))
        g = QHBoxLayout()
        g.setSpacing(18)
        g.addWidget(kv("ENTRY", f"{pick['entry']:.2f}"))
        g.addWidget(kv("STOP", f"{pick['stop']:.2f} ({pick['stop_pct']:.1f}%)", P.red))
        g.addWidget(kv("T1", f"{pick['t1']:.2f} (+{pick['t1_pct']:.1f}%)", P.green))
        g.addWidget(kv("T2", f"{pick['t2']:.2f} (+{pick['t2_pct']:.1f}%)", P.primary))
        g.addWidget(kv("R:R", f"{pick['rr1']:.1f} / {pick['rr2']:.1f}"))
        g.addStretch(1)
        b.addLayout(g)
        o = pick.get("odds") or {}
        if o:
            ob = QHBoxLayout()
            ob.addWidget(OddsBar(o), 1)
            ob.addWidget(label(f"model: stop {o.get('stop', 0) * 100:.0f}% · T1 {o.get('t1', 0) * 100:.0f}% · "
                               f"T2 {o.get('t2', 0) * 100:.0f}% · EV {o.get('exp_pct', 0):+.2f}%", px=11, color=P.faint))
            b.addLayout(ob)
        co = pick.get("claude_odds")
        if co:
            b.addWidget(label(f"Claude: bear {co['bear'] * 100:.0f}% · base {co['base'] * 100:.0f}% · bull "
                              f"{co['bull'] * 100:.0f}% · confidence {co.get('confidence', '?')}", px=11, color=P.tertiary))
        lay.addLayout(b, 1)
        # sizing + actions
        c = QVBoxLayout()
        c.setSpacing(8)
        sz = mw.ledger.size(pick["entry"], pick["stop"], mw.fx, mw.prices)
        if pick.get("alloc_shares", 0) > 0:
            sh, cost = pick["alloc_shares"], pick["alloc_aud"]
            risk = sh * (pick["entry"] - pick["stop"]) / mw.fx
            txt = f"BUDGET PLAN\n{sh:g} sh · {aud(cost)}\nrisk {aud(risk)}"
        elif "alloc_shares" in pick:
            txt = f"not in budget plan\n(max {sz['shares']:g} sh · {aud(sz['cost_aud'])})"
        else:
            txt = (f"{sz['shares']:g} sh · {aud(sz['cost_aud'])}\nrisk {aud(sz['risk_aud'])} "
                   f"({sz['risk_aud'] / max(sz['equity'], 1) * 100:.2f}%)")
        self.size_lbl = label(txt, px=12, color=P.green if pick.get("alloc_shares", 0) > 0 else P.muted)
        self.size_lbl.setFont(mono(12))
        self.size_lbl.setAlignment(Qt.AlignRight)
        c.addWidget(self.size_lbl)
        c.addStretch(1)
        self.ok = confirm_button()
        self.ok.clicked.connect(self.confirm)
        self.view = view_button()
        self.view.clicked.connect(self.open_view)
        held = {p["symbol"] for p in mw.ledger.d["positions"]} | {o["symbol"] for o in mw.ledger.pending()}
        if pick["symbol"] in held:
            self._mark("Holding")
        elif sz["shares"] <= 0:
            self.ok.setEnabled(False)
            self.ok.setToolTip("No room: risk budget, cash or position limit reached")
        c.addWidget(self.ok, 0, Qt.AlignRight)
        c.addWidget(self.view, 0, Qt.AlignRight)
        lay.addLayout(c)

    def _mark(self, text):
        self.ok.setText(text)
        self.ok.set_colors(P.card_hi, P.green, P.card_hi)
        self.ok.setEnabled(False)

    def confirm(self):
        d = ConfirmDialog(self.mw, self.pick)
        if d.exec() == QDialog.Accepted:
            self.mw.ledger.place_order(self.pick, d.shares())
            self._mark("✓ Ordered")
            self.mw.toast(f"Paper order placed: {d.shares():g} {self.pick['symbol']} @ {usd(self.pick['entry'])} limit", "order")
            self.mw.render_all()

    def open_view(self):
        DetailDialog(self.mw, self.pick["symbol"], self.pick, self.pick.get("name", "")).exec()


# ---------------------------------------------------------------- budget plan + advice
ADVICE_STYLE = {
    "dont_invest": (P.red, "\U000F0028", "Keep cash"),
    "wait_for_pullback": (P.primary, "\U000F0150", "Wait"),
    "invest_partial": (P.tertiary, "\U000F0A9E", "Invest part"),
    "invest": (P.green, "\U000F05E0", "Invest"),
}


def available_cash(mw) -> float:
    L = mw.ledger
    return max(0.0, L.d["cash"] - L.reserved_aud(mw.fx))


def allocate_budget(mw, picks: list, budget: float, targets: dict | None = None, top_k: int = 5) -> dict:
    """Split `budget` A$ across picks within cash, posture and risk limits. Writes alloc_* onto each pick.
    `targets` (symbol -> A$) comes from Claude's advice; otherwise the top picks share the budget equally."""
    L, fx = mw.ledger, mw.fx
    eq = L.equity(mw.prices, fx)
    cash = available_cash(mw)
    cap = min(budget, cash)
    notes = []
    if cap < budget - 0.01:
        notes.append(f"limited to your available cash A${cash:,.0f}")
    post = mw.posture or {}
    if post.get("ceiling_pct") is not None and targets is None:
        exposure_now = L.positions_value_aud(mw.prices, fx) + L.reserved_aud(fx)
        room = max(0.0, eq * post["ceiling_pct"] / 100 - exposure_now)
        if room < cap:
            cap = room
            notes.append(f"market posture caps total exposure at {post['ceiling_pct']}% of equity")
    risk_room = max(0.0, eq * L.s["max_open_risk_pct"] / 100 - L.open_risk_aud(fx))
    per_trade_risk = eq * L.s["risk_pct"] / 100
    max_pos = eq * L.s["max_position_pct"] / 100
    for p in picks:
        p["alloc_shares"], p["alloc_aud"] = 0.0, 0.0
    if targets is not None:
        chosen = [p for p in picks if targets.get(p["symbol"], 0) > 0]
        want = {p["symbol"]: float(targets[p["symbol"]]) for p in chosen}
    else:
        chosen = picks[:top_k]
        want = {p["symbol"]: cap / max(1, len(chosen)) for p in chosen}
    tot_want = sum(want.values())
    scale = min(1.0, cap / tot_want) if tot_want > 0 else 0.0
    if scale < 0.999 and targets is not None:
        notes.append(f"Claude's amounts scaled to {scale * 100:.0f}% to fit your budget/cash")
    invest = risk_used = 0.0
    for p in chosen:
        aud = want[p["symbol"]] * scale
        aud = min(aud, max_pos)
        risk_ps = (p["entry"] - p["stop"]) / fx
        aud = min(aud, per_trade_risk / risk_ps * p["entry"] / fx, (risk_room - risk_used) / risk_ps * p["entry"] / fx)
        sh = max(0.0, int(aud * fx / p["entry"] * 10000) / 10000)
        if sh * p["entry"] / fx < 20:  # too small to be worth managing
            continue
        p["alloc_shares"], p["alloc_aud"] = sh, sh * p["entry"] / fx
        p["alloc_why"] = p.get("alloc_why") or (p.get("bullets") or [""])[0]
        invest += p["alloc_aud"]
        risk_used += sh * risk_ps
    return {"budget": budget, "invest": invest, "cash_left": budget - invest, "risk": risk_used, "notes": notes,
            "count": sum(1 for p in picks if p["alloc_shares"] > 0)}


def builtin_advice(mw, picks: list, plan: dict) -> dict:
    post = mw.posture or {}
    lbl = post.get("label", "Unknown")
    n, inv, b = plan["count"], plan["invest"], plan["budget"]
    if not picks or n == 0 or lbl == "Defensive":
        action = "dont_invest"
        head = f"Don't invest for now: keep your A${b:,.0f} in cash"
        why = ("Nothing passed every filter with room in your limits." if not picks or n == 0 else
               "The market-breadth and uptrend skills read Defensive: most stocks are falling, so new buys tend to fail.")
    elif lbl == "Cautious" or inv < b * 0.6:
        action = "invest_partial"
        head = f"Invest part: A${inv:,.0f} across the top {n}, keep A${plan['cash_left']:,.0f} in cash"
        why = (f"Market posture is {lbl} ({post.get('detail', '')}), so the plan stays below the exposure ceiling and "
               f"spreads money across different sectors.")
    else:
        action = "invest"
        head = f"Here are the top {n} stocks for your A${b:,.0f}, and why"
        why = f"Market posture is {lbl}. The plan splits the budget across the strongest ideas, max 2 per sector."
    if plan["notes"]:
        why += " Budget " + "; ".join(plan["notes"]) + "."
    return {"action": action, "headline": head, "reasoning": why, "invest_now_aud": inv,
            "keep_cash_aud": plan["cash_left"], "confidence": "rule-based",
            "what_would_change": "Breadth ≥ 60 and uptrend ≥ 50 would allow more exposure; a Defensive read means cash.",
            "allocations": [{"symbol": p["symbol"], "aud": p["alloc_aud"], "why": p.get("alloc_why", "")}
                            for p in picks if p.get("alloc_shares", 0) > 0]}


class AdviceCard(QFrame):
    def __init__(self, mw, adv: dict, picks: list, budget: float, source: str, extra_notes: list | None = None):
        super().__init__()
        self.setObjectName("card")
        col, ico, tag = ADVICE_STYLE.get(adv.get("action", ""), (P.muted, "\U000F02FC", "Advice"))
        self.setStyleSheet(f"QFrame#card{{background:{mix(col, P.card, 0.9)};border:1px solid {mix(col, P.bg, 0.55)};"
                           f"border-radius:{P.radius + 4}px;}}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(12)
        i = QLabel(ico)
        i.setFont(icon_font(30))
        i.setStyleSheet(f"color:{col};background:transparent")
        top.addWidget(i, 0, Qt.AlignTop)
        hb = QVBoxLayout()
        hb.setSpacing(2)
        hl = label(adv.get("headline") or tag, px=21, color=col, bold=True, wrap=True)
        hb.addWidget(hl)
        hb.addWidget(label(f"{'Claude advice' if source == 'claude' else 'Budget plan · built-in rules (not Claude)'}"
                           f"  ·  confidence {adv.get('confidence', '?')}", px=11, color=P.faint))
        top.addLayout(hb, 1)
        top.addWidget(Chip(tag, mix(col, P.bg, 0.7), col, 13), 0, Qt.AlignTop)
        lay.addLayout(top)
        if adv.get("reasoning"):
            lay.addWidget(label(adv["reasoning"], px=13, color=P.fg, wrap=True))
        allocs = [p for p in picks if p.get("alloc_shares", 0) > 0]
        inv = sum(p["alloc_aud"] for p in allocs)
        stats = QHBoxLayout()
        stats.setSpacing(28)
        stats.addWidget(kv("BUDGET", aud(budget)))
        stats.addWidget(kv("INVEST NOW", aud(inv), col))
        stats.addWidget(kv("KEEP IN CASH", aud(max(0.0, budget - inv))))
        stats.addWidget(kv("RISK IF ALL STOPS HIT", aud(-sum(p["alloc_shares"] * (p["entry"] - p["stop"]) / self_fx(mw)
                                                              for p in allocs)), P.red))
        stats.addStretch(1)
        lay.addLayout(stats)
        if allocs:
            for p in allocs:
                r = QHBoxLayout()
                r.setSpacing(14)
                t = QLabel(p["symbol"])
                t.setFont(mono(15, QFont.Black))
                t.setStyleSheet(f"color:{P.primary};background:transparent")
                t.setFixedWidth(64)
                r.addWidget(t, 0, Qt.AlignTop)
                amt = QLabel(f"{aud(p['alloc_aud'])}\n{p['alloc_shares']:g} sh @ {p['entry']:.2f}")
                amt.setFont(mono(12))
                amt.setStyleSheet("background:transparent")
                amt.setFixedWidth(150)
                r.addWidget(amt, 0, Qt.AlignTop)
                bar = QProgressBar()
                bar.setTextVisible(False)
                bar.setRange(0, 1000)
                bar.setFixedWidth(90)
                bar.setValue(int(1000 * p["alloc_aud"] / max(budget, 1)))
                r.addWidget(bar, 0, Qt.AlignVCenter)
                r.addWidget(label(p.get("alloc_why", ""), px=12, color=P.muted, wrap=True), 1)
                lay.addLayout(r)
        if adv.get("what_would_change"):
            lay.addWidget(label(f"What would change this: {adv['what_would_change']}", px=12, color=P.faint, wrap=True))
        for n in extra_notes or []:
            lay.addWidget(label("•  " + n, px=11, color=P.faint, wrap=True))
        lay.addWidget(label("Paper-trading suggestions, not financial advice. Press Confirm on a card below to place it.",
                            px=10, color=P.faint))


def self_fx(mw):
    return mw.fx or 0.65


class ScannerPage(Page):
    title = "Scanner"

    def __init__(self, mw):
        super().__init__(mw)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        top, tl = card(pad=18)
        h = QHBoxLayout()
        txt = QVBoxLayout()
        txt.addWidget(label("Find trade ideas", role="h2"))
        txt.addWidget(label("Scans ~150 liquid US stocks: uptrend above the 50/200-day averages, not over-extended, "
                            "positive EPS growth, sane valuation, no earnings in 14 days, max 2 per sector. "
                            "Or let Claude run your trading skills for a deeper scan.", px=12, color=P.faint, wrap=True))
        h.addLayout(txt, 1)
        self.scan_btn = primary_button("Scan market", ICONS["scan"])
        self.scan_btn.clicked.connect(self.scan)
        self.claude_btn = AnimButton("Ask Claude to scan", P.tertiary, "#06261d", mix(P.tertiary, "#ffffff", 0.2),
                                     icon=ICONS["claude"])
        self.claude_btn.clicked.connect(self.claude_scan)
        h.addWidget(self.scan_btn)
        h.addWidget(self.claude_btn)
        tl.addLayout(h)
        br = QHBoxLayout()
        br.setSpacing(10)
        bi = QLabel("\U000F0116")
        bi.setFont(icon_font(18))
        bi.setStyleSheet(f"color:{P.primary}")
        br.addWidget(bi)
        br.addWidget(label("Budget for this scan", px=13, bold=True))
        self.budget = QDoubleSpinBox()
        self.budget.setDecimals(0)
        self.budget.setRange(0, 100_000_000)
        self.budget.setSingleStep(250)
        self.budget.setPrefix("A$ ")
        self.budget.setGroupSeparatorShown(True)
        self.budget.setFixedWidth(160)
        self.budget.setFont(mono(14))
        b0 = float(mw.ledger.s.get("scan_budget") or 0)
        self.budget.setValue(b0 if b0 > 0 else round(mw.ledger.d["cash"]))
        self.budget.valueChanged.connect(self._budget_changed)
        br.addWidget(self.budget)
        for txt, frac in (("25%", 0.25), ("50%", 0.5), ("All cash", 1.0)):
            q = ghost_button(txt, px=12)
            q.clicked.connect(lambda _=False, f=frac: self.budget.setValue(round(available_cash(self.mw) * f)))
            br.addWidget(q)
        self.budget_hint = label("", px=12, color=P.faint)
        br.addWidget(self.budget_hint)
        br.addStretch(1)
        tl.addLayout(br)
        pr = QHBoxLayout()
        self.spin = Spinner()
        pr.addWidget(self.spin)
        self.status = label("Press Scan market to look for ideas.", px=12, color=P.muted)
        pr.addWidget(self.status, 1)
        self.cancel_btn = AnimButton("Cancel scan", mix(P.red, P.bg, 0.78), P.red, mix(P.red, P.bg, 0.6),
                                     icon="\U000F0156", px=13)
        self.cancel_btn.clicked.connect(self.cancel)
        self.cancel_btn.hide()
        pr.addWidget(self.cancel_btn)
        tl.addLayout(pr)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        tl.addWidget(self.bar)
        self.banner = label("", px=12, wrap=True)
        self.banner.hide()
        tl.addWidget(self.banner)
        lay.addWidget(top)
        inner = QWidget()
        self.list = QVBoxLayout(inner)
        self.list.setContentsMargins(0, 0, 6, 0)
        self.list.setSpacing(12)
        self.list.addStretch(1)
        lay.addWidget(scroll_area(inner), 1)
        self._bar_anim = None
        self._scan_id = 0
        self._stop = False
        self._handle: claude_bridge.RunHandle | None = None
        self._kind = ""

    # ----- plumbing
    def _budget_changed(self, v):
        self.mw.ledger.s["scan_budget"] = float(v)
        self.mw.ledger.save()
        self.render()

    def render(self):
        cash = available_cash(self.mw)
        b = self.budget.value()
        self.budget_hint.setText(f"available cash {aud(cash)}" + ("  ·  ⚠ more than your cash" if b > cash + 0.5 else ""))

    def _progress(self, p, m, sid=None):
        if sid is not None and sid != self._scan_id:
            return
        from PySide6.QtCore import QEasingCurve, QPropertyAnimation
        a = QPropertyAnimation(self.bar, b"value", self, duration=300, easingCurve=QEasingCurve.OutCubic)
        a.setStartValue(self.bar.value())
        a.setEndValue(p)
        a.start()
        self._bar_anim = a
        self.status.setText(m)

    def _busy(self, on, kind=""):
        self._kind = kind if on else ""
        self.scan_btn.setEnabled(not on)
        self.claude_btn.setEnabled(not on)
        self.cancel_btn.setVisible(on)
        if on:
            entrance(self.cancel_btn, 0, 6, 250)
        (self.spin.start() if on else self.spin.stop())

    def _new_run(self):
        self._scan_id += 1
        self._stop = False
        self._handle = None
        return self._scan_id

    def cancel(self):
        kind = self._kind
        self._stop = True
        if self._handle:
            self._handle.cancel()
        self._scan_id += 1          # ignore anything the old run still sends
        self._busy(False)
        QTimer.singleShot(3000, self.mw.refresh_usage)
        self._progress(0, "Scan cancelled." + (" Claude was stopped, so it isn't using any more of your usage."
                                               if kind == "claude" else ""))
        self.mw.toast("Scan cancelled", "info")

    def _show_banner(self, text, col):
        self.banner.setText(text)
        self.banner.setStyleSheet(f"background:{mix(col, P.bg, 0.8)};color:{col};border-radius:{P.radius}px;padding:10px;")
        self.banner.show()
        entrance(self.banner, 0, 8, 300)

    def _err_for(self, sid):
        def err(e):
            if sid != self._scan_id:
                return
            self._busy(False)
            if e == CANCELLED:
                return
            self.status.setText(f"Scan failed: {e}")
            self.mw.toast(f"Scan failed: {e}", "error")
            self.mw.notify("Scan failed", str(e)[:300], urgency="critical")
        return err

    # ----- built-in scan
    def scan(self):
        sid = self._new_run()
        self._busy(True, "builtin")
        self.banner.hide()
        clear_layout(self.list)
        self.list.addStretch(1)
        mw = self.mw
        exclude = {p["symbol"] for p in mw.ledger.d["positions"]}
        s = mw.ledger.s

        def job(pr):
            post = mw.posture or scanner.market_posture()
            if self._stop:
                raise scanner.Cancelled()
            return post, scanner.scan(pr, post, int(s["scan_size"]), int(s["max_per_sector"]), exclude,
                                      should_stop=lambda: self._stop or sid != self._scan_id)

        def done(res):
            if sid != self._scan_id:
                return
            post, picks = res
            if post and not mw.posture:
                mw.posture = post
            self._busy(False)
            budget = self.budget.value()
            plan = allocate_budget(mw, picks, budget)
            adv = builtin_advice(mw, picks, plan)
            self.show_picks(picks, AdviceCard(mw, adv, picks, budget, "builtin", plan["notes"]))
            top = ", ".join(p["symbol"] for p in picks if p.get("alloc_shares", 0) > 0) or "none"
            mw.notify("Market scan finished", f"{adv['headline']}\n{len(picks)} ideas · budget plan: {top}")
        mw.run(job, done, self._err_for(sid), lambda p, m: self._progress(p, m, sid))

    def show_picks(self, picks, header: QWidget | None = None):
        self.mw.scan_results = picks
        clear_layout(self.list)
        # Ideas in the budget plan first.
        picks.sort(key=lambda p: -p.get("alloc_aud", 0))
        if header is not None:
            self.list.addWidget(header)
            entrance(header, 0, 20, 480)
        if not picks:
            self.list.addWidget(label("Nothing passed every filter right now. That's a valid answer: cash is a position.",
                                      px=14, color=P.muted))
        for i, p in enumerate(picks):
            c = PickCard(self.mw, p)
            self.list.addWidget(c)
            entrance(c, 120 + 90 * i, 22, 460)
        self.list.addStretch(1)

    def clear_results(self):
        self.mw.scan_results = []
        clear_layout(self.list)
        self.list.addStretch(1)
        self.banner.hide()
        self.bar.setValue(0)
        self.status.setText("Press Scan market to look for ideas.")

    # ----- Claude scan
    def claude_scan(self):
        mw = self.mw
        if not claude_bridge.find_claude(mw.ledger.s.get("claude_path", "")):
            QMessageBox.information(self, "Claude not connected", mw.claude_help_text())
            return
        ses = mw.usage.get("session", {})
        if ses.get("pct", 0) >= 85:
            r = QMessageBox.question(self, "Close to your Claude limit",
                                     f"You've used {ses['pct']:.0f}% of your 5-hour Claude limit (resets {ses['resets']}). "
                                     f"A full scan is a big request and might hit the limit part-way. Start anyway?")
            if r != QMessageBox.Yes:
                return
        sid = self._new_run()
        handle = claude_bridge.RunHandle()
        self._handle = handle
        self._busy(True, "claude")
        self.banner.hide()
        self._progress(5, "Claude is running your trading skills (breadth, uptrend, sectors, screener, analysis) and "
                          "verifying every number… usually 4–10 minutes. You can cancel any time.")
        L = mw.ledger
        exclude = sorted({p["symbol"] for p in L.d["positions"]} | {o["symbol"] for o in L.pending()})
        budget = self.budget.value()
        holdings = ", ".join(f"{p['symbol']} ({p['shares']:g} sh, {p.get('sector', '')})" for p in L.d["positions"])
        holdings += (("; pending: " if holdings else "pending: ") + ", ".join(o["symbol"] for o in L.pending())
                     if L.pending() else "")
        prompt = claude_bridge.SCAN_PROMPT.format(
            equity=L.equity(mw.prices, mw.fx), cash=available_cash(mw), holdings=holdings or "nothing",
            budget=budget, risk_pct=L.s["risk_pct"], max_pos=L.s["max_position_pct"],
            max_risk=L.s["max_open_risk_pct"], fx=mw.fx,
            n=int(L.s["scan_size"]), exclude=", ".join(exclude) or "none", today=dt.date.today().isoformat(),
            data_howto=claude_bridge._DATA_HOWTO)
        tick = QTimer(self)
        started = time.time()
        tick.timeout.connect(lambda: sid == self._scan_id and self._kind == "claude" and self.status.setText(
            f"Claude is researching… {int(time.time() - started) // 60}m {int(time.time() - started) % 60:02d}s "
            f"elapsed. You can cancel any time."))
        tick.start(1000)

        def job(pr):
            j = claude_bridge.ask(prompt, L.s.get("claude_path", ""), 1500, 80, handle=handle)
            pr(85, "Claude replied. Double-checking its numbers against live prices…")
            return j, *verify_claude_picks(j.get("picks", []), int(L.s["max_per_sector"]),
                                           lambda: handle.cancelled)

        def done(res):
            tick.stop()
            mw.refresh_usage()
            if sid != self._scan_id:
                return
            j, picks, dropped = res
            self._busy(False)
            self._progress(100, f"Claude returned {len(j.get('picks', []))} idea(s); {len(picks)} passed the app's "
                                f"own checks." + (f" Dropped: {'; '.join(dropped)}" if dropped else ""))
            adv = j.get("advice") or {}
            kept = {p["symbol"] for p in picks}
            targets, notes = {}, []
            for a in adv.get("allocations") or []:
                sym = str(a.get("symbol", "")).upper()
                try:
                    amt = float(a.get("aud") or 0)
                except (TypeError, ValueError):
                    amt = 0.0
                if amt <= 0:
                    continue
                if sym in kept:
                    targets[sym] = amt
                    for p in picks:
                        if p["symbol"] == sym:
                            p["alloc_why"] = a.get("why", "")
                else:
                    notes.append(f"Claude suggested A${amt:,.0f} in {sym}, but it failed the app's checks, so that "
                                 f"money stays in cash.")
            plan = allocate_budget(mw, picks, budget, targets=targets)
            notes = plan["notes"] + notes
            self.show_picks(picks, AdviceCard(mw, adv, picks, budget, "claude", notes))
            mw.notify("Claude scan finished", f"{adv.get('headline') or 'Results ready'}\n"
                                              f"{len(picks)} idea(s) passed the app's checks"
                                              + (f" · {len(dropped)} dropped" if dropped else ""))
            self._show_banner(f"Claude · {j.get('posture', '')} (exposure ≤ {j.get('ceiling_pct', '?')}%)\n"
                              f"{j.get('summary', '')}", P.tertiary)

        def err(e):
            tick.stop()
            mw.refresh_usage()
            self._err_for(sid)(e)
        mw.run(job, done, err, lambda p, m: self._progress(p, m, sid))


from .verify import verify_claude_picks  # noqa: E402  (re-exported for tests)


class PositionCard(QFrame):
    def __init__(self, mw, p: dict):
        super().__init__()
        self.setObjectName("card")
        self.mw, self.p = mw, p
        fx = mw.fx
        px = mw.prices.get(p["symbol"], p["entry"])
        val = p["shares"] * px / fx
        cost = p["shares"] * p["entry"] / p["entry_fx"]
        pnl = val - cost + p.get("realized_aud", 0)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        top = QHBoxLayout()
        t = QLabel(p["symbol"])
        t.setFont(mono(20, QFont.Black))
        t.setStyleSheet(f"color:{P.primary}")
        top.addWidget(t)
        top.addWidget(label(p.get("name", "")[:30], px=12, color=P.muted))
        top.addWidget(Chip(p.get("sector", "") or "—"))
        days = mw.ledger.days_held(p)
        top.addWidget(Chip(f"{days:.0f}d held"))
        if p.get("half_taken"):
            top.addWidget(Chip("T1 hit · stop at breakeven", mix(P.green, P.bg, 0.75), P.green))
        if days > mw.ledger.s["max_hold_days"] and px < p["t1"]:
            top.addWidget(Chip("Time exit due", mix(P.red, P.bg, 0.75), P.red))
        top.addStretch(1)
        pl = QLabel(f"{aud(pnl, True)}  ({pnl / cost * 100 if cost else 0:+.2f}%)")
        pl.setFont(mono(18, QFont.Bold))
        pl.setStyleSheet(f"color:{pnl_color(pnl)}")
        top.addWidget(pl)
        lay.addLayout(top)
        mid = QHBoxLayout()
        mid.setSpacing(22)
        mid.addWidget(kv("SHARES", f"{p['shares']:g}"))
        mid.addWidget(kv("ENTRY", usd(p["entry"])))
        mid.addWidget(kv("NOW", usd(px)))
        mid.addWidget(kv("VALUE", aud(val)))
        mid.addWidget(kv("STOP", usd(p["stop"]), P.red))
        mid.addWidget(kv("RISK LEFT", aud(max(0, px - p["stop"]) * p["shares"] / fx)))
        mid.addStretch(1)
        v = view_button()
        v.clicked.connect(lambda: DetailDialog(mw, p["symbol"], {"entry": p["entry"], "stop": p["stop"], "t1": p["t1"],
                                                                 "t2": p["t2"]}, "open position").exec())
        s = ghost_button("Sell now")
        s.clicked.connect(self.sell)
        mid.addWidget(v)
        mid.addWidget(s)
        lay.addLayout(mid)
        lay.addWidget(PriceTrack(p["orig_stop"], p["entry"], p["t1"], p["t2"], px))

    def sell(self):
        px = self.mw.prices.get(self.p["symbol"])
        if not px:
            return
        r = QMessageBox.question(self, "Sell now", f"Sell {self.p['shares']:g} {self.p['symbol']} at about {usd(px)} "
                                                   f"(last price)? This is a paper trade.")
        if r == QMessageBox.Yes:
            self.mw.ledger.sell_now(self.p["id"], px, self.mw.fx)
            self.mw.toast(f"Sold {self.p['symbol']} at {usd(px)}", "info")
            self.mw.render_all()


class OrderCard(QFrame):
    def __init__(self, mw, o: dict):
        super().__init__()
        self.setObjectName("card")
        fx = mw.fx
        px = mw.prices.get(o["symbol"])
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(22)
        t = QLabel(o["symbol"])
        t.setFont(mono(20, QFont.Black))
        t.setStyleSheet(f"color:{P.primary}")
        lay.addWidget(t)
        lay.addWidget(Chip("Pending limit", mix(P.tertiary, P.bg, 0.75), P.tertiary))
        lay.addWidget(kv("SHARES", f"{o['shares']:g}"))
        lay.addWidget(kv("LIMIT", usd(o["limit"])))
        lay.addWidget(kv("NOW", usd(px) if px else "…"))
        if px:
            lay.addWidget(kv("TO FILL", f"{(o['limit'] / px - 1) * 100:+.2f}%"))
        lay.addWidget(kv("COST", aud(o["shares"] * o["limit"] / fx)))
        lay.addWidget(kv("STOP / T1 / T2", f"{o['stop']:.2f} / {o['t1']:.2f} / {o['t2']:.2f}"))
        hrs = (o["expires"] - time.time()) / 3600
        lay.addWidget(kv("EXPIRES", f"{hrs / 24:.1f}d" if hrs > 0 else "now"))
        lay.addStretch(1)
        v = view_button()
        v.clicked.connect(lambda: DetailDialog(mw, o["symbol"], {"entry": o["limit"], "stop": o["stop"], "t1": o["t1"],
                                                                 "t2": o["t2"]}, "pending order").exec())
        c = ghost_button("Cancel")
        c.clicked.connect(lambda: (mw.ledger.cancel_order(o["id"]), mw.toast(f"Cancelled {o['symbol']} order"),
                                   mw.render_all()))
        lay.addWidget(v)
        lay.addWidget(c)


class TradesPage(Page):
    title = "Trades"

    def __init__(self, mw):
        super().__init__(mw)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        wa, wp = QWidget(), QWidget()
        self.active = QVBoxLayout(wa)
        self.pend = QVBoxLayout(wp)
        for lay_, w, name in ((self.active, wa, "Active positions"), (self.pend, wp, "Pending orders")):
            lay_.setContentsMargins(0, 10, 6, 0)
            lay_.setSpacing(12)
            self.tabs.addTab(scroll_area(w), name)
        lay.addWidget(self.tabs)
        self._sig = None

    def render(self):
        L = self.mw.ledger
        with L.lock:
            pos = [dict(p) for p in L.d["positions"]]
            pend = [dict(o) for o in L.pending()]
        sig = (tuple((p["id"], round(self.mw.prices.get(p["symbol"], 0), 2), p["shares"]) for p in pos),
               tuple((o["id"], round(self.mw.prices.get(o["symbol"], 0), 2)) for o in pend))
        if sig == self._sig:
            return
        first = self._sig is None or len(sig[0]) != len(self._sig[0]) or len(sig[1]) != len(self._sig[1])
        self._sig = sig
        self.tabs.setTabText(0, f"Active positions ({len(pos)})")
        self.tabs.setTabText(1, f"Pending orders ({len(pend)})")
        for lay_, items, cls, empty in ((self.active, pos, PositionCard, "No open positions yet. Confirm an idea in the "
                                                                          "Scanner; filled orders show up here."),
                                        (self.pend, pend, OrderCard, "No pending orders.")):
            clear_layout(lay_)
            if not items:
                lay_.addWidget(label(empty, px=14, color=P.muted, wrap=True))
            for i, it in enumerate(items):
                c = cls(self.mw, it)
                lay_.addWidget(c)
                if first:
                    entrance(c, 70 * i)
            lay_.addStretch(1)


class HistoryPage(Page):
    title = "History"

    def __init__(self, mw):
        super().__init__(mw)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.c_n = StatCard("Closed trades", lambda v: f"{v:.0f}")
        self.c_w = StatCard("Win rate", lambda v: f"{v:.0f}%")
        self.c_r = StatCard("Realised P&L", lambda v: aud(v, True))
        self.c_ar = StatCard("Avg R multiple", lambda v: f"{v:+.2f}R")
        self.c_pf = StatCard("Profit factor", lambda v: "∞" if v > 1e6 else f"{v:.2f}")
        for c in (self.c_n, self.c_w, self.c_r, self.c_ar, self.c_pf):
            row.addWidget(c)
        lay.addLayout(row)
        h = QHBoxLayout()
        h.addWidget(label("Closed trades", role="h2"))
        h.addStretch(1)
        ex = ghost_button("Export CSV", "\U000F0207")
        ex.clicked.connect(self.export)
        h.addWidget(ex)
        lay.addLayout(h)
        inner = QWidget()
        self.list = QVBoxLayout(inner)
        self.list.setContentsMargins(0, 0, 6, 0)
        self.list.setSpacing(8)
        lay.addWidget(scroll_area(inner), 1)
        self._n = -1

    def render(self):
        L = self.mw.ledger
        st = L.stats()
        self.c_n.num.set_value(st["trades"])
        self.c_w.num.set_value(st["win_rate"])
        self.c_r.num.set_value(st["realized"], pnl_color(st["realized"]))
        self.c_ar.num.set_value(st["avg_r"], pnl_color(st["avg_r"]))
        self.c_pf.num.set_value(min(st["profit_factor"], 1e7))
        hist = list(L.d["history"])
        if len(hist) == self._n:
            return
        self._n = len(hist)
        clear_layout(self.list)
        if not hist:
            self.list.addWidget(label("No closed trades yet. When a stop, target or manual sell closes a position it "
                                      "lands here with its P&L, R multiple and max adverse/favourable move.", px=14,
                                      color=P.muted, wrap=True))
        for i, x in enumerate(reversed(hist)):
            f, fl = card(pad=12, horizontal=True)
            fl.setSpacing(22)
            t = QLabel(x["symbol"])
            t.setFont(mono(17, QFont.Black))
            t.setStyleSheet(f"color:{P.primary}")
            t.setFixedWidth(70)
            fl.addWidget(t)
            d0 = dt.datetime.fromtimestamp(x["opened"]).strftime("%d %b")
            d1 = dt.datetime.fromtimestamp(x["closed"]).strftime("%d %b %Y")
            fl.addWidget(kv("DATES", f"{d0} → {d1}", px=13))
            fl.addWidget(kv("ENTRY → EXIT", f"{x['entry']:.2f} → {x['exit']:.2f}", px=13))
            fl.addWidget(kv("P&L", aud(x["pnl_aud"], True), pnl_color(x["pnl_aud"]), 13))
            fl.addWidget(kv("RETURN", pct(x["pnl_pct"]), pnl_color(x["pnl_pct"]), 13))
            fl.addWidget(kv("R", f"{x['r_multiple']:+.2f}R", pnl_color(x["r_multiple"]), 13))
            fl.addWidget(kv("MAE / MFE", f"{x['mae_pct']:.1f}% / +{x['mfe_pct']:.1f}%", px=13))
            fl.addWidget(Chip(x["reason"]))
            fl.addStretch(1)
            v = view_button()
            v.clicked.connect(lambda _=False, x=x: DetailDialog(
                self.mw, x["symbol"], {"entry": x["entry"], "stop": x["stop"], "t1": x["t1"], "t2": x["t2"]},
                "closed trade", exits=[{"x": x["closed"], "y": x["exit"], "color": pnl_color(x["pnl_aud"]),
                                        "text": f" exit {x['exit']:.2f}", "symbol": "t"}]).exec())
            fl.addWidget(v)
            self.list.addWidget(f)
            if i < 12:
                entrance(f, 50 * i, 12, 320)
        self.list.addStretch(1)

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export trade history", os.path.expanduser("~/paper_trades.csv"),
                                              "CSV (*.csv)")
        if not path:
            return
        hist = self.mw.ledger.d["history"]
        keys = ["symbol", "sector", "opened", "closed", "shares", "entry", "exit", "entry_fx", "exit_fx", "pnl_aud",
                "pnl_pct", "r_multiple", "reason", "mae_pct", "mfe_pct", "stop", "t1", "t2"]
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(keys)
            for x in hist:
                row = [x.get(k) for k in keys]
                row[2] = dt.datetime.fromtimestamp(x["opened"]).isoformat(timespec="minutes")
                row[3] = dt.datetime.fromtimestamp(x["closed"]).isoformat(timespec="minutes")
                w.writerow(row)
        self.mw.toast(f"Exported {len(hist)} trades to {path}")


class SettingsPage(Page):
    title = "Settings"
    FIELDS = [("risk_pct", "Risk per trade (% of equity)", 0.1, 5, 0.1, 1),
              ("max_position_pct", "Max position size (% of equity)", 5, 100, 1, 0),
              ("max_open_risk_pct", "Max total open risk (% of equity)", 1, 20, 0.5, 1),
              ("max_per_sector", "Max trades per sector", 1, 10, 1, 0),
              ("order_expiry_days", "Pending orders expire after (trading days)", 1, 60, 1, 0),
              ("max_hold_days", "Time-exit warning after (days held)", 5, 365, 1, 0),
              ("scan_size", "Ideas per scan", 3, 20, 1, 0),
              ("refresh_seconds", "Price refresh interval (seconds)", 30, 3600, 10, 0)]

    def __init__(self, mw):
        super().__init__(mw)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(14)
        rf, rl = card(pad=18)
        rl.addWidget(label("Risk & trading rules", role="h2"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        self.inputs = {}
        for i, (k, name, lo, hi, step, dec) in enumerate(self.FIELDS):
            grid.addWidget(label(name, px=13), i, 0)
            sp = QDoubleSpinBox() if dec else QSpinBox()
            if dec:
                sp.setDecimals(dec)
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setValue(mw.ledger.s[k])
            sp.setFixedWidth(120)
            sp.setFont(mono(13))
            grid.addWidget(sp, i, 1)
            self.inputs[k] = sp
        rl.addLayout(grid)
        rl.addWidget(label("Notifications", px=14, bold=True))
        self.chk_scans = QCheckBox("Desktop notification when a scan or Claude estimate finishes")
        self.chk_scans.setChecked(bool(mw.ledger.s.get("notify_scans", True)))
        self.chk_trades = QCheckBox("Desktop notification when an order fills or a stop/target closes a trade")
        self.chk_trades.setChecked(bool(mw.ledger.s.get("notify_trades", True)))
        tb = ghost_button("Send test notification", "\U000F009A", px=12)
        tb.clicked.connect(lambda: notify.send("Paper Trader", "Notifications are working. Click Open to jump back "
                                                               "to the app.") or mw.toast("notify-send not found", "error"))
        rl.addWidget(self.chk_scans)
        rl.addWidget(self.chk_trades)
        rl.addWidget(tb, 0, Qt.AlignLeft)
        sv = primary_button("Save settings", "\U000F0193")
        sv.clicked.connect(self.save)
        rl.addWidget(sv, 0, Qt.AlignRight)
        lay.addWidget(rf)

        cf, cl = card(pad=18)
        hh = QHBoxLayout()
        ic = QLabel(ICONS["claude"])
        ic.setFont(icon_font(22))
        ic.setStyleSheet(f"color:{P.tertiary}")
        hh.addWidget(ic)
        hh.addWidget(label("Claude connection", role="h2"))
        hh.addStretch(1)
        cl.addLayout(hh)
        self.claude_status = label("", px=13, wrap=True)
        self.claude_status.setTextFormat(Qt.RichText)
        cl.addWidget(self.claude_status)
        pr = QHBoxLayout()
        pr.addWidget(label("CLI path (optional)", px=13))
        self.path = QLineEdit(mw.ledger.s.get("claude_path", ""))
        self.path.setPlaceholderText("auto-detect (claude on PATH, ~/.local/bin/claude …)")
        pr.addWidget(self.path, 1)
        test = ghost_button("Test", ICONS["claude"])
        test.clicked.connect(self.test_claude)
        pr.addWidget(test)
        cl.addLayout(pr)
        lay.addWidget(cf)

        pf, pl = card(pad=18)
        ph = QHBoxLayout()
        pi = QLabel("\U000F011C")
        pi.setFont(icon_font(22))
        pi.setStyleSheet(f"color:{P.primary}")
        ph.addWidget(pi)
        ph.addWidget(label("iPhone companion", role="h2"))
        ph.addStretch(1)
        self.srv_chip = Chip("")
        ph.addWidget(self.srv_chip)
        pl.addLayout(ph)
        pl.addWidget(label("Lets the Paper Trader iPhone app run Claude scans, estimates and usage through this PC. "
                           "Use it on the same Wi-Fi, or anywhere via Tailscale. Anyone with the token on your network "
                           "can start Claude jobs, so keep it private.", px=12, color=P.faint, wrap=True))
        self.srv_enable = QCheckBox("Run the companion server while Paper Trader is open")
        self.srv_enable.setChecked(bool(mw.ledger.s.get("server_enabled")))
        self.srv_enable.toggled.connect(self._toggle_server)
        pl.addWidget(self.srv_enable)
        r1 = QHBoxLayout()
        r1.addWidget(label("Port", px=13))
        self.srv_port = QSpinBox()
        self.srv_port.setRange(1024, 65535)
        self.srv_port.setValue(int(mw.ledger.s.get("server_port", 8787)))
        self.srv_port.setFixedWidth(100)
        self.srv_port.setFont(mono(13))
        r1.addWidget(self.srv_port)
        r1.addSpacing(16)
        r1.addWidget(label("Token", px=13))
        self.srv_token = QLineEdit(mw.ledger.s.get("server_token", ""))
        self.srv_token.setReadOnly(True)
        self.srv_token.setFont(mono(13))
        r1.addWidget(self.srv_token, 1)
        cp = ghost_button("Copy", "\U000F018F", px=12)
        cp.clicked.connect(lambda: (QApplication.clipboard().setText(self.srv_token.text()), mw.toast("Token copied")))
        r1.addWidget(cp)
        rg = ghost_button("New token", ICONS["refresh"], px=12)
        rg.clicked.connect(self._new_token)
        r1.addWidget(rg)
        pl.addLayout(r1)
        self.srv_addr = label("", px=12, color=P.muted, wrap=True)
        self.srv_addr.setTextFormat(Qt.RichText)
        self.srv_addr.setTextInteractionFlags(Qt.TextSelectableByMouse)
        pl.addWidget(self.srv_addr)
        lay.addWidget(pf)

        af, al = card(pad=18)
        al.addWidget(label("Reset account", role="h2"))
        al.addWidget(label("Erases every position, pending order, closed trade, activity entry and the equity curve, "
                           "then starts again with the balance below.", px=12, color=P.faint, wrap=True))
        rr = QHBoxLayout()
        rr.addWidget(label("Starting balance (A$)", px=13))
        self.start_amt = QDoubleSpinBox()
        self.start_amt.setDecimals(2)
        self.start_amt.setRange(100, 100_000_000)
        self.start_amt.setSingleStep(1000)
        self.start_amt.setGroupSeparatorShown(True)
        self.start_amt.setValue(float(mw.ledger.d.get("starting_cash", 10000)))
        self.start_amt.setFixedWidth(170)
        self.start_amt.setFont(mono(13))
        rr.addWidget(self.start_amt)
        for amt in (1000, 10000, 50000, 100000):
            q = ghost_button(f"{amt // 1000}k", px=12)
            q.clicked.connect(lambda _=False, a=amt: self.start_amt.setValue(a))
            rr.addWidget(q)
        rr.addStretch(1)
        al.addLayout(rr)
        self.reset_settings = QCheckBox("Also reset risk rules and settings to defaults")
        al.addWidget(self.reset_settings)
        rs = AnimButton("Reset everything", P.red, P.on_red, mix(P.red, "#ffffff", 0.2), icon="\U000F0450")
        rs.clicked.connect(self.reset)
        al.addWidget(rs, 0, Qt.AlignLeft)
        al.addWidget(label(f"Ledger file: {engine.LEDGER}", px=11, color=P.faint))
        lay.addWidget(af)
        lay.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_area(inner))

    def _new_token(self):
        L = self.mw.ledger
        L.s["server_token"] = server.new_token()
        L.save()
        self.srv_token.setText(L.s["server_token"])
        if self.mw.server:
            self.mw.start_server()
        self.mw.toast("New token created; update it in the iPhone app")
        self.render()

    def _toggle_server(self, on):
        L = self.mw.ledger
        L.s["server_enabled"] = bool(on)
        L.s["server_port"] = self.srv_port.value()
        L.save()
        (self.mw.start_server() if on else self.mw.stop_server())
        self.render()

    def render_server(self):
        L, srv = self.mw.ledger, self.mw.server
        self.srv_token.setText(L.s.get("server_token", ""))
        if srv:
            self.srv_chip.set("● running", mix(P.green, P.bg, 0.78), P.green)
            urls = "<br>".join(f"<code>http://{ip}:{srv.port}</code>" for ip in server.local_addresses()) or "?"
            self.srv_addr.setText(f"In the iPhone app → Settings → PC companion, enter one of these addresses and the "
                                  f"token:<br>{urls}<br><span style='color:{P.faint}'>100.x.x.x addresses are "
                                  f"Tailscale (work away from home).</span>")
        else:
            self.srv_chip.set(self.mw.server_error or "off", P.card_hi, P.red if self.mw.server_error else P.faint)
            self.srv_addr.setText("")

    def render(self):
        self.render_server()
        exe = claude_bridge.find_claude(self.mw.ledger.s.get("claude_path", ""))
        if exe:
            self.claude_status.setText(f"<span style='color:{P.green}'>● Connected</span> &nbsp; {exe}<br>"
                                       f"<span style='color:{P.faint}'>Scans and estimates run through "
                                       f"<code>claude -p</code> with your trading skills. Claude only suggests; "
                                       f"you press Confirm.</span>")
        else:
            self.claude_status.setText(self.mw.claude_help_html())

    def save(self):
        for k, sp in self.inputs.items():
            self.mw.ledger.s[k] = sp.value()
        self.mw.ledger.s["notify_scans"] = self.chk_scans.isChecked()
        self.mw.ledger.s["notify_trades"] = self.chk_trades.isChecked()
        self.mw.ledger.s["claude_path"] = self.path.text().strip()
        self.mw.ledger.save()
        self.mw.timer.setInterval(int(self.mw.ledger.s["refresh_seconds"]) * 1000)
        self.mw.toast("Settings saved")
        self.mw.render_all()

    def test_claude(self):
        self.mw.ledger.s["claude_path"] = self.path.text().strip()
        exe = claude_bridge.find_claude(self.mw.ledger.s["claude_path"])
        if not exe:
            self.render()
            return
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
            self.mw.toast(f"Claude CLI OK: {out}", "fill")
        except Exception as e:  # noqa: BLE001
            self.mw.toast(f"Claude test failed: {e}", "error")
        self.render()

    def reset(self):
        amt = self.start_amt.value()
        L = self.mw.ledger
        extra = "\n\nRisk rules and settings will also go back to defaults." if self.reset_settings.isChecked() else ""
        r = QMessageBox.warning(
            self, "Reset paper account",
            f"Erase {len(L.d['positions'])} position(s), {len(L.pending())} pending order(s), "
            f"{len(L.d['history'])} closed trade(s) and all activity, and start again with A${amt:,.2f}?"
            f"{extra}\n\nThis can't be undone.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        L.reset(amt, self.reset_settings.isChecked())
        for k, sp in self.inputs.items():
            sp.setValue(L.s[k])
        self.path.setText(L.s.get("claude_path", ""))
        self.reset_settings.setChecked(False)
        self.mw.pages[1].clear_results()
        self.mw.timer.setInterval(int(L.s["refresh_seconds"]) * 1000)
        self.mw.toast(f"Paper account reset to A${amt:,.2f}", "info")
        self.mw.render_all()


# ---------------------------------------------------------------- main window
class NavButton(QPushButton):
    def __init__(self, icon, text):
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(44)
        self.setFlat(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 12, 0)
        lay.setSpacing(12)
        self.ic = QLabel(icon)
        self.ic.setFont(icon_font(19))
        self.tx = QLabel(text)
        self.tx.setStyleSheet("font-size:14px;font-weight:600")
        lay.addWidget(self.ic)
        lay.addWidget(self.tx, 1)
        self.setStyleSheet("QPushButton{background:transparent;border:none;}")
        self.set_active(False)

    def set_active(self, on):
        col = P.on_primary_container if on else P.muted
        self.ic.setStyleSheet(f"color:{P.primary if on else P.faint}")
        self.tx.setStyleSheet(f"font-size:14px;font-weight:{700 if on else 600};color:{col}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 880)
        self.pool = QThreadPool.globalInstance()
        self._sigs = set()
        self.ledger = engine.Ledger()
        self.prices: dict[str, float] = {}
        self.fx = 0.65
        self.posture = None
        self.scan_results = []
        self.toasts: list[Toast] = []
        self._refreshing = False
        self.server = None
        self.server_error = ""

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(220)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 18, 12, 14)
        sl.setSpacing(4)
        brand = QHBoxLayout()
        lg = QLabel(ICONS["logo"])
        lg.setFont(icon_font(26))
        lg.setStyleSheet(f"color:{P.primary}")
        brand.addWidget(lg)
        bt = QVBoxLayout()
        bt.setSpacing(0)
        bt.addWidget(label("Paper Trader", px=17, bold=True))
        bt.addWidget(label("A$ paper desk", px=11, color=P.faint))
        brand.addLayout(bt)
        brand.addStretch(1)
        sl.addLayout(brand)
        sl.addSpacing(18)
        self.nav_holder = QWidget()
        nl = QVBoxLayout(self.nav_holder)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(4)
        self.indicator = SlidingIndicator(self.nav_holder)
        self.navs = []
        for key, text in (("dash", "Dashboard"), ("scan", "Scanner"), ("trades", "Trades"), ("hist", "History"),
                          ("set", "Settings")):
            b = NavButton(ICONS[key], text)
            b.clicked.connect(lambda _=False, i=len(self.navs): self.go(i))
            nl.addWidget(b)
            self.navs.append(b)
        sl.addWidget(self.nav_holder)
        sl.addStretch(1)
        self.usage_box = QFrame()
        self.usage_box.setObjectName("card")
        ul = QVBoxLayout(self.usage_box)
        ul.setContentsMargins(12, 10, 12, 8)
        ul.setSpacing(4)
        uh = QHBoxLayout()
        uh.addWidget(label("CLAUDE USAGE", px=10, color=P.faint, bold=True))
        uh.addStretch(1)
        self.usage_spin = Spinner(14)
        uh.addWidget(self.usage_spin)
        ub = QPushButton(ICONS["refresh"])
        ub.setFont(icon_font(13))
        ub.setFlat(True)
        ub.setCursor(Qt.PointingHandCursor)
        ub.setToolTip("Refresh (uses Claude Code's /usage; costs nothing)")
        ub.setStyleSheet(f"QPushButton{{border:none;background:transparent;color:{P.faint}}}"
                         f"QPushButton:hover{{color:{P.primary}}}")
        ub.clicked.connect(self.refresh_usage)
        uh.addWidget(ub)
        ul.addLayout(uh)
        self.usage_session = UsageBar("5-hour session")
        self.usage_week = UsageBar("Weekly limit")
        ul.addWidget(self.usage_session)
        ul.addWidget(self.usage_week)
        self.usage_msg = label("", px=10, color=P.faint, wrap=True)
        self.usage_msg.hide()
        ul.addWidget(self.usage_msg)
        self.usage_box.hide()
        self.usage = {}
        self._usage_busy = False
        sl.addWidget(self.usage_box)
        sl.addSpacing(6)
        self.claude_chip = Chip("")
        sl.addWidget(self.claude_chip)
        sl.addSpacing(4)
        sl.addWidget(label("Paper money only · not financial advice", px=10, color=P.faint, wrap=True))
        h.addWidget(side)

        content = QVBoxLayout()
        content.setContentsMargins(24, 18, 24, 18)
        content.setSpacing(16)
        hdr = QHBoxLayout()
        self.page_title = label("Dashboard", role="h1")
        hdr.addWidget(self.page_title)
        hdr.addStretch(1)
        self.dot = PulseDot(P.faint)
        hdr.addWidget(self.dot)
        self.mkt = label("", px=13, color=P.muted)
        hdr.addWidget(self.mkt)
        hdr.addSpacing(14)
        self.fx_lbl = Chip("AUD/USD …")
        hdr.addWidget(self.fx_lbl)
        self.upd = label("", px=12, color=P.faint)
        hdr.addWidget(self.upd)
        self.ref_spin = Spinner(20)
        hdr.addWidget(self.ref_spin)
        rb = ghost_button("Refresh", ICONS["refresh"])
        rb.clicked.connect(self.refresh)
        hdr.addWidget(rb)
        content.addLayout(hdr)
        self.stack = FadeStack()
        self.pages = [DashboardPage(self), ScannerPage(self), TradesPage(self), HistoryPage(self), SettingsPage(self)]
        for p in self.pages:
            self.stack.addWidget(p)
        content.addWidget(self.stack, 1)
        h.addLayout(content, 1)

        self.timer = QTimer(self, interval=int(self.ledger.s["refresh_seconds"]) * 1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.clock = QTimer(self, interval=30000)
        self.clock.timeout.connect(self.tick)
        self.clock.start()
        self.usage_timer = QTimer(self, interval=5 * 60 * 1000)
        self.usage_timer.timeout.connect(self.refresh_usage)
        self.usage_timer.start()
        QTimer.singleShot(1500, self.refresh_usage)
        if self.ledger.s.get("server_enabled"):
            QTimer.singleShot(800, self.start_server)
        for i in range(5):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self.go(i))
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.refresh)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=lambda: (self.go(1), self.pages[1].scan()))
        self.tick()
        self.update_claude_chip()
        QTimer.singleShot(50, lambda: self.go(0, animate=False))
        QTimer.singleShot(300, self.refresh)
        QTimer.singleShot(600, self.load_posture)

    # ----- infra
    def run(self, fn, done=None, err=None, prog=None):
        sig = _Sig()
        self._sigs.add(sig)
        if done:
            sig.done.connect(done)
        sig.err.connect(err or (lambda e: None if e == CANCELLED else self.toast(e, "error")))
        if prog:
            sig.prog.connect(prog)
        sig.done.connect(lambda _: self._sigs.discard(sig))
        sig.err.connect(lambda _: self._sigs.discard(sig))
        self.pool.start(Job(fn, sig))

    def toast(self, text, kind="info"):
        t = Toast(self.centralWidget(), text, kind)
        t.closed.connect(self._toast_closed)
        self.toasts.append(t)
        self._place_toasts(new=t)

    def _toast_closed(self, t):
        if t in self.toasts:
            self.toasts.remove(t)
        self._place_toasts()

    def _place_toasts(self, new=None):
        y = self.centralWidget().height() - 20
        for t in reversed(self.toasts):
            t.adjustSize()
            y -= t.height()
            pos = self.centralWidget().rect().topLeft()
            from PySide6.QtCore import QPoint
            p = QPoint(self.centralWidget().width() - t.width() - 24, y)
            if t is new:
                t.show_at(p)
            else:
                t.move(p)
            y -= 10

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_toasts()
        QTimer.singleShot(0, lambda: self._move_indicator(False))

    def _move_indicator(self, animate=True):
        b = self.navs[self.stack.currentIndex()]
        self.indicator.move_to(QRect(b.x(), b.y(), b.width(), b.height()), animate)
        self.indicator.lower()

    def go(self, i, animate=True):
        for j, b in enumerate(self.navs):
            b.set_active(j == i)
        self.stack.go(i) if animate else self.stack.setCurrentIndex(i)
        self.page_title.setText(self.pages[i].title)
        self._move_indicator(animate)
        self.pages[i].render()

    def render_all(self):
        for p in self.pages:
            try:
                p.render()
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    def tick(self):
        live, txt = data.market_status()
        self.mkt.setText(txt)
        self.dot.set_live(live, P.green if live else P.faint)

    def update_claude_chip(self):
        exe = claude_bridge.find_claude(self.ledger.s.get("claude_path", ""))
        if exe:
            self.claude_chip.set(f"{ICONS['claude']}  Claude connected", mix(P.tertiary, P.bg, 0.78), P.tertiary)
        else:
            self.claude_chip.set(f"{ICONS['claude']}  Claude CLI not found", P.card_hi, P.faint)
        self.claude_chip.setToolTip(exe or self.claude_help_text())

    def start_server(self):
        self.stop_server()
        s = self.ledger.s
        if not s.get("server_token"):
            s["server_token"] = server.new_token()
            self.ledger.save()
        try:
            self.server = server.CompanionServer(int(s.get("server_port", 8787)), s["server_token"],
                                                 s.get("claude_path", "")).start()
            self.server_error = ""
        except OSError as e:
            self.server, self.server_error = None, f"failed: {e.strerror or e}"
            self.toast(f"Companion server couldn't start: {e}", "error")
        self.pages[4].render_server()

    def stop_server(self):
        if self.server:
            self.server.stop()
            self.server = None
        self.pages[4].render_server()

    def closeEvent(self, e):
        self.stop_server()
        super().closeEvent(e)

    def notify(self, title, body="", kind="scan", urgency="normal"):
        key = "notify_trades" if kind == "trade" else "notify_scans"
        if self.ledger.s.get(key, True):
            notify.send(title, body, urgency)

    def refresh_usage(self):
        path = self.ledger.s.get("claude_path", "")
        if not claude_bridge.find_claude(path):
            self.usage_box.hide()
            return
        if self._usage_busy:
            return
        self._usage_busy = True
        self.usage_spin.start()

        def done(rows):
            self._usage_busy = False
            self.usage_spin.stop()
            self.usage = {r["key"]: r for r in rows}
            ses, wk = self.usage.get("session"), self.usage.get("week")
            if ses:
                self.usage_session.set_value(ses["pct"], ses["resets"])
            if wk:
                self.usage_week.set_value(wk["pct"], wk["resets"])
                extra = [r for r in rows if r["key"].startswith("week:")]
                if extra:
                    self.usage_week.setToolTip(self.usage_week.toolTip() + "\n" + "\n".join(
                        f"{r['label']}: {r['pct']:.0f}% used, resets {r['resets']}" for r in extra))
            self.usage_msg.hide()
            if not self.usage_box.isVisible():
                self.usage_box.show()
                entrance(self.usage_box, 0, 10, 400)

        def err(e):
            self._usage_busy = False
            self.usage_spin.stop()
            self.usage_msg.setText(f"Couldn't read usage: {e[:120]}")
            self.usage_msg.show()
            self.usage_box.show()
        self.run(lambda pr: claude_bridge.usage(path), done, err)

    def claude_help_text(self):
        return ("The app talks to Claude through the Claude Code command-line tool (claude -p).\n\n"
                "Install it, then sign in once:\n"
                "  curl -fsSL https://claude.ai/install.sh | bash\n"
                "  claude        (sign in with your Claude account, then exit)\n\n"
                "Until then, the built-in scanner and model forecasts work without Claude.")

    def claude_help_html(self):
        return (f"<span style='color:{P.primary}'>● Claude CLI not found</span><br>"
                f"<span style='color:{P.muted}'>Install Claude Code and sign in once:<br>"
                f"<code>curl -fsSL https://claude.ai/install.sh | bash</code><br><code>claude</code> "
                f"(sign in, then exit)<br>The built-in scanner and model forecasts keep working without it.</span>")

    def load_posture(self, force=False):
        dp = self.pages[0]
        dp.post_spin.start()

        def done(p):
            dp.post_spin.stop()
            self.posture = p
            dp.render_posture()

        def err(e):
            dp.post_spin.stop()
            dp.post_detail.setText(f"Couldn't run the market skills: {e}")
        self.run(lambda pr: scanner.market_posture(force), done, err)

    def refresh(self):
        if self._refreshing:
            return
        self._refreshing = True
        self.ref_spin.start()
        L = self.ledger
        syms = sorted({p["symbol"] for p in L.d["positions"]} | {o["symbol"] for o in L.pending()})

        def job(pr):
            fx = data.audusd(ttl=120)
            intraday, prices, prev = {}, {}, {}
            for s in syms:
                try:
                    intraday[s] = data.history(s, "1mo", "15m", ttl=45)
                    d = data.history(s, "5d", "1d", ttl=45)
                    prices[s] = float(intraday[s].price if np.isfinite(intraday[s].price) else d.c[-1])
                    prev[s] = float(d.c[-2]) if len(d.c) > 1 else prices[s]
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
            events = L.process(intraday, {**self.prices, **prices}, fx)
            return fx, prices, events

        def done(res):
            fx, prices, events = res
            self.fx = fx
            self.prices.update(prices)
            self._refreshing = False
            self.ref_spin.stop()
            self.fx_lbl.set(f"AUD/USD {fx:.4f}")
            self.upd.setText(f"updated {dt.datetime.now():%H:%M}")
            kinds = {e["text"]: e["kind"] for e in L.d["events"][-len(events):]} if events else {}
            for t in events:
                self.toast(t, kinds.get(t, "info"))
                if kinds.get(t) in ("fill", "win", "loss"):
                    title = {"fill": "Order filled", "win": "Position closed · profit",
                             "loss": "Position closed · loss"}[kinds[t]]
                    self.notify(title, t, kind="trade")
            self.update_claude_chip()
            self.render_all()

        def err(e):
            self._refreshing = False
            self.ref_spin.stop()
            self.toast(f"Refresh failed: {e}", "error")
        self.run(job, done, err)


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland;xcb")
    app = QApplication(sys.argv)
    app.setApplicationName("papertrader")
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName("papertrader")
    from PySide6.QtGui import QIcon
    icon_svg = os.path.join(os.path.dirname(__file__), "assets", "papertrader.svg")
    icon_png = os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps/papertrader.png")
    app.setWindowIcon(QIcon(icon_png if os.path.exists(icon_png) else icon_svg))
    f = QFont(P.font)
    f.setPixelSize(14)
    app.setFont(f)
    app.setStyleSheet(stylesheet())
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
