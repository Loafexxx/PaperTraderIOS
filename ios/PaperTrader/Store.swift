import Foundation
import SwiftUI
import UIKit

/// App state + paper-trading engine (AUD ledger, simulated limit/bracket fills from real 15-minute bars).
@MainActor
final class Store: ObservableObject {
    @Published var d: LedgerData
    @Published var prices: [String: Double] = [:]
    @Published var prevClose: [String: Double] = [:]
    @Published var fx: Double = 0.65
    @Published var lastUpdate: Date?
    @Published var refreshing = false
    @Published var posture: Posture?
    @Published var postureBusy = false
    @Published var usage: [UsageRow] = []
    @Published var companionState = "Not set up"
    @Published var companionOK = false
    @Published var toast: String?

    // Scanner state
    @Published var scanning = false
    @Published var scanKind = ""
    @Published var scanProgress: Double = 0
    @Published var scanStatus = "Tap Scan market to look for ideas."
    @Published var picks: [Pick] = []
    @Published var advice: Advice?
    @Published var claudeSummary: String?
    private var scanTask: Task<Void, Never>?

    private static var fileURL: URL {
        let dir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return dir.appendingPathComponent("ledger.json")
    }

    init() {
        if let data = try? Data(contentsOf: Store.fileURL), let l = try? JSONDecoder().decode(LedgerData.self, from: data) {
            d = l
        } else {
            d = LedgerData()
            d.events.append(LedgerEvent(ts: Date().timeIntervalSince1970,
                                        text: "Welcome! Paper account opened with A$10,000.", kind: "info"))
        }
    }

    var s: AppSettings {
        get { d.settings }
        set { d.settings = newValue; save() }
    }

    var companion: Companion { Companion(baseURL: d.settings.serverURL, token: d.settings.serverToken) }

    func save() {
        if let data = try? JSONEncoder().encode(d) { try? data.write(to: Store.fileURL, options: .atomic) }
    }

    func event(_ text: String, _ kind: String = "info") {
        d.events.append(LedgerEvent(ts: Date().timeIntervalSince1970, text: text, kind: kind))
        if d.events.count > 300 { d.events.removeFirst(d.events.count - 300) }
    }

