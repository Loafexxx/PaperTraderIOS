"""Animated widgets: buttons, page transitions, counters, toasts, odds bars, price tracks."""
from __future__ import annotations

from PySide6.QtCore import (Property, QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, QRectF,
                            QSequentialAnimationGroup, Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QStackedWidget, QVBoxLayout, QWidget)

from .theme import P, mix

ICON_FONT = "Iosevka Nerd Font"
EASE = QEasingCurve.OutCubic


def icon_font(px: int = 18) -> QFont:
    f = QFont(ICON_FONT)
    f.setPixelSize(px)
    return f


def mono(px: int = 14, weight=QFont.Medium) -> QFont:
    f = QFont(P.mono)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def label(text="", role=None, px=None, color=None, bold=False, wrap=False) -> QLabel:
    l = QLabel(text)
    if role:
        l.setProperty("role", role)
    css = []
    if px:
        css.append(f"font-size:{px}px")
    if color:
        css.append(f"color:{color}")
    if bold:
        css.append("font-weight:700")
    if css:
        l.setStyleSheet(";".join(css))
    l.setWordWrap(wrap)
    return l


class AnimButton(QPushButton):
    """Rounded button with smooth hover/press colour transitions."""

    def __init__(self, text="", bg=None, fg=None, hover=None, icon: str = "", px=14, pad=(16, 9), radius=None,
                 outline=False, parent=None):
        super().__init__(text, parent)
        self._bg = QColor(bg or P.card_hi)
        self._fg = QColor(fg or P.fg)
        self._hover_c = QColor(hover or mix(bg or P.card_hi, "#ffffff", 0.12))
        self._icon = icon
        self._t = 0.0
        self._press = 0.0
        self._outline = outline
        self._radius = P.radius if radius is None else radius
        self._px = px
        self.setCursor(Qt.PointingHandCursor)
        f = self.font()
        f.setPixelSize(px)
        f.setWeight(QFont.DemiBold)
        self.setFont(f)
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(text) + pad[0] * 2 + (px + 8 if icon else 0)
        self.setMinimumSize(max(w, 40), fm.height() + pad[1] * 2)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._anim = QVariantAnimation(self, duration=180, easingCurve=EASE)
        self._anim.valueChanged.connect(self._set_t)
        self._panim = QVariantAnimation(self, duration=120, easingCurve=EASE)
        self._panim.valueChanged.connect(self._set_p)

    def set_colors(self, bg, fg, hover):
        self._bg, self._fg, self._hover_c = QColor(bg), QColor(fg), QColor(hover)
        self.update()

    def _set_t(self, v):
        self._t = float(v)
        self.update()

    def _set_p(self, v):
        self._press = float(v)
        self.update()

    def _go(self, anim, cur, to):
        anim.stop()
        anim.setStartValue(cur)
        anim.setEndValue(to)
        anim.start()

    def enterEvent(self, e):
        self._go(self._anim, self._t, 1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._go(self._anim, self._t, 0.0)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._go(self._panim, self._press, 1.0)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._go(self._panim, self._press, 0.0)
        super().mouseReleaseEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        s = 1 - 0.035 * self._press
        r = QRectF(r.center().x() - r.width() * s / 2, r.center().y() - r.height() * s / 2, r.width() * s, r.height() * s)
        a, b = self._bg, self._hover_c
        t = self._t
        c = QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                   int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))
        fg = QColor(self._fg)
        if not self.isEnabled():
            c.setAlpha(90)
            fg.setAlpha(120)
        path = QPainterPath()
        path.addRoundedRect(r, self._radius, self._radius)
        if self._outline:
            p.setPen(QPen(fg if t > 0.5 else QColor(P.outline), 1.2))
            p.setBrush(c)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(c)
        p.drawPath(path)
        p.setPen(fg)
        text = self.text()
        if self._icon:
            fm = self.fontMetrics()
            total = self._px + 8 + fm.horizontalAdvance(text)
            x0 = r.center().x() - total / 2
            p.setFont(icon_font(self._px + 2))
            p.drawText(QRectF(x0, r.top(), self._px + 4, r.height()), Qt.AlignVCenter | Qt.AlignLeft, self._icon)
            p.setFont(self.font())
            p.drawText(QRectF(x0 + self._px + 8, r.top(), total, r.height()), Qt.AlignVCenter | Qt.AlignLeft, text)
        else:
            p.setFont(self.font())
            p.drawText(r, Qt.AlignCenter, text)


def primary_button(text, icon=""):
    return AnimButton(text, P.primary, P.on_primary, mix(P.primary, "#ffffff", 0.18), icon=icon)


def ghost_button(text, icon="", px=13):
    return AnimButton(text, P.card_hi, P.fg, mix(P.card_hi, P.fg, 0.12), icon=icon, px=px)


