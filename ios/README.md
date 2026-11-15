# Paper Trader for iPhone

Native SwiftUI version of the desktop app (iOS 17+, tested target: iPhone 15 Pro on iOS 27).
Built by GitHub Actions on a Mac runner into an **unsigned .ipa** that you sideload
(AltStore / SideStore / Sideloadly sign it with your own Apple ID).

## What's on the phone
- A$ paper ledger (starts at A$10,000; reset to any amount in Settings).
- Built-in scanner, budget plan + advice card, Confirm (green) / View (red).
- Charts with touch-and-drag date/price, 30-day model range, plan levels.
- Trades (live P&L, stop→target track, Sell now / Cancel) and History (CSV share).
- Local notifications when scans finish and when orders fill or close.

## What comes from your PC (companion)
Claude scans, Claude estimates, Claude usage, and the full market-posture skills.
On the PC: Paper Trader → Settings → **iPhone companion** → tick "Run the companion server".
On the phone: Settings → **PC companion** → paste an address (e.g. `192.168.1.97:8787`) and the token.
Same Wi-Fi works out of the box; for away-from-home use install Tailscale on both and use the `100.x` address.

Note: iOS pauses apps in the background, so if you leave the app during a long Claude scan the phone
can't poll the PC. The scan keeps running on the PC; reopen the app to see the result.

## Build it on GitHub (no Mac needed)
1. Create an empty repository on github.com (private is fine).
2. From the `papertrader` folder on your PC:

       git init -b main
       git add .
       git commit -m "Paper Trader desktop + iPhone"
       git remote add origin https://github.com/<you>/<repo>.git
       git push -u origin main

3. GitHub → your repo → **Actions** → "Build iPhone app (.ipa)" runs automatically
   (or press **Run workflow**). It takes ~5–10 minutes.
4. Open the finished run → **Artifacts** → download `PaperTrader-ipa` (a zip containing `PaperTrader.ipa`).
5. Sideload `PaperTrader.ipa` with your usual tool.

If the build fails, download the `build-log` artifact (or copy the "Compiler errors" section of the
run log) and send it to Claude to fix.

## Local project generation (on a Mac, optional)

    brew install xcodegen
    cd ios && xcodegen generate && open PaperTrader.xcodeproj
