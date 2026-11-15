"""Talk to Claude through the Claude Code CLI in headless mode (`claude -p`).

Claude runs with your installed trading skills (market-breadth-analyzer, uptrend-analyzer,
sector-analyst, finviz-screener, us-stock-analysis, ...) and web search, and replies in JSON.
Claude never places orders from here: it only returns ideas and estimates; you press Confirm.
Runs can be cancelled: the CLI process (and anything it started) is killed immediately.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading

APP_DIR = os.path.expanduser("~/.local/share/papertrader")
CANDIDATES = ["~/.local/bin/claude", "~/.claude/local/claude", "/usr/local/bin/claude", "/usr/bin/claude",
              "~/.npm-global/bin/claude", "~/.bun/bin/claude"]
ALLOWED = ["Skill", "Read", "Glob", "Grep", "WebSearch", "WebFetch", "Bash(python3:*)", "Bash(ls:*)", "Bash(cat:*)"]


class Cancelled(Exception):
    pass


class RunHandle:
    """Shared between the GUI thread (cancel) and the worker thread (run)."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.cancelled = False
        self._lock = threading.Lock()

    def cancel(self):
        with self._lock:
            self.cancelled = True
            p = self.proc
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            threading.Timer(2.0, self._force_kill, args=(p,)).start()

    @staticmethod
    def _force_kill(p):
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def find_claude(custom: str = "") -> str | None:
    if custom and os.path.exists(os.path.expanduser(custom)):
        return os.path.expanduser(custom)
    w = shutil.which("claude")
    if w:
        return w
    for c in CANDIDATES:
        p = os.path.expanduser(c)
        if os.path.exists(p):
            return p
    return None


def extract_json(text: str):
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        return json.loads(m.group(1))
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("Claude's reply contained no JSON")


def ask(prompt: str, custom_path: str = "", timeout: int = 900, max_turns: int = 40,
        handle: RunHandle | None = None) -> dict:
    exe = find_claude(custom_path)
    if not exe:
        raise FileNotFoundError("Claude Code CLI not found. Install it and run `claude` once to sign in.")
    handle = handle or RunHandle()
    if handle.cancelled:
        raise Cancelled()
    os.makedirs(APP_DIR, exist_ok=True)
    cmd = [exe, "-p", prompt, "--output-format", "json", "--max-turns", str(max_turns),
           "--allowedTools", *ALLOWED]
    with handle._lock:
        if handle.cancelled:
            raise Cancelled()
        # Own process group so cancel can kill Claude and every tool process it spawned.
        handle.proc = subprocess.Popen(cmd, cwd=APP_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, start_new_session=True)
    try:
        out, err = handle.proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        handle.cancel()
        raise TimeoutError(f"Claude took longer than {timeout // 60} minutes and was stopped.")
    if handle.cancelled:
        raise Cancelled()
    if handle.proc.returncode != 0 and not out.strip():
        raise RuntimeError((err or "claude exited with an error").strip()[-800:])
    try:
        env = json.loads(out)
        text = env.get("result", "") if isinstance(env, dict) else out
        if isinstance(env, dict) and env.get("is_error"):
            raise RuntimeError(text or "Claude reported an error")
    except json.JSONDecodeError:
        text = out
    return extract_json(text)


_USAGE_RE = re.compile(r"^\s*(Current session|Current week(?: \(([^)]+)\))?)\s*:\s*(\d+(?:\.\d+)?)%\s*used"
                       r"(?:\s*[·•-]\s*resets\s*(.+?))?\s*$", re.M | re.I)


