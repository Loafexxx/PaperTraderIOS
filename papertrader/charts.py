"""pyqtgraph charts: price history + forecast cone + plan levels, and the account equity curve."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEasingCurve, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QLinearGradient, QBrush

from .data import US_HOLIDAYS
from .theme import P, mix

pg.setConfigOptions(antialias=True, background=None, foreground=P.faint)
NY = ZoneInfo("America/New_York")


def _qc(hex_, alpha=255):
    c = QColor(hex_)
    c.setAlpha(alpha)
    return c


def _future_bdays(last_ts: float, n: int) -> np.ndarray:
    d = dt.datetime.fromtimestamp(last_ts, NY)
    out = []
    while len(out) < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 and d.date() not in US_HOLIDAYS:
            out.append(d.timestamp())
    return np.array(out)


def _style(plot: pg.PlotItem):
    for ax in ("left", "bottom"):
        a = plot.getAxis(ax)
        a.setPen(pg.mkPen(_qc(P.outline)))
        a.setTextPen(pg.mkPen(_qc(P.faint)))
        f = QFont(P.mono)
        f.setPixelSize(11)
        a.setStyle(tickFont=f, tickLength=-5)
    plot.showGrid(x=False, y=True, alpha=0.12)
    plot.getViewBox().setMouseEnabled(x=True, y=False)
    plot.hideButtons()


class Crosshair:
    """Hover crosshair with a tooltip. `lookup(x)` returns (snap_x, snap_y, html) or None."""

    def __init__(self, pw: pg.PlotWidget, lookup):
        self.pw, self.lookup = pw, lookup
        pen = pg.mkPen(_qc(P.muted, 110), width=1, style=Qt.DashLine)
        self.v = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self.h = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        self.dot = pg.ScatterPlotItem(size=11, brush=pg.mkBrush(_qc(P.primary)), pen=pg.mkPen(_qc(P.bg), width=2))
        self.tip = pg.TextItem(html="", fill=pg.mkBrush(_qc(P.card_hi, 238)), border=pg.mkPen(_qc(P.outline)),
                               anchor=(0, 1))
        self.items = (self.v, self.h, self.dot, self.tip)
        for it in self.items:
            it.setZValue(1000)
        pw.scene().sigMouseMoved.connect(self._moved)
        self.hide()

    def attach(self):
        for it in self.items:
            if it.scene() is None:
                self.pw.addItem(it, ignoreBounds=True)
        self.hide()

    def hide(self):
        for it in self.items:
            it.setVisible(False)

    def _moved(self, pos):
        vb = self.pw.getPlotItem().getViewBox()
        if self.tip.scene() is None or not vb.sceneBoundingRect().contains(pos):
            self.hide()
            return
        pt = vb.mapSceneToView(pos)
        r = self.lookup(pt.x())
        if r is None:
            self.hide()
            return
        x, y, html = r
        self.v.setPos(x)
        self.h.setPos(y if y is not None else pt.y())
        if y is not None:
            self.dot.setData([x], [y])
        self.tip.setHtml(html)
        (x0, x1), (y0, y1) = vb.viewRange()
        ax = 1.0 if (pt.x() - x0) / max(x1 - x0, 1e-9) > 0.62 else 0.0
        ay = 0.0 if (pt.y() - y0) / max(y1 - y0, 1e-9) > 0.6 else 1.0
        self.tip.setAnchor((ax, ay))
        self.tip.setPos(x, pt.y())
        for it in self.items:
            it.setVisible(True)
        self.dot.setVisible(y is not None)


def _tip(head: str, lines: list[str]) -> str:
    body = "<br>".join(lines)
    return (f"<div style='padding:2px 4px;font-family:\"{P.mono}\";font-size:12px;color:{P.fg}'>"
            f"<span style='color:{P.faint}'>{head}</span><br>{body}</div>")


class PriceChart(pg.PlotWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent, axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
        self.setBackground(_qc(P.card))
        self.plot_item = self.getPlotItem()
        _style(self.plot_item)
        self.plot_item.setMenuEnabled(False)
        self._anim = None
        self._extra = []
        self._ts = self._c = self._fut = self._fc = self._s50 = None
        self.cross = Crosshair(self, self._lookup)

    def leaveEvent(self, e):
        self.cross.hide()
        super().leaveEvent(e)

    def _lookup(self, x):
        ts, c = self._ts, self._c
        if ts is None or not len(ts):
            return None
        if self._fut is not None and x > ts[-1]:
            fut, fc = self._fut, self._fc
            i = int(np.clip(np.abs(fut - x).argmin(), 1, len(fut) - 1))
            d = dt.datetime.fromtimestamp(fut[i], NY)
            return fut[i], float(fc.p50[i]), _tip(
                f"{d:%a %d %b %Y} · +{i} trading days (model)",
                [f"<b style='color:{P.tertiary}'>Median  {fc.p50[i]:,.2f}</b>",
                 f"25–75%  {fc.p25[i]:,.2f} – {fc.p75[i]:,.2f}", f"10–90%  {fc.p10[i]:,.2f} – {fc.p90[i]:,.2f}"])
        i = int(np.clip(np.searchsorted(ts, x), 0, len(ts) - 1))
        if i > 0 and abs(ts[i - 1] - x) < abs(ts[i] - x):
            i -= 1
        d = dt.datetime.fromtimestamp(ts[i], NY)
        chg = (c[i] / c[i - 1] - 1) * 100 if i > 0 else 0.0
        col = P.green if chg >= 0 else P.red
        lines = [f"<b style='color:{P.primary}'>Close  US${c[i]:,.2f}</b>  <span style='color:{col}'>{chg:+.2f}%</span>"]
        if self._s50 is not None and np.isfinite(self._s50[i]):
            lines.append(f"50-day avg  {self._s50[i]:,.2f}")
        lines.append(f"vs last close  {(c[-1] / c[i] - 1) * 100:+.1f}%")
        return ts[i], float(c[i]), _tip(f"{d:%a %d %b %Y}", lines)

    def show_data(self, ts, closes, fc=None, plan: dict | None = None, horizon_label="", marks: list | None = None):
        self.clear()
        self._extra.clear()
        self._fut = None
        ts, closes = np.asarray(ts, float), np.asarray(closes, float)
        n = len(ts)
        line = self.plot(ts[:1], closes[:1], pen=pg.mkPen(_qc(P.primary), width=2.2), fillLevel=float(np.nanmin(closes)) * 0.97,
                         brush=pg.mkBrush(_qc(P.primary, 28)))
        # Moving averages
        from .forecast import sma_series
        s50 = sma_series(closes, 50)
        self._ts, self._c, self._s50, self._fc = ts, closes, s50, fc
        self.plot(ts, s50, pen=pg.mkPen(_qc(P.tertiary, 150), width=1.2, style=Qt.DashLine))
        s200 = sma_series(closes, 200)
        if np.isfinite(s200).any():
            self.plot(ts, s200, pen=pg.mkPen(_qc(P.muted, 110), width=1.2, style=Qt.DotLine))

        fut = None
        items_fc = []
        if fc is not None:
            fut = np.concatenate([[ts[-1]], _future_bdays(ts[-1], len(fc.days) - 1)])
            self._fut = fut
            c90 = pg.PlotDataItem(fut, fc.p90, pen=pg.mkPen(None))
            c10 = pg.PlotDataItem(fut, fc.p10, pen=pg.mkPen(None))
            c75 = pg.PlotDataItem(fut, fc.p75, pen=pg.mkPen(None))
            c25 = pg.PlotDataItem(fut, fc.p25, pen=pg.mkPen(None))
            outer = pg.FillBetweenItem(c10, c90, brush=pg.mkBrush(_qc(P.tertiary, 40)))
            inner = pg.FillBetweenItem(c25, c75, brush=pg.mkBrush(_qc(P.tertiary, 75)))
            med = pg.PlotDataItem(fut, fc.p50, pen=pg.mkPen(_qc(P.tertiary), width=2, style=Qt.DashLine))
            for it in (c90, c10, c75, c25, outer, inner, med):
                self.addItem(it)
                it.setOpacity(0.0)
                items_fc.append(it)
            today = pg.InfiniteLine(pos=ts[-1], angle=90, pen=pg.mkPen(_qc(P.outline), width=1, style=Qt.DashLine))
            self.addItem(today)

        if plan:
            for key, name, col in (("stop", "Stop", P.red), ("entry", "Entry", P.muted), ("t1", "T1", P.green),
                                   ("t2", "T2", P.primary)):
                v = plan.get(key)
                if v:
                    il = pg.InfiniteLine(pos=v, angle=0, pen=pg.mkPen(_qc(col, 170), width=1.2, style=Qt.DashLine),
                                         label=f"{name} {v:,.2f}", labelOpts={"position": 0.02, "color": _qc(col),
                                                                              "fill": _qc(P.card, 200),
                                                                              "anchors": [(0, 1), (0, 1)]})
                    self.addItem(il)
        for m in marks or []:
            sc = pg.ScatterPlotItem([m["x"]], [m["y"]], size=13, brush=pg.mkBrush(_qc(m["color"])),
                                    pen=pg.mkPen(_qc(P.bg), width=2), symbol=m.get("symbol", "o"))
            self.addItem(sc)
            t = pg.TextItem(m["text"], color=_qc(m["color"]), anchor=(0, 0.5))
            t.setPos(m["x"], m["y"])
            self.addItem(t)

        ys = [np.nanmin(closes), np.nanmax(closes)]
        if fc is not None:
            ys += [float(np.min(fc.p10)), float(np.max(fc.p90))]
        if plan:
            ys += [v for v in (plan.get("stop"), plan.get("t2")) if v]
        lo, hi = min(ys), max(ys)
        pad = (hi - lo) * 0.08
        x1 = fut[-1] if fut is not None else ts[-1]
        self.setXRange(ts[max(0, n - 260)], x1 + (x1 - ts[0]) * 0.02, padding=0)
        self.setYRange(lo - pad, hi + pad, padding=0)

        # Draw-in animation: history line sweeps left->right, then the forecast fades in.
        self._anim = QVariantAnimation(self, duration=1100, easingCurve=QEasingCurve.OutCubic, startValue=0.0, endValue=1.0)

        def step(v):
            v = float(v)
            k = max(2, int(n * min(1.0, v / 0.75)))
            line.setData(ts[:k], closes[:k])
            if v > 0.7:
                o = (v - 0.7) / 0.3
                for it in items_fc:
                    it.setOpacity(o)
        self._anim.valueChanged.connect(step)
        self._anim.start()
        self.cross.attach()


class EquityChart(pg.PlotWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent, axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
        self.setBackground(_qc(P.card))
        _style(self.getPlotItem())
        self.getPlotItem().setMenuEnabled(False)
        self._anim = None
        self._x = self._y = None
        self._start = 0.0
        self.cross = Crosshair(self, self._lookup)

    def leaveEvent(self, e):
        self.cross.hide()
        super().leaveEvent(e)

    def _lookup(self, x):
        if self._x is None or not len(self._x):
            return None
        i = int(np.abs(self._x - x).argmin())
        d = dt.datetime.fromtimestamp(self._x[i])
        v = self._y[i]
        r = (v / self._start - 1) * 100 if self._start else 0.0
        col = P.green if r >= 0 else P.red
        return self._x[i], float(v), _tip(f"{d:%a %d %b %Y %H:%M}",
                                         [f"<b style='color:{P.primary}'>Equity  A${v:,.2f}</b>",
                                          f"<span style='color:{col}'>{r:+.2f}% since start</span>"])

    def show_curve(self, curve: list, start_cash: float):
        self.clear()
        if not curve:
            return
        x = np.array([c[0] for c in curve], float)
        y = np.array([c[1] for c in curve], float)
        if len(x) == 1:
            x = np.array([x[0] - 3600, x[0]])
            y = np.array([y[0], y[0]])
        up = y[-1] >= start_cash
        col = P.green if up else P.red
        self.addItem(pg.InfiniteLine(pos=start_cash, angle=0, pen=pg.mkPen(_qc(P.outline), width=1, style=Qt.DashLine),
                                     label=f"Start A${start_cash:,.0f}",
                                     labelOpts={"position": 0.02, "anchors": [(0, 1), (0, 1)], "color": _qc(P.faint), "fill": _qc(P.card, 200)}))
        lo, hi = min(y.min(), start_cash), max(y.max(), start_cash)
        pad = max((hi - lo) * 0.15, start_cash * 0.004)
        line = self.plot(x[:2], y[:2], pen=pg.mkPen(_qc(col), width=2.2), fillLevel=lo - pad, brush=pg.mkBrush(_qc(col, 30)))
        self.setYRange(lo - pad, hi + pad, padding=0)
        self.setXRange(x[0], x[-1] + (x[-1] - x[0]) * 0.02 + 1, padding=0)
        self._x, self._y, self._start = x, y, start_cash
        self._anim = QVariantAnimation(self, duration=900, easingCurve=QEasingCurve.OutCubic, startValue=2, endValue=len(x))
        self._anim.valueChanged.connect(lambda k: line.setData(x[:int(k)], y[:int(k)]))
        self._anim.start()
        self.cross.attach()