def confirm_button():
    return AnimButton("Confirm", P.green, P.on_green, mix(P.green, "#ffffff", 0.2), icon="\U000F012C")


def view_button():
    return AnimButton("View", P.red, P.on_red, mix(P.red, "#ffffff", 0.2), icon="\U000F0208")


class FadeStack(QStackedWidget):
    """QStackedWidget with a fade + slide-up transition."""

    def go(self, idx: int):
        if idx == self.currentIndex():
            return
        w = self.widget(idx)
        self.setCurrentIndex(idx)
        eff = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(eff)
        fade = QPropertyAnimation(eff, b"opacity", w, duration=320, easingCurve=EASE)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        end = w.pos()
        slide = QPropertyAnimation(w, b"pos", w, duration=380, easingCurve=EASE)
        slide.setStartValue(end + QPoint(0, 26))
        slide.setEndValue(end)
        g = QParallelAnimationGroup(w)
        g.addAnimation(fade)
        g.addAnimation(slide)
        g.finished.connect(lambda: w.setGraphicsEffect(None))
        g.start(QParallelAnimationGroup.DeleteWhenStopped)


def entrance(w: QWidget, delay_ms: int = 0, dy: int = 18, duration: int = 420):
    """Fade + rise animation for a freshly added widget (used for staggered card lists)."""
    eff = QGraphicsOpacityEffect(w)
    eff.setOpacity(0.0)
    w.setGraphicsEffect(eff)

    def start():
        if not w.isVisible():
            w.setGraphicsEffect(None)
            return
        fade = QPropertyAnimation(eff, b"opacity", w, duration=duration, easingCurve=EASE)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        m = w.contentsMargins()
        rise = QVariantAnimation(w, duration=duration, easingCurve=EASE, startValue=dy, endValue=0)
        rise.valueChanged.connect(lambda v: w.setContentsMargins(m.left(), m.top() + int(v), m.right(), max(0, m.bottom() - int(v))))
        g = QParallelAnimationGroup(w)
        g.addAnimation(fade)
        g.addAnimation(rise)
        g.finished.connect(lambda: w.setGraphicsEffect(None))
        g.start(QParallelAnimationGroup.DeleteWhenStopped)
    QTimer.singleShot(delay_ms, start)


class AnimNumber(QLabel):
    """Label that counts smoothly to new values."""

    def __init__(self, fmt=lambda v: f"{v:,.2f}", px=24, color=None, parent=None):
        super().__init__(parent)
        self._v = 0.0
        self._fmt = fmt
        self.setFont(mono(px, QFont.Bold))
        if color:
            self.setStyleSheet(f"color:{color}")
        self._anim = QVariantAnimation(self, duration=650, easingCurve=EASE)
        self._anim.valueChanged.connect(self._show)
        self.setText(fmt(0.0))

    def _show(self, v):
        self._v = float(v)
        self.setText(self._fmt(self._v))

    def set_value(self, v: float, color: str | None = None):
        if color:
            self.setStyleSheet(f"color:{color}")
        if abs(v - self._v) < 1e-9:
            self.setText(self._fmt(v))
            return
        self._anim.stop()
        self._anim.setStartValue(self._v)
        self._anim.setEndValue(float(v))
        self._anim.start()


def card(parent=None, pad=16, spacing=10, horizontal=False) -> tuple[QFrame, QVBoxLayout | QHBoxLayout]:
    f = QFrame(parent)
    f.setObjectName("card")
    lay = QHBoxLayout(f) if horizontal else QVBoxLayout(f)
    lay.setContentsMargins(pad, pad, pad, pad)
    lay.setSpacing(spacing)
    return f, lay


class Chip(QLabel):
    def __init__(self, text, bg=None, fg=None, px=12, parent=None):
        super().__init__(text, parent)
        self.set(text, bg, fg, px)

    def set(self, text, bg=None, fg=None, px=12):
        self.setText(text)
        self.setStyleSheet(f"background:{bg or P.card_hi};color:{fg or P.muted};border-radius:9px;padding:3px 9px;"
                           f"font-size:{px}px;font-weight:600;")