def usage(custom_path: str = "", timeout: int = 60) -> list[dict]:
    """Plan usage via Claude Code's built-in `/usage` command (runs locally, no model turn, no cost).
    Returns [{"key": "session"|"week"|"week:<model>", "label", "pct", "resets"}]."""
    exe = find_claude(custom_path)
    if not exe:
        raise FileNotFoundError("Claude Code CLI not found")
    os.makedirs(APP_DIR, exist_ok=True)
    r = subprocess.run([exe, "-p", "/usage", "--output-format", "json"], cwd=APP_DIR, capture_output=True,
                       text=True, timeout=timeout)
    try:
        text = json.loads(r.stdout).get("result", "")
    except json.JSONDecodeError:
        text = r.stdout
    out = []
    for m in _USAGE_RE.finditer(text):
        name, model, pct, resets = m.group(1), m.group(2), float(m.group(3)), (m.group(4) or "").strip()
        resets = re.sub(r"\s*\([^)]*\)\s*$", "", resets)  # drop the "(Australia/Sydney)" suffix
        if name.lower().startswith("current session"):
            out.append({"key": "session", "label": "5-hour session", "pct": pct, "resets": resets})
        elif not model or model.lower().startswith("all"):
            out.append({"key": "week", "label": "Weekly (all models)", "pct": pct, "resets": resets})
        else:
            out.append({"key": f"week:{model}", "label": f"Weekly ({model})", "pct": pct, "resets": resets})
    if not out:
        raise RuntimeError((text or r.stderr or "no usage info returned").strip()[:200])
    return out


_DATA_HOWTO = """Price data (keyless): run python3 to GET \
https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>?range=1y&interval=1d with header \
"User-Agent: Mozilla/5.0" (other user agents get HTTP 429) and compute from the daily bars: last close and its date, \
SMA20/50/200, ATR(14), 10- and 20-day lows, 20-day and 52-week highs. Do this for every stock you report on."""

SCAN_PROMPT = """You are the market scanner and adviser for my paper-trading app. Account: A${equity:,.0f} paper money \
(AUD), A${cash:,.0f} available cash, currently holding: {holdings}. My BUDGET for this scan is A${budget:,.0f}: the most \
I want to put to work now. Rules: risk {risk_pct}% of the account per trade, max {max_pos}% of the account per \
position, max {max_risk}% total open risk, AUD/USD {fx:.4f}, no API keys available. Today is {today}.
Quality matters far more than quantity: return FEWER picks (even zero) rather than weak or unverified ones.

Work through these steps:
1. Market posture: run my keyless skills market-breadth-analyzer, uptrend-analyzer and sector-analyst. If the posture \
is cautious or defensive, prefer fewer, lower-beta names in leading sectors and say so in the summary.
2. Candidates: use finviz-screener (public screener: USA, price > $5, avg volume > 1M, above SMA50 and SMA200, \
positive EPS growth this year and Q/Q, forward P/E < 25) and favour the strongest sectors and recent news themes. \
Shortlist about 15, at least 4 sectors.
3. Verify each candidate with real numbers. {data_howto}
   Reject: price more than 12% above its SMA50 (over-extended), RSI above 72, below SMA200, a broken recent chart.
4. Fundamentals via the us-stock-analysis approach: forward P/E, EPS growth this year and next, margins, \
debt/equity. Reject weak or deteriorating fundamentals.
5. Earnings date: confirm from TWO sources (FinViz quote page plus a web search of the company's IR or a major \
outlet). Exclude anything reporting within the next 14 days, and anything whose date you cannot confirm.
6. News: web-search the last 10 days for each finalist. Exclude names with an unresolved major negative catalyst \
(investigation, guidance cut, CEO exit, pending deal, offering).
7. Plan, using the computed data only: entry = a limit at or slightly below the last close (pullback toward SMA20 \
or support), never more than 3% from the last close; stop just below a real support level AND at least 1.5 x ATR \
below entry; target1 at the next real resistance with reward:risk >= 1.5; target2 at a further resistance or \
about 3R. Holding period in trading days.
8. Probabilities must be calibrated to base rates: with a stop of about 1.5-2 ATR and target1 about 1.5-2R, swing \
trades typically hit the stop first 40-55% of the time. Keep p_base + p_bull <= 0.60 unless the evidence is \
unusually strong, and p_bear + p_base + p_bull <= 0.95 (the remainder is a flat time exit).
9. Self-check before replying: recompute every number from the data you fetched; stop < entry < t1 < t2; entry \
within 3% of the last close; price date within the last 3 trading days; at most 2 picks per sector; none of these \
excluded tickers: {exclude}. Drop any pick that fails.

10. Advice for my budget. Decide honestly what I should do with the A${budget:,.0f} right now:
   - "dont_invest": conditions are poor or nothing is good enough; keep it all in cash.
   - "wait_for_pullback": good names exist but entries are stretched; list them with entry levels but invest little or nothing now.
   - "invest_partial": invest part of the budget (say how much) in the best few and keep the rest in cash.
   - "invest": conditions support it; split the budget across the top picks (at most 5).
   Allocations must use only symbols from your picks, sum to no more than the budget or my available cash, keep each \
position within the per-position limit, and keep each trade's loss at its stop within the risk-per-trade limit. Explain \
why for each allocation in one plain-English sentence, and say what would change your advice. Write the headline the \
way you'd say it to a beginner, e.g. "Don't invest for now" or "Here are the top 3 stocks to buy, and why".

Return up to {n} picks. Reply with ONLY a JSON object, no prose, exactly in this shape:
{{"posture": "short label", "ceiling_pct": 0-100, "summary": "2-3 sentences on market conditions and news",
 "advice": {{"action": "dont_invest|wait_for_pullback|invest_partial|invest", "headline": "",
  "reasoning": "3-5 plain-English sentences", "invest_now_aud": 0, "keep_cash_aud": 0,
  "allocations": [{{"symbol": "", "aud": 0, "why": ""}}], "what_would_change": "", "confidence": "low|medium|high"}},
 "rejected": ["TICKER: reason", "..."],
 "picks": [{{"symbol": "", "name": "", "sector": "", "price": 0, "price_as_of": "YYYY-MM-DD", "atr": 0,
  "entry": 0, "stop": 0, "t1": 0, "t2": 0, "support": 0, "resistance": 0, "horizon_days": 0,
  "p_bear": 0, "p_base": 0, "p_bull": 0, "confidence": "low|medium|high",
  "earnings": "YYYY-MM-DD", "earnings_sources": ["...", "..."],
  "thesis": "one or two sentences", "evidence": ["3 short factual bullets with numbers"],
  "kill": "what news would kill the thesis", "sources": ["urls or skill names"]}}]}}
All prices in USD. Probabilities are estimates; be honest and conservative."""

