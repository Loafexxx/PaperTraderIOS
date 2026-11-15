"""Market data: keyless sources only (Yahoo chart endpoint, FinViz public quote pages)."""
from __future__ import annotations

import datetime as dt
import re
import threading
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import numpy as np
import requests

NY = ZoneInfo("America/New_York")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"}
_session = requests.Session()
_session.headers.update(UA)
_yahoo = requests.Session()  # Yahoo throttles full browser/curl UAs from scripts; a bare one works.
_yahoo.headers.update({"User-Agent": "Mozilla/5.0"})
_lock = threading.Lock()
_cache: dict[tuple, tuple[float, object]] = {}


def _cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


@dataclass
class Bars:
    symbol: str
    ts: np.ndarray            # unix seconds
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    price: float = float("nan")
    prev_close: float = float("nan")
    name: str = ""
    currency: str = "USD"
    market_time: int = 0

    @property
    def dates(self):
        return [dt.datetime.fromtimestamp(t, NY).date() for t in self.ts]


def history(symbol: str, rng: str = "1y", interval: str = "1d", ttl: int = 300) -> Bars:
    def fetch():
        r = None
        for attempt in range(4):
            host = "query1" if attempt % 2 == 0 else "query2"
            r = _yahoo.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}",
                           params={"range": rng, "interval": interval, "includePrePost": "false"}, timeout=20)
            if r.status_code != 429:
                break
            time.sleep(0.8 * (attempt + 1))
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        ts = np.array(res.get("timestamp") or [], dtype=float)
        arr = {k: np.array([np.nan if x is None else x for x in q.get(k, [])], dtype=float) for k in ("open", "high", "low", "close", "volume")}
        ok = ~np.isnan(arr["close"]) if len(ts) else np.array([], bool)
        meta = res.get("meta", {})
        return Bars(symbol, ts[ok], arr["open"][ok], arr["high"][ok], arr["low"][ok], arr["close"][ok], arr["volume"][ok],
                    price=float(meta.get("regularMarketPrice") or (arr["close"][ok][-1] if ok.any() else np.nan)),
                    prev_close=float(meta.get("chartPreviousClose") or np.nan),
                    name=meta.get("longName") or meta.get("shortName") or symbol,
                    currency=meta.get("currency", "USD"),
                    market_time=int(meta.get("regularMarketTime") or 0))
    return _cached(("hist", symbol, rng, interval), ttl, fetch)


def audusd(ttl: int = 300) -> float:
    """USD per 1 AUD (e.g. 0.65). Prices in AUD = usd / audusd()."""
    try:
        v = float(history("AUDUSD=X", "5d", "1d", ttl).price)
        if not (0.3 < v < 1.5):
            raise ValueError(v)
        with _lock:
            _cache[("fx_last",)] = (time.time(), v)
        return v
    except Exception:
        return _cache.get(("fx_last",), (0, 0.65))[1]


def usd_to_aud(x: float, fx: float) -> float:
    return x / fx if fx else float("nan")


