# Paper Trader

A$10,000 paper-trading desk for Arch + Hyprland, themed from your Serpantinum/Matugen colours
(`~/.config/kitty/colors.conf`, re-read at every launch).

- **Run:** `papertrader`, or "Paper Trader" in your launcher. Window class: `papertrader`.
- **Data:** `~/.local/share/papertrader/ledger.json` (positions, orders, history, settings).
- **Reinstall after edits:** `./install.sh`

## Pages
- **Dashboard:** equity, cash, open/realised P&L, open risk, equity curve, market posture (runs your
  market-breadth, uptrend and sector skills), activity feed.
- **Scanner:** *Scan market* (built-in, keyless) or *Ask Claude to scan* (your trading skills via `claude -p`).
  Each idea has a green **Confirm** (places a paper limit order with stop/T1/T2) and a red **View** (chart of
  the past year plus a 30-day model range, plan levels, and an optional Claude bear/base/bull estimate).
- **Trades:** live positions with a stop→target track, pending orders, sell or cancel buttons.
- **History:** closed trades with P&L, R multiple, MAE/MFE, CSV export.
- **Settings:** risk rules, refresh interval, Claude CLI path, account reset.

## How fills are simulated
Prices come from Yahoo every refresh (15-minute bars). A buy limit fills when price trades at or below it.
The stop wins if stop and target are hit in the same bar. Half the position sells at T1, then the stop moves
to breakeven and the rest sells at T2. US-dollar prices are converted at the live AUD/USD rate.

## Claude
Install Claude Code once and sign in; the app finds `claude` automatically:

    curl -fsSL https://claude.ai/install.sh | bash
    claude

Claude only suggests ideas and estimates. Orders are placed only when you press Confirm.

Keys: Ctrl+1…5 pages · Ctrl+R refresh · Ctrl+K scan.