ESTIMATE_PROMPT = """Estimate the near-term outlook for {symbol} ({name}) for my paper-trading app. Today is {today}. \
The app's last price is about US${price:.2f}. My draft plan: entry {entry:.2f}, stop {stop:.2f}, target1 {t1:.2f}, \
target2 {t2:.2f}.
1. {data_howto}
2. Fundamentals, valuation and business quality using my keyless us-stock-analysis skill approach.
3. News from the last 10 days and the next earnings date, confirmed from two sources (FinViz plus a web search).
4. Bear/base/bull prices at your chosen horizon must be consistent with the stock's volatility: over N trading \
days a typical move is about ATR x sqrt(N). Calibrate probabilities to base rates and make them sum to 1.
5. Self-check every number against the data you fetched before replying; say which numbers you could not verify.
Reply with ONLY a JSON object, no prose:
{{"symbol": "{symbol}", "verdict": "buy-on-pullback|wait|avoid", "summary": "2-4 sentences",
 "price": 0, "price_as_of": "YYYY-MM-DD", "atr": 0, "horizon_days": 0,
 "bear": {{"price": 0, "prob": 0}}, "base": {{"price": 0, "prob": 0}}, "bull": {{"price": 0, "prob": 0}},
 "support": [0], "resistance": [0], "earnings": "YYYY-MM-DD or null", "confidence": "low|medium|high",
 "evidence": ["short factual bullets with numbers"], "key_risks": ["..."],
 "plan_changes": "suggested tweaks to entry/stop/targets, or 'none'", "unverified": ["..."]}}"""