    func showToast(_ t: String) {
        withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) { toast = t }
        let current = t
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 3_500_000_000)
            if self.toast == current { withAnimation(.easeOut(duration: 0.3)) { self.toast = nil } }
        }
    }

    // MARK: Ledger maths

    var pending: [Order] { d.orders.filter { $0.status == "pending" } }

    func price(_ sym: String, fallback: Double) -> Double { prices[sym] ?? fallback }

    var reservedAud: Double { pending.reduce(0) { $0 + $1.shares * $1.limit / fx } }
    var positionsValueAud: Double { d.positions.reduce(0) { $0 + $1.shares * price($1.symbol, fallback: $1.entry) / fx } }
    var equity: Double { d.cash + positionsValueAud }
    var availableCash: Double { max(0, d.cash - reservedAud) }
    var openPnl: Double {
        d.positions.reduce(0) { $0 + $1.shares * (price($1.symbol, fallback: $1.entry) / fx - $1.entry / $1.entryFx) }
    }
    var openRiskAud: Double {
        d.positions.reduce(0) { $0 + max(0, $1.entry - $1.stop) * $1.shares / fx }
            + pending.reduce(0) { $0 + max(0, $1.limit - $1.stop) * $1.shares / fx }
    }

    func sectorCount(_ sec: String) -> Int {
        d.positions.filter { $0.sector == sec }.count + pending.filter { $0.sector == sec }.count
    }

    struct Sizing { var shares: Double; var cost: Double; var risk: Double; var limitedBy: String }

    func size(entry: Double, stop: Double) -> Sizing {
        let eq = equity
        let riskAud = eq * s.riskPct / 100
        let maxPos = eq * s.maxPositionPct / 100
        let room = max(0, eq * s.maxOpenRiskPct / 100 - openRiskAud)
        let perShareRisk = max(1e-9, (entry - stop) / fx)
        let entryAud = entry / fx
        let byRisk = min(riskAud, room) / perShareRisk
        let byPos = maxPos / entryAud
        let byCash = availableCash / entryAud
        var sh = min(byRisk, min(byPos, byCash))
        sh = max(0, (sh * 10000).rounded(.down) / 10000)
        let lim = byRisk <= byPos && byRisk <= byCash ? "risk" : (byPos <= byCash ? "max position" : "cash")
        return Sizing(shares: sh, cost: sh * entryAud, risk: sh * perShareRisk, limitedBy: lim)
    }

    struct Stats { var trades = 0; var winRate = 0.0; var realized = 0.0; var avgR = 0.0; var profitFactor = 0.0 }

    var stats: Stats {
        let h = d.history
        guard !h.isEmpty else { return Stats() }
        let wins = h.filter { $0.pnlAud > 0 }
        let gp = wins.reduce(0) { $0 + $1.pnlAud }
        let gl = -h.filter { $0.pnlAud <= 0 }.reduce(0) { $0 + $1.pnlAud }
        return Stats(trades: h.count, winRate: Double(wins.count) / Double(h.count) * 100,
                     realized: h.reduce(0) { $0 + $1.pnlAud }, avgR: h.reduce(0) { $0 + $1.rMultiple } / Double(h.count),
                     profitFactor: gl > 0 ? gp / gl : (gp > 0 ? .infinity : 0))
    }

    // MARK: Actions

    func placeOrder(_ p: Pick, shares: Double) {
        let now = Date().timeIntervalSince1970
        let o = Order(id: UUID().uuidString, symbol: p.symbol, name: p.name, sector: p.sector, created: now,
                      limit: p.entry, stop: p.stop, t1: p.t1, t2: p.t2, shares: shares, status: "pending",
                      expires: now + Double(s.orderExpiryDays) * 86400 * 7 / 5, source: p.source, thesis: p.thesis)
        d.orders.append(o)
        event("Order placed: buy \(Fmt.shares(shares)) \(p.symbol) @ \(Fmt.usd(p.entry)) limit (stop \(Fmt.num(p.stop)), T1 \(Fmt.num(p.t1)), T2 \(Fmt.num(p.t2))).", "order")
        save()
        showToast("Paper order placed: \(p.symbol)")
    }

    func cancelOrder(_ id: String) {
        if let i = d.orders.firstIndex(where: { $0.id == id && $0.status == "pending" }) {
            d.orders[i].status = "cancelled"
            event("Order cancelled: \(d.orders[i].symbol).")
            save()
        }
    }

    func sellNow(_ id: String) {
        guard let i = d.positions.firstIndex(where: { $0.id == id }) else { return }
        let px = price(d.positions[i].symbol, fallback: d.positions[i].entry)
        sell(index: i, shares: d.positions[i].shares, price: px, ts: Date().timeIntervalSince1970, reason: "manual")
        save()
    }

    func reset(to amount: Double, resetSettings: Bool) {
        var st = resetSettings ? AppSettings() : d.settings
        if resetSettings {      // keep the PC connection details
            st.serverURL = d.settings.serverURL
            st.serverToken = d.settings.serverToken
        }
        d = LedgerData(start: amount, settings: st)
        event("Account reset: starting fresh with \(Fmt.aud(amount)).")
        picks = []
        advice = nil
        save()
    }

    // MARK: Engine

    private func openPosition(orderIndex oi: Int, price: Double, ts: Double) {
        var o = d.orders[oi]
        let cost = o.shares * price / fx
        d.cash -= cost
        o.status = "filled"
        o.fillPrice = price
        d.orders[oi] = o
        d.positions.append(Position(id: UUID().uuidString, symbol: o.symbol, name: o.name, sector: o.sector,
                                    shares: o.shares, origShares: o.shares, entry: price, entryFx: fx, costAud: cost,
                                    opened: ts, stop: o.stop, origStop: o.stop, t1: o.t1, t2: o.t2,
                                    thesis: o.thesis, mae: price, mfe: price, lastBarTs: ts - 1))
        event("FILLED: bought \(Fmt.shares(o.shares)) \(o.symbol) @ \(Fmt.usd(price)) (\(Fmt.aud(cost))).", "fill")
    }

    @discardableResult
    private func sell(index i: Int, shares req: Double, price: Double, ts: Double, reason: String) -> Bool {
        var p = d.positions[i]
        let sh = min(req, p.shares)
        let proceeds = sh * price / fx
        let cost = sh * p.entry / p.entryFx
        let pnl = proceeds - cost
        d.cash += proceeds
        p.shares = ((p.shares - sh) * 1e8).rounded() / 1e8
        p.realizedAud += pnl
        event("SOLD \(Fmt.shares(sh)) \(p.symbol) @ \(Fmt.usd(price)) (\(reason)) · P&L \(Fmt.aud(pnl, sign: true)).",
              pnl >= 0 ? "win" : "loss")
        if p.shares <= 1e-6 {
            d.positions.remove(at: i)
            let risk = (p.entry - p.origStop) * p.origShares / p.entryFx
            d.history.append(ClosedTrade(id: p.id, symbol: p.symbol, name: p.name, sector: p.sector, opened: p.opened,
                                         closed: ts, shares: p.origShares, entry: p.entry, exit: price,
                                         entryFx: p.entryFx, exitFx: fx, pnlAud: p.realizedAud,
                                         pnlPct: p.costAud > 0 ? p.realizedAud / p.costAud * 100 : 0,
                                         rMultiple: risk > 0 ? p.realizedAud / risk : 0, reason: reason,
                                         maePct: (p.mae - p.entry) / p.entry * 100, mfePct: (p.mfe - p.entry) / p.entry * 100,
                                         stop: p.origStop, t1: p.t1, t2: p.t2))
            return true
        }
        d.positions[i] = p
        return false
    }

    /// Apply fills/exits for bars newer than what we've already processed. Returns new events.
    func process(_ intraday: [String: Bars]) -> [LedgerEvent] {
        let now = Date().timeIntervalSince1970
        for oi in d.orders.indices where d.orders[oi].status == "pending" {
            if let b = intraday[d.orders[oi].symbol] {
                for i in 0..<b.count {
                    let t = b.ts[i]
                    if t <= max(d.orders[oi].created, d.orders[oi].lastBarTs) { continue }
                    d.orders[oi].lastBarTs = t
                    if b.l[i] <= d.orders[oi].limit {
                        openPosition(orderIndex: oi, price: min(d.orders[oi].limit, b.o[i]), ts: t)
                        break
                    }
                }
            }
            if d.orders[oi].status == "pending" && now > d.orders[oi].expires {
                d.orders[oi].status = "expired"
                event("Order expired unfilled: \(d.orders[oi].symbol) @ \(Fmt.usd(d.orders[oi].limit)).")
            }
        }
        var pi = 0
        while pi < d.positions.count {
            let sym = d.positions[pi].symbol
            guard let b = intraday[sym] else { pi += 1; continue }
            var closed = false
            for i in 0..<b.count {
                let t = b.ts[i]
                if t <= d.positions[pi].lastBarTs { continue }
                d.positions[pi].lastBarTs = t
                let lo = b.l[i], hi = b.h[i], op = b.o[i]
                d.positions[pi].mae = min(d.positions[pi].mae, lo)
                d.positions[pi].mfe = max(d.positions[pi].mfe, hi)
                let p = d.positions[pi]
                if lo <= p.stop {
                    closed = sell(index: pi, shares: p.shares, price: min(p.stop, op), ts: t,
                                  reason: p.halfTaken ? "breakeven stop" : "stop")
                    break
                }
                if !p.halfTaken && hi >= p.t1 {
                    let half = (p.shares / 2 * 10000).rounded() / 10000
                    closed = sell(index: pi, shares: half, price: max(p.t1, op), ts: t, reason: "target 1")
                    if closed { break }
                    d.positions[pi].halfTaken = true
                    d.positions[pi].stop = d.positions[pi].entry
                    event("\(sym): stop moved to breakeven \(Fmt.usd(d.positions[pi].entry)).")
                }
                if d.positions[pi].halfTaken && hi >= d.positions[pi].t2 {
                    closed = sell(index: pi, shares: d.positions[pi].shares, price: max(d.positions[pi].t2, op), ts: t,
                                  reason: "target 2")
                    break
                }
            }
            if !closed { pi += 1 }
        }
        let eq = equity
        if let last = d.equity.last {
            if now - last.ts > 600 || (abs(last.value - eq) > 0.01 && now - last.ts > 60) {
                d.equity.append(EquityPoint(ts: now, value: (eq * 100).rounded() / 100))
            }
        } else {
            d.equity.append(EquityPoint(ts: now, value: eq))
        }
        if d.equity.count > 5000 { d.equity.removeFirst(d.equity.count - 5000) }
        save()
        return d.events.filter { $0.ts >= now }
    }

    func refresh() async {
        if refreshing { return }
        refreshing = true
        defer { refreshing = false }
        fx = await Market.audusd()
        let syms = Set(d.positions.map { $0.symbol } + pending.map { $0.symbol })
        var intraday: [String: Bars] = [:]
        for sym in syms {
            if let b = try? await Market.history(sym, range: "1mo", interval: "15m", ttl: 45) {
                intraday[sym] = b
                if b.price.isFinite { prices[sym] = b.price }
            }
            if let dly = try? await Market.history(sym, range: "5d", interval: "1d", ttl: 45), dly.count > 1 {
                prevClose[sym] = dly.c[dly.count - 2]
                if prices[sym] == nil { prices[sym] = dly.c.last }
            }
        }
        let evs = process(intraday)
        lastUpdate = Date()
        for e in evs where ["fill", "win", "loss"].contains(e.kind) {
            showToast(e.text)
            if s.notifyTrades {
                let title = e.kind == "fill" ? "Order filled" : (e.kind == "win" ? "Position closed · profit" : "Position closed · loss")
                Notifier.shared.send(title, e.text)
            }
        }
    }

    // MARK: Posture + usage (PC when connected, phone proxy otherwise)

    func loadPosture(force: Bool = false) async {
        if postureBusy || (posture != nil && !force) { return }
        postureBusy = true
        defer { postureBusy = false }
        if companion.configured, let p = try? await companion.posture() {
            posture = p
            return
        }
        if let techs = try? await Scanner.fetchUniverse(progress: { _, _ in }) {
            posture = Scanner.posture(from: techs)
        }
    }

    func checkCompanion() async {
        guard companion.configured else {
            companionOK = false
            companionState = "Not set up"
            usage = []
            return
        }
        do {
            let st = try await companion.status()
            companionOK = st.claude
            companionState = st.claude ? (st.busy ? "Connected · Claude busy" : "Connected") : "PC found, but Claude CLI missing"
            if st.claude { usage = (try? await companion.usage()) ?? usage }
        } catch {
            companionOK = false
            companionState = "Can't reach PC: \(error.localizedDescription)"
        }
    }

    // MARK: Budget plan + advice

    func allocate(_ list: inout [Pick], budget: Double, targets: [String: Double]?) -> [String] {
        let eq = equity
        var cap = min(budget, availableCash)
        var notes: [String] = []
        if cap < budget - 0.01 { notes.append("limited to your available cash \(Fmt.aud(availableCash))") }
        if targets == nil, let p = posture {
            let room = max(0, eq * Double(p.ceilingPct) / 100 - positionsValueAud - reservedAud)
            if room < cap {
                cap = room
                notes.append("market posture caps total exposure at \(p.ceilingPct)% of equity")
            }
        }
        let riskRoom = max(0, eq * s.maxOpenRiskPct / 100 - openRiskAud)
        let perTrade = eq * s.riskPct / 100
        let maxPos = eq * s.maxPositionPct / 100
        for i in list.indices {
            list[i].allocShares = 0
            list[i].allocAud = 0
            list[i].planned = true
        }
        var want: [String: Double] = [:]
        if let targets {
            for p in list where (targets[p.symbol] ?? 0) > 0 { want[p.symbol] = targets[p.symbol] }
        } else {
            let top = list.prefix(5)
            for p in top { want[p.symbol] = cap / Double(max(1, top.count)) }
        }
        let total = want.values.reduce(0, +)
        let scale = total > 0 ? min(1, cap / total) : 0
        if scale < 0.999 && targets != nil { notes.append(String(format: "Claude's amounts scaled to %.0f%% to fit your budget/cash", scale * 100)) }
        var riskUsed = 0.0
        for i in list.indices {
            guard let w = want[list[i].symbol] else { continue }
            let p = list[i]
            let riskPS = (p.entry - p.stop) / fx
            var aud = min(w * scale, maxPos)
            aud = min(aud, perTrade / riskPS * p.entry / fx)
            aud = min(aud, max(0, riskRoom - riskUsed) / riskPS * p.entry / fx)
            let sh = max(0, (aud * fx / p.entry * 10000).rounded(.down) / 10000)
            if sh * p.entry / fx < 20 { continue }
            list[i].allocShares = sh
            list[i].allocAud = sh * p.entry / fx
            if list[i].allocWhy.isEmpty { list[i].allocWhy = p.bullets.first ?? "" }
            riskUsed += sh * riskPS
        }
        return notes
    }

    func builtinAdvice(_ list: [Pick], budget: Double, notes: [String]) -> Advice {
        let inPlan = list.filter { $0.allocShares > 0 }
        let inv = inPlan.reduce(0) { $0 + $1.allocAud }
        let label = posture?.label ?? "Unknown"
        var a: Advice
        if list.isEmpty || inPlan.isEmpty || label == "Defensive" {
            a = Advice(action: "dont_invest", headline: "Don't invest for now: keep your \(Fmt.aud(budget)) in cash",
                       reasoning: list.isEmpty || inPlan.isEmpty ? "Nothing passed every filter with room in your limits."
                            : "Most large caps are below their long-term averages, so new buys tend to fail.",
                       confidence: "rule-based", whatWouldChange: "More stocks back above their 200-day averages.",
                       source: "builtin")
        } else if label == "Cautious" || inv < budget * 0.6 {
            a = Advice(action: "invest_partial",
                       headline: "Invest part: \(Fmt.aud(inv)) across the top \(inPlan.count), keep \(Fmt.aud(budget - inv)) in cash",
                       reasoning: "Market posture is \(label) (\(posture?.detail ?? "")), so the plan stays below the exposure ceiling and spreads money across sectors.",
                       confidence: "rule-based", whatWouldChange: "A stronger market read would allow more exposure.",
                       source: "builtin")
        } else {
            a = Advice(action: "invest", headline: "Here are the top \(inPlan.count) stocks for your \(Fmt.aud(budget)), and why",
                       reasoning: "Market posture is \(label). The plan splits the budget across the strongest ideas, max \(s.maxPerSector) per sector.",
                       confidence: "rule-based", whatWouldChange: "A weaker market read would mean holding more cash.",
                       source: "builtin")
        }
        a.notes = notes
        return a
    }

    // MARK: Scans

    var budget: Double {
        get { s.scanBudget > 0 ? s.scanBudget : availableCash.rounded() }
        set { s.scanBudget = max(0, newValue) }
    }

    func cancelScan() {
        scanTask?.cancel()
        scanTask = nil
        withAnimation { scanning = false }
        scanProgress = 0
        scanStatus = scanKind == "claude" ? "Scan cancelled. Claude was stopped on your PC." : "Scan cancelled."
    }

    func startScan() {
        scanTask?.cancel()
        withAnimation { scanning = true; scanKind = "builtin"; picks = []; advice = nil; claudeSummary = nil }
        scanProgress = 0.01
        let exclude = Set(d.positions.map { $0.symbol })
        let topN = s.scanSize, mps = s.maxPerSector
        let pc = (companion.configured && posture?.source == "pc") ? posture : nil
        let budget = self.budget
        scanTask = Task { @MainActor in
            do {
                let (post, list) = try await Scanner.scan(topN: topN, maxPerSector: mps, exclude: exclude, pcPosture: pc) { p, m in
                    Task { @MainActor in self.scanProgress = p; self.scanStatus = m }
                }
                try Task.checkCancellation()
                self.posture = post
                var l = list
                let notes = self.allocate(&l, budget: budget, targets: nil)
                l.sort { $0.allocAud > $1.allocAud }
                let adv = self.builtinAdvice(l, budget: budget, notes: notes)
                withAnimation(.spring(response: 0.5, dampingFraction: 0.85)) {
                    self.picks = l
                    self.advice = adv
                    self.scanning = false
                }
                self.scanProgress = 1
                if self.s.notifyScans {
                    Notifier.shared.send("Market scan finished", "\(adv.headline)\n\(l.count) ideas")
                }
            } catch is CancellationError {
            } catch {
                self.scanning = false
                self.scanStatus = "Scan failed: \(error.localizedDescription)"
                if self.s.notifyScans { Notifier.shared.send("Scan failed", error.localizedDescription) }
            }
        }
    }

    func startClaudeScan() {
        guard companion.configured else { showToast("Set up the PC companion in Settings first"); return }
        scanTask?.cancel()
        withAnimation { scanning = true; scanKind = "claude"; picks = []; advice = nil; claudeSummary = nil }
        scanProgress = 0.05
        scanStatus = "Asking Claude on your PC… (usually 4–10 minutes; you can cancel)"
        let holdings = d.positions.map { "\($0.symbol) (\(Fmt.shares($0.shares)) sh, \($0.sector))" }.joined(separator: ", ")
        let exclude = Array(Set(d.positions.map { $0.symbol } + pending.map { $0.symbol }))
        let budget = self.budget
        let body: [String: Any] = [
            "equity": equity, "cash": availableCash, "holdings": holdings.isEmpty ? "nothing" : holdings,
            "budget": budget, "risk_pct": s.riskPct, "max_pos": s.maxPositionPct, "max_risk": s.maxOpenRiskPct,
            "fx": fx, "n": s.scanSize, "exclude": exclude, "max_per_sector": s.maxPerSector,
        ]
        let comp = companion
        // Keep polling briefly if the app is backgrounded mid-scan.
        var bgID = UIBackgroundTaskIdentifier.invalid
        bgID = UIApplication.shared.beginBackgroundTask(withName: "claude-scan") {
            UIApplication.shared.endBackgroundTask(bgID)
            bgID = .invalid
        }
        scanTask = Task { @MainActor in
            defer {
                if bgID != .invalid { UIApplication.shared.endBackgroundTask(bgID) }
            }
            do {
                let id = try await comp.startScan(body)
                let job = try await comp.wait(id) { el in
                    Task { @MainActor in
                        self.scanStatus = String(format: "Claude is researching on your PC… %dm %02ds", Int(el) / 60, Int(el) % 60)
                        self.scanProgress = min(0.95, 0.05 + el / 600)
                    }
                }
                guard job.status == "done", let res = job.result else {
                    throw Companion.Failure(message: job.error ?? "Claude job \(job.status)")
                }
                let claude = res["claude"] as? [String: Any] ?? [:]
                var list = (res["picks"] as? [[String: Any]] ?? []).compactMap { Companion.pick($0) }
                let dropped = res["dropped"] as? [String] ?? []
                let adv = claude["advice"] as? [String: Any] ?? [:]
                var targets: [String: Double] = [:]
                var extra: [String] = []
                let kept = Set(list.map { $0.symbol })
                for a in adv["allocations"] as? [[String: Any]] ?? [] {
                    let sym = (a["symbol"] as? String ?? "").uppercased()
                    let amt = Companion.d(a["aud"])
                    guard amt.isFinite, amt > 0 else { continue }
                    if kept.contains(sym) {
                        targets[sym] = amt
                        if let i = list.firstIndex(where: { $0.symbol == sym }) { list[i].allocWhy = a["why"] as? String ?? "" }
                    } else {
                        extra.append("Claude suggested \(Fmt.aud(amt)) in \(sym), but it failed the app's checks, so that money stays in cash.")
                    }
                }
                let notes = self.allocate(&list, budget: budget, targets: targets) + extra
                list.sort { $0.allocAud > $1.allocAud }
                let advice = Advice(action: adv["action"] as? String ?? "", headline: adv["headline"] as? String ?? "Claude's advice",
                                    reasoning: adv["reasoning"] as? String ?? "", confidence: adv["confidence"] as? String ?? "?",
                                    whatWouldChange: adv["what_would_change"] as? String ?? "", source: "claude", notes: notes)
                withAnimation(.spring(response: 0.5, dampingFraction: 0.85)) {
                    self.picks = list
                    self.advice = advice
                    self.claudeSummary = "\(claude["posture"] as? String ?? "") · \(claude["summary"] as? String ?? "")"
                    self.scanning = false
                }
                self.scanProgress = 1
                self.scanStatus = "Claude returned \((claude["picks"] as? [Any])?.count ?? 0) idea(s); \(list.count) passed the checks."
                    + (dropped.isEmpty ? "" : " Dropped: " + dropped.joined(separator: "; "))
                if self.s.notifyScans { Notifier.shared.send("Claude scan finished", advice.headline) }
                await self.checkCompanion()
            } catch is CancellationError {
            } catch {
                self.scanning = false
                self.scanStatus = "Claude scan failed: \(error.localizedDescription)"
                if self.s.notifyScans { Notifier.shared.send("Claude scan failed", error.localizedDescription) }
            }
        }
    }
}