class StatCard(QFrame):
    def __init__(self, title, fmt, sub="", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(4)
        self.title = label(title.upper(), px=11, color=P.faint, bold=True)
        self.num = AnimNumber(fmt, px=22)
        self.sub = label(sub, px=12, color=P.faint)
        lay.addWidget(self.title)
        lay.addWidget(self.num)
        lay.addWidget(self.sub)


class OddsBar(QWidget):
    """Stacked bar: stop (red) / time-exit (grey) / T1 (green) / T2 (primary). Grows in when shown."""

    def __init__(self, odds: dict, parent=None):
        super().__init__(parent)
        self.odds = odds
        self._t = 0.0
        self.setFixedHeight(10)
        self.setMinimumWidth(160)
        self._a = QVariantAnimation(self, duration=800, easingCurve=EASE, startValue=0.0, endValue=1.0)
        self._a.valueChanged.connect(self._set)
        QTimer.singleShot(150, self._a.start)
        o = odds
        self.setToolTip(f"Model odds over ~30 trading days\nStop first: {o.get('stop', 0) * 100:.0f}%\n"
                        f"Target 1 then breakeven: {o.get('t1', 0) * 100:.0f}%\nTarget 2: {o.get('t2', 0) * 100:.0f}%\n"
                        f"Neither (time exit): {o.get('none', 0) * 100:.0f}%")

    def _set(self, v):
        self._t = float(v)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 5, 5)
        p.setClipPath(path)
        p.fillRect(r, QColor(P.card_hi))
        x = 0.0
        w = r.width() * self._t
        for key, col in (("stop", P.red), ("none", P.faint), ("t1", P.green), ("t2", P.primary)):
            seg = w * max(0.0, self.odds.get(key, 0) or 0)
            p.fillRect(QRectF(x, 0, seg, r.height()), QColor(col))
            x += seg


class PriceTrack(QWidget):
    """Horizontal track from stop to T2 with entry/T1 ticks and an animated current-price dot."""

    def __init__(self, stop, entry, t1, t2, price, parent=None):
        super().__init__(parent)
        self.stop, self.entry, self.t1, self.t2 = stop, entry, t1, t2
        self._price = entry
        self.setFixedHeight(34)
        self.setMinimumWidth(220)
        self._a = QVariantAnimation(self, duration=900, easingCurve=EASE)
        self._a.valueChanged.connect(self._set)
        self.set_price(price)

    def _set(self, v):
        self._price = float(v)
        self.update()

    def set_price(self, price):
        self._a.stop()
        self._a.setStartValue(self._price)
        self._a.setEndValue(float(price))
        self._a.start()

    def _x(self, v, r):
        lo, hi = self.stop - (self.t2 - self.stop) * 0.04, self.t2 + (self.t2 - self.stop) * 0.04
        return r.left() + (min(max(v, lo), hi) - lo) / (hi - lo) * r.width()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(8, 0, -8, 0)
        y = 12
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(P.card_hi))
        p.drawRoundedRect(QRectF(r.left(), y - 3, r.width(), 6), 3, 3)
        xe = self._x(self.entry, r)
        xs = self._x(self.stop, r)
        x1, x2 = self._x(self.t1, r), self._x(self.t2, r)
        p.setBrush(QColor(mix(P.red, P.card_hi, 0.45)))
        p.drawRoundedRect(QRectF(xs, y - 3, xe - xs, 6), 3, 3)
        p.setBrush(QColor(mix(P.green, P.card_hi, 0.45)))
        p.drawRoundedRect(QRectF(xe, y - 3, x2 - xe, 6), 3, 3)
        f = QFont(P.mono)
        f.setPixelSize(10)
        p.setFont(f)
        for x, txt, col in ((xs, f"{self.stop:.2f}", P.red), (xe, f"{self.entry:.2f}", P.muted),
                            (x1, f"{self.t1:.2f}", P.green), (x2, f"{self.t2:.2f}", P.primary)):
            p.setPen(QPen(QColor(col), 2))
            p.drawLine(int(x), y - 6, int(x), y + 6)
            p.drawText(QRectF(x - 30, y + 7, 60, 14), Qt.AlignCenter, txt)
        xc = self._x(self._price, r)
        col = QColor(P.green if self._price >= self.entry else P.red)
        glow = QColor(col)
        glow.setAlpha(70)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QRectF(xc - 9, y - 9, 18, 18))
        p.setBrush(col)
        p.drawEllipse(QRectF(xc - 5, y - 5, 10, 10))


class PulseDot(QWidget):
    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self._t = 0.0
        self.setFixedSize(16, 16)
        self._a = QVariantAnimation(self, duration=1600, startValue=0.0, endValue=1.0, loopCount=-1)
        self._a.valueChanged.connect(lambda v: (setattr(self, "_t", float(v)), self.update()))

    def set_live(self, live: bool, color):
        self.color = QColor(color)
        (self._a.start() if live else self._a.stop())
        self._t = 0.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self.color)
        if self._t:
            g = QColor(c)
            g.setAlpha(int(150 * (1 - self._t)))
            rr = 3 + 5 * self._t
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(QRectF(8 - rr, 8 - rr, rr * 2, rr * 2))
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawEllipse(QRectF(4.5, 4.5, 7, 7))