# ---------- FinViz fundamentals (public quote page) ----------
class _TD(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells, self._in, self._buf = [], 0, []

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            self._in += 1
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "td" and self._in:
            self._in -= 1
            self.cells.append(re.sub(r"\s+", " ", "".join(self._buf)).strip())

    def handle_data(self, d):
        if self._in:
            self._buf.append(d)


FV_KEYS = ["P/E", "Forward P/E", "PEG", "EPS this Y", "EPS next Y", "EPS Q/Q", "Sales Q/Q", "ROE", "Debt/Eq",
           "Profit Margin", "RSI (14)", "Beta", "Earnings", "Target Price", "Recom", "Sector", "Industry",
           "Market Cap", "Avg Volume", "Dividend TTM", "Short Float"]


def finviz(symbol: str, ttl: int = 3600) -> dict:
    def fetch():
        r = _session.get("https://finviz.com/quote.ashx", params={"t": symbol, "p": "d"}, timeout=20)
        r.raise_for_status()
        p = _TD()
        p.feed(r.text)
        out = {}
        cells = p.cells
        for i in range(len(cells) - 1):
            k = cells[i]
            if k in FV_KEYS and k not in out:
                out[k] = cells[i + 1]
        return out
    return _cached(("fv", symbol), ttl, fetch)


def pnum(s) -> float:
    """Parse FinViz numbers like '23.4%', '1.2B', '-'."""
    if s is None:
        return float("nan")
    s = str(s).split(" ")[0].replace(",", "")
    mult = 1.0
    if s.endswith("%"):
        s = s[:-1]
    for suf, m in (("B", 1e9), ("M", 1e6), ("K", 1e3), ("T", 1e12)):
        if s.endswith(suf):
            s, mult = s[:-1], m
    try:
        return float(s) * mult
    except ValueError:
        return float("nan")


def next_earnings(fv_value: str, today: dt.date | None = None) -> dt.date | None:
    """FinViz shows 'Oct 13 BMO' without a year: pick the nearest plausible date."""
    if not fv_value or fv_value == "-":
        return None
    today = today or dt.datetime.now(NY).date()
    m = re.match(r"([A-Z][a-z]{2}) (\d{1,2})", fv_value)
    if not m:
        return None
    try:
        d = dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {today.year}", "%b %d %Y").date()
    except ValueError:
        return None
    cands = [d.replace(year=today.year - 1), d, d.replace(year=today.year + 1)]
    return min(cands, key=lambda x: abs((x - today).days))


# ---------- Market clock ----------
US_HOLIDAYS = {  # NYSE full-day closures
    dt.date(2026, 11, 26), dt.date(2026, 12, 25), dt.date(2027, 1, 1), dt.date(2027, 1, 18),
    dt.date(2027, 2, 15), dt.date(2027, 3, 26), dt.date(2027, 5, 31), dt.date(2027, 6, 18),
    dt.date(2027, 7, 5), dt.date(2027, 9, 6), dt.date(2027, 11, 25), dt.date(2027, 12, 24),
}


def market_status(now: dt.datetime | None = None) -> tuple[bool, str]:
    now = (now or dt.datetime.now(NY)).astimezone(NY)
    d = now.date()

    def is_day(x):
        return x.weekday() < 5 and x not in US_HOLIDAYS
    o = now.replace(hour=9, minute=30, second=0, microsecond=0)
    c = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if is_day(d) and o <= now < c:
        mins = int((c - now).total_seconds() // 60)
        return True, f"Market open · closes in {mins // 60}h {mins % 60:02d}m"
    nd = d if (is_day(d) and now < o) else d + dt.timedelta(days=1)
    while not is_day(nd):
        nd += dt.timedelta(days=1)
    nxt = dt.datetime.combine(nd, dt.time(9, 30), NY)
    mins = int((nxt - now).total_seconds() // 60)
    h = mins // 60
    when = f"{h // 24}d {h % 24}h" if h >= 24 else f"{h}h {mins % 60:02d}m"
    return False, f"Market closed · opens in {when}"


# ---------- Scan universe: liquid US names across sectors ----------
UNIVERSE = {
    "Technology": "AAPL MSFT NVDA AVGO ORCL CRM ADBE AMD CSCO ACN IBM INTU TXN QCOM NOW AMAT ADP PAYX CTSH ZM MU ANET",
    "Communication Services": "GOOGL META NFLX TMUS VZ T DIS CMCSA FOXA EA TTWO APP",
    "Healthcare": "UNH LLY JNJ ABBV MRK TMO ABT DHR AMGN ISRG GILD HCA ELV CI GEHC DGX COR CVS",
    "Consumer Defensive": "PG KO PEP COST WMT PM MO MDLZ CL KMB GIS DG DLTR KR TGT",
    "Financial": "JPM BAC WFC GS MS C SCHW BLK SPGI CME ICE TRV CB PGR AIG MET AXP V MA BK",
    "Industrials": "GE CAT HON UNP RTX LMT DE ETN UPS GD NOC CSX ITW WM EMR",
    "Consumer Cyclical": "AMZN TSLA HD MCD NKE LOW SBUX BKNG TJX CMG RCL YUM GM",
    "Energy": "XOM CVX COP EOG SLB PSX MPC OXY KMI WMB",
    "Utilities": "NEE SO DUK AEP D EXC XEL SRE CEG",
    "Real Estate": "PLD AMT EQIX PSA O SPG WELL",
    "Basic Materials": "LIN SHW APD ECL NEM FCX DOW",
}
SECTOR_OF = {s: sec for sec, syms in UNIVERSE.items() for s in syms.split()}
