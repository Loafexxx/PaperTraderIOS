"""Indicators and a Monte-Carlo price model used for the 'predicted' part of charts.

The model is deliberately simple and conservative: geometric Brownian motion using
recent realised volatility and a heavily shrunk drift. It produces a range of
plausible outcomes, not a prediction you should rely on.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def sma(x: np.ndarray, n: int) -> float:
    return float(np.mean(x[-n:])) if len(x) >= n else float("nan")


def sma_series(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def atr(h, l, c, n: int = 14) -> float:
    if len(c) < n + 1:
        return float("nan")
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    return float(np.mean(tr[-n:]))


def rsi(c: np.ndarray, n: int = 14) -> float:
    if len(c) < n + 1:
        return float("nan")
    d = np.diff(c[-(n * 5):])
    up, dn = np.where(d > 0, d, 0), np.where(d < 0, -d, 0)
    a_up, a_dn = up[:n].mean(), dn[:n].mean()
    for u, w in zip(up[n:], dn[n:]):  # Wilder smoothing
        a_up = (a_up * (n - 1) + u) / n
        a_dn = (a_dn * (n - 1) + w) / n
    return 100.0 if a_dn == 0 else float(100 - 100 / (1 + a_up / a_dn))


@dataclass
class Forecast:
    days: np.ndarray            # 0..horizon (trading days ahead)
    p10: np.ndarray
    p25: np.ndarray
    p50: np.ndarray
    p75: np.ndarray
    p90: np.ndarray
    vol_annual: float
    drift_annual: float
    p_stop: float = float("nan")
    p_t1: float = float("nan")
    p_t2: float = float("nan")
    p_none: float = float("nan")
    exp_return_pct: float = float("nan")


def simulate(closes: np.ndarray, horizon: int = 30, n: int = 4000, entry=None, stop=None, t1=None, t2=None,
             seed: int = 7) -> Forecast:
    closes = np.asarray(closes, float)
    rets = np.diff(np.log(closes[-127:]))
    sigma = float(np.std(rets, ddof=1)) if len(rets) > 5 else 0.02
    sigma = max(sigma, 0.006)
    mu_raw = float(np.mean(rets)) if len(rets) else 0.0
    # Shrink observed drift hard toward zero (momentum persists weakly at best) and cap it.
    mu = float(np.clip(0.2 * mu_raw, -0.0006, 0.0006))
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n, horizon))
    # Fat-ish tails: mix in occasional larger shocks.
    z *= np.where(rng.random((n, horizon)) < 0.05, 1.8, 1.0)
    steps = (mu - 0.5 * sigma ** 2) + sigma * z
    start = float(closes[-1])
    paths = start * np.exp(np.cumsum(steps, axis=1))
    paths = np.hstack([np.full((n, 1), start), paths])
    q = np.percentile(paths, [10, 25, 50, 75, 90], axis=0)
    fc = Forecast(np.arange(horizon + 1), *q, vol_annual=sigma * np.sqrt(252), drift_annual=mu * 252)

    if entry and stop and t1:
        # Re-run paths starting at the entry price for trade-outcome odds (first touch wins).
        pe = float(entry) * np.exp(np.cumsum(steps, axis=1))
        hit_stop = np.argmax(pe <= stop, axis=1).astype(float)
        hit_stop[~(pe <= stop).any(axis=1)] = np.inf
        hit_t1 = np.argmax(pe >= t1, axis=1).astype(float)
        hit_t1[~(pe >= t1).any(axis=1)] = np.inf
        t2v = t2 or t1
        hit_t2 = np.argmax(pe >= t2v, axis=1).astype(float)
        hit_t2[~(pe >= t2v).any(axis=1)] = np.inf
        stop_first = hit_stop < hit_t1
        t1_first = hit_t1 < hit_stop
        # After T1, half is sold and the stop moves to breakeven: T2 counts if reached before falling back to entry.
        be = np.where(pe <= entry, np.arange(horizon)[None, :], np.inf)
        be_after_t1 = np.array([np.min(be[i, int(hit_t1[i]):]) if np.isfinite(hit_t1[i]) else np.inf for i in range(n)])
        t2_hit = t1_first & (hit_t2 < be_after_t1)
        fc.p_stop = float(stop_first.mean())
        fc.p_t2 = float(t2_hit.mean())
        fc.p_t1 = float((t1_first & ~t2_hit).mean())
        fc.p_none = float(1 - fc.p_stop - fc.p_t1 - fc.p_t2)
        final = pe[:, -1]
        r_stop = (stop - entry) / entry
        r_t1 = (t1 - entry) / entry
        r_t2 = 0.5 * r_t1 + 0.5 * (t2v - entry) / entry
        r_none = (final - entry) / entry
        rr = np.where(stop_first, r_stop, np.where(t2_hit, r_t2, np.where(t1_first, 0.5 * r_t1, r_none)))
        fc.exp_return_pct = float(rr.mean() * 100)
    return fc