class Spinner(QWidget):
    def __init__(self, size=22, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._a = QVariantAnimation(self, duration=900, startValue=0, endValue=360, loopCount=-1)
        self._ang = 0
        self._a.valueChanged.connect(lambda v: (setattr(self, "_ang", int(v)), self.update()))
        self.hide()

    def start(self):
        self.show()
        self._a.start()

    def stop(self):
        self._a.stop()
        self.hide()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(P.primary), 2.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        p.drawArc(r, -self._ang * 16, 270 * 16)


class Toast(QFrame):
    closed = Signal(object)

    def __init__(self, parent, text, kind="info"):
        super().__init__(parent)
        col = {"fill": P.primary, "win": P.green, "loss": P.red, "order": P.tertiary, "error": P.red}.get(kind, P.muted)
        ico = {"fill": "\U000F012C", "win": "\U000F0535", "loss": "\U000F0533", "order": "\U000F0415",
               "error": "\U000F0026"}.get(kind, "\U000F02FC")
        self.setStyleSheet(f"QFrame{{background:{P.card_hi};border:1px solid {mix(col, P.bg, 0.4)};"
                           f"border-radius:{P.radius + 2}px;}} QLabel{{border:none;background:transparent;}}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 16, 10)
        i = QLabel(ico)
        i.setFont(icon_font(18))
        i.setStyleSheet(f"color:{col}")
        t = label(text, px=13, wrap=True)
        t.setMaximumWidth(360)
        t.setMinimumWidth(min(360, t.fontMetrics().horizontalAdvance(text) + 6))
        lay.addWidget(i)
        lay.addWidget(t, 1)
        self.adjustSize()
        self.eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.eff)

    def show_at(self, pos: QPoint, life_ms=4800):
        self.move(pos + QPoint(0, 30))
        self.show()
        self.raise_()
        a1 = QPropertyAnimation(self, b"pos", self, duration=380, easingCurve=EASE, startValue=pos + QPoint(0, 30), endValue=pos)
        a2 = QPropertyAnimation(self.eff, b"opacity", self, duration=380, easingCurve=EASE, startValue=0.0, endValue=1.0)
        g = QParallelAnimationGroup(self)
        g.addAnimation(a1)
        g.addAnimation(a2)
        g.start()
        QTimer.singleShot(life_ms, self.dismiss)

    def dismiss(self):
        a = QPropertyAnimation(self.eff, b"opacity", self, duration=300, easingCurve=EASE, startValue=1.0, endValue=0.0)
        a.finished.connect(lambda: (self.closed.emit(self), self.deleteLater()))
        a.start()

    def mousePressEvent(self, e):
        self.dismiss()


class SlidingIndicator(QWidget):
    """Pill that slides behind the active sidebar item."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._anim = QPropertyAnimation(self, b"geometry", self, duration=340, easingCurve=EASE)

    def move_to(self, rect, animate=True):
        if not animate:
            self.setGeometry(rect)
            return
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(rect)
        self._anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(P.primary_container))
        p.drawRoundedRect(QRectF(self.rect()), P.radius + 2, P.radius + 2)
        p.setBrush(QColor(P.primary))
        p.drawRoundedRect(QRectF(0, self.height() * 0.25, 3.5, self.height() * 0.5), 2, 2)


class UsageBar(QWidget):
    """Thin animated bar with label, percentage and reset time (for Claude plan limits)."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title, self.pct, self.resets = title, 0.0, ""
        self._shown = 0.0
        self.setFixedHeight(38)
        self._a = QVariantAnimation(self, duration=900, easingCurve=EASE)
        self._a.valueChanged.connect(lambda v: (setattr(self, "_shown", float(v)), self.update()))

    def set_value(self, pct: float, resets: str):
        self.pct, self.resets = pct, resets
        self.setToolTip(f"{self.title}: {pct:.0f}% used" + (f"\nResets {resets}" if resets else ""))
        self._a.stop()
        self._a.setStartValue(self._shown)
        self._a.setEndValue(float(pct))
        self._a.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        col = QColor(P.green if self.pct < 60 else P.primary if self.pct < 85 else P.red)
        f = QFont(P.font)
        f.setPixelSize(11)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(P.muted))
        p.drawText(QRectF(0, 0, w, 14), Qt.AlignLeft | Qt.AlignVCenter, self.title)
        fm = QFont(P.mono)
        fm.setPixelSize(11)
        fm.setWeight(QFont.Bold)
        p.setFont(fm)
        p.setPen(col)
        p.drawText(QRectF(0, 0, w, 14), Qt.AlignRight | Qt.AlignVCenter, f"{self._shown:.0f}%")
        r = QRectF(0, 18, w, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(P.card_hi))
        p.drawRoundedRect(r, 3, 3)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(0, 18, max(6.0, w * min(self._shown, 100) / 100), 6), 3, 3)
        f.setPixelSize(10)
        f.setWeight(QFont.Normal)
        p.setFont(f)
        p.setPen(QColor(P.faint))
        p.drawText(QRectF(0, 26, w, 12), Qt.AlignLeft | Qt.AlignVCenter, f"resets {self.resets}" if self.resets else "")
