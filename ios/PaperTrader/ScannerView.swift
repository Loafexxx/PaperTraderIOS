import SwiftUI

struct ScannerView: View {
    @EnvironmentObject var store: Store
    @State private var confirmPick: Pick?
    @State private var budgetText = ""
    @FocusState private var budgetFocused: Bool

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                controls
                if let a = store.advice {
                    AdviceCardView(advice: a, picks: store.picks, budget: store.budget)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
                if let s = store.claudeSummary, !s.isEmpty {
                    Text(s).font(.system(size: 12)).foregroundStyle(T.tertiary).card(T.tertiary)
                }
                ForEach(Array(store.picks.enumerated()), id: \.element.id) { i, p in
                    PickCardView(pick: p, onConfirm: { confirmPick = p })
                        .entrance(0.08 * Double(i))
                }
                if !store.scanning && store.advice != nil && store.picks.isEmpty {
                    Text("Nothing passed every filter right now. That's a valid answer: cash is a position.")
                        .font(.system(size: 14)).foregroundStyle(T.muted).card()
                }
            }
            .padding(.horizontal, 14)
            .padding(.bottom, 24)
            .animation(.spring(response: 0.5, dampingFraction: 0.85), value: store.picks.count)
        }
        .background(T.bg)
        .navigationTitle("Scanner")
        .navigationDestination(for: String.self) { sym in
            if let p = store.picks.first(where: { $0.symbol == sym }) {
                DetailView(symbol: p.symbol, name: p.name, plan: (p.entry, p.stop, p.t1, p.t2), subtitle: p.sector)
            }
        }
        .sheet(item: $confirmPick) { p in ConfirmSheet(pick: p).presentationDetents([.medium, .large]) }
        .onAppear { budgetText = String(Int(store.budget)) }
        .toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") { budgetFocused = false }
            }
        }
    }

    private var controls: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Scans ~150 liquid US stocks: uptrend, not over-extended, EPS growth, sane valuation, no earnings within 14 days, max \(store.s.maxPerSector) per sector. Or let Claude on your PC run your trading skills.")
                .font(.system(size: 12)).foregroundStyle(T.faint)
            HStack(spacing: 8) {
                Image(systemName: "banknote").foregroundStyle(T.primary)
                Text("Budget").font(.system(size: 14, weight: .semibold))
                TextField("A$", text: $budgetText)
                    .keyboardType(.numberPad)
                    .focused($budgetFocused)
                    .font(.system(size: 15, design: .monospaced))
                    .padding(.horizontal, 10).padding(.vertical, 7)
                    .background(RoundedRectangle(cornerRadius: 10).fill(T.cardHi))
                    .frame(width: 110)
                    .onChange(of: budgetText) { _, v in
                        if let n = Double(v.filter { $0.isNumber }) { store.budget = n }
                    }
                Spacer(minLength: 0)
            }
            HStack(spacing: 6) {
                ForEach(["25%", "50%", "All cash"], id: \.self) { name in
                    Button(name) {
                        let frac = name == "25%" ? 0.25 : (name == "50%" ? 0.5 : 1.0)
                        let v = (store.availableCash * frac).rounded()
                        store.budget = v
                        budgetText = String(Int(v))
                    }
                    .buttonStyle(PillButtonStyle(bg: T.cardHi, fg: T.fg, small: true))
                }
                Spacer()
                Text("cash \(Fmt.aud(store.availableCash))").font(.system(size: 11)).foregroundStyle(T.faint)
            }
            HStack(spacing: 8) {
                Button { budgetFocused = false; store.startScan() } label: {
                    Label("Scan market", systemImage: "magnifyingglass").frame(maxWidth: .infinity)
                }
                .buttonStyle(PillButtonStyle(bg: T.primary, fg: T.onPrimary))
                .disabled(store.scanning)
                .opacity(store.scanning ? 0.5 : 1)
                Button { budgetFocused = false; store.startClaudeScan() } label: {
                    Label("Ask Claude", systemImage: "sparkles").frame(maxWidth: .infinity)
                }
                .buttonStyle(PillButtonStyle(bg: T.tertiary, fg: T.onTertiary))
                .disabled(store.scanning || !store.companion.configured)
                .opacity(store.scanning || !store.companion.configured ? 0.5 : 1)
            }
            if store.scanning || store.scanProgress > 0 {
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        if store.scanning { ProgressView().scaleEffect(0.8) }
                        Text(store.scanStatus).font(.system(size: 12)).foregroundStyle(T.muted).lineLimit(4)
                        Spacer()
                        if store.scanning {
                            Button { store.cancelScan() } label: { Label("Cancel", systemImage: "xmark") }
                                .buttonStyle(PillButtonStyle(bg: T.red.opacity(0.2), fg: T.red, small: true))
                                .transition(.scale.combined(with: .opacity))
                        }
                    }
                    ProgressView(value: store.scanProgress).tint(T.primary)
                        .animation(.easeOut(duration: 0.3), value: store.scanProgress)
                }
            } else {
                Text(store.scanStatus).font(.system(size: 12)).foregroundStyle(T.muted)
            }
            if !store.companion.configured {
                Text("Ask Claude needs the PC companion: Settings → PC companion.").font(.system(size: 11)).foregroundStyle(T.faint)
            }
        }
        .card()
        .animation(.spring(response: 0.4, dampingFraction: 0.85), value: store.scanning)
    }
}

struct AdviceCardView: View {
    var advice: Advice
    var picks: [Pick]
    var budget: Double

    var style: (Color, String, String) {
        switch advice.action {
        case "dont_invest": return (T.red, "hand.raised.fill", "Keep cash")
        case "wait_for_pullback": return (T.primary, "clock.fill", "Wait")
        case "invest_partial": return (T.tertiary, "chart.pie.fill", "Invest part")
        case "invest": return (T.green, "checkmark.seal.fill", "Invest")
        default: return (T.muted, "info.circle.fill", "Advice")
        }
    }

    var body: some View {
        let st = style
        let col = st.0
        let icon = st.1
        let tag = st.2
        let alloc = picks.filter { $0.allocShares > 0 }
        let inv = alloc.reduce(0) { $0 + $1.allocAud }
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: icon).font(.system(size: 26)).foregroundStyle(col)
                VStack(alignment: .leading, spacing: 2) {
                    Text(advice.headline).font(.system(size: 18, weight: .bold)).foregroundStyle(col)
                    Text((advice.source == "claude" ? "Claude advice" : "Budget plan · built-in rules (not Claude)")
                         + " · confidence \(advice.confidence)")
                        .font(.system(size: 10)).foregroundStyle(T.faint)
                }
                Spacer(minLength: 0)
                Chip(text: tag, bg: col.opacity(0.2), fg: col)
            }
            if !advice.reasoning.isEmpty {
                Text(advice.reasoning).font(.system(size: 13)).foregroundStyle(T.fg)
            }
            HStack(spacing: 14) {
                KV(title: "BUDGET", value: Fmt.aud(budget))
                KV(title: "INVEST", value: Fmt.aud(inv), color: col)
                KV(title: "CASH", value: Fmt.aud(max(0, budget - inv)))
            }
            ForEach(alloc) { p in
                HStack(alignment: .top, spacing: 10) {
                    Text(p.symbol).font(.system(size: 15, weight: .black, design: .monospaced)).foregroundStyle(T.primary)
                        .frame(width: 58, alignment: .leading)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(Fmt.aud(p.allocAud)) · \(Fmt.shares(p.allocShares)) sh @ \(Fmt.num(p.entry))")
                            .font(.system(size: 12, design: .monospaced)).foregroundStyle(T.fg)
                        ProgressView(value: min(p.allocAud / max(budget, 1), 1)).tint(col)
                        Text(p.allocWhy).font(.system(size: 12)).foregroundStyle(T.muted)
                    }
                }
            }
            if !advice.whatWouldChange.isEmpty {
                Text("What would change this: \(advice.whatWouldChange)").font(.system(size: 12)).foregroundStyle(T.faint)
            }
            ForEach(advice.notes, id: \.self) { n in
                Text("• \(n)").font(.system(size: 11)).foregroundStyle(T.faint)
            }
            Text("Paper-trading suggestions, not financial advice.").font(.system(size: 10)).foregroundStyle(T.faint)
        }
        .card(col)
    }
}

struct PickCardView: View {
    @EnvironmentObject var store: Store
    var pick: Pick
    var onConfirm: () -> Void

    var body: some View {
        let held = store.d.positions.contains { $0.symbol == pick.symbol } || store.pending.contains { $0.symbol == pick.symbol }
        let sz = store.size(entry: pick.entry, stop: pick.stop)
        let chg = pick.prev > 0 ? (pick.price / pick.prev - 1) * 100 : 0
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(pick.symbol).font(.system(size: 24, weight: .black, design: .monospaced)).foregroundStyle(T.primary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(pick.name).font(.system(size: 12)).foregroundStyle(T.muted).lineLimit(1)
                    HStack(spacing: 4) {
                        Chip(text: pick.sector.isEmpty ? "—" : pick.sector)
                        if pick.source == "claude" { Chip(text: "Claude", bg: T.tertiary.opacity(0.2), fg: T.tertiary) }
                    }
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    Text(Fmt.usd(pick.price)).font(.system(size: 14, design: .monospaced))
                    Text(Fmt.pct(chg)).font(.system(size: 12, design: .monospaced)).foregroundStyle(T.pnl(chg))
                }
            }
            ForEach(Array(pick.bullets.prefix(5).enumerated()), id: \.offset) { _, b in
                Text("• \(b)").font(.system(size: 12)).foregroundStyle(b.hasPrefix("⚠") ? T.primary : T.muted)
            }
            HStack(spacing: 12) {
                KV(title: "ENTRY", value: Fmt.num(pick.entry))
                KV(title: "STOP", value: Fmt.num(pick.stop), color: T.red)
                KV(title: "T1", value: Fmt.num(pick.t1), color: T.green)
                KV(title: "T2", value: Fmt.num(pick.t2), color: T.primary)
                KV(title: "R:R", value: String(format: "%.1f", pick.rr1))
            }
            if let o = pick.odds {
                VStack(alignment: .leading, spacing: 4) {
                    OddsBar(odds: o)
                    Text(String(format: "model: stop %.0f%% · T1 %.0f%% · T2 %.0f%% · EV %+.2f%%", o.stop * 100, o.t1 * 100, o.t2 * 100, o.expPct))
                        .font(.system(size: 10)).foregroundStyle(T.faint)
                }
            }
            if let c = pick.claudeOdds {
                Text(String(format: "Claude: bear %.0f%% · base %.0f%% · bull %.0f%% · confidence ", c.bear * 100, c.base * 100, c.bull * 100) + c.confidence)
                    .font(.system(size: 11)).foregroundStyle(T.tertiary)
            }
            HStack {
                if pick.allocShares > 0 {
                    Text("Budget plan: \(Fmt.shares(pick.allocShares)) sh · \(Fmt.aud(pick.allocAud))")
                        .font(.system(size: 12, weight: .semibold)).foregroundStyle(T.green)
                } else if pick.planned {
                    Text("Not in budget plan").font(.system(size: 12)).foregroundStyle(T.faint)
                } else {
                    Text("\(Fmt.shares(sz.shares)) sh · \(Fmt.aud(sz.cost))").font(.system(size: 12)).foregroundStyle(T.muted)
                }
                Spacer()
                Button(action: onConfirm) { Label(held ? "Holding" : "Confirm", systemImage: "checkmark") }
                    .buttonStyle(PillButtonStyle(bg: held ? T.cardHi : T.green, fg: held ? T.green : T.onGreen, small: true))
                    .disabled(held || sz.shares <= 0)
                NavigationLink(value: pick.symbol) { Label("View", systemImage: "eye") }
                    .buttonStyle(PillButtonStyle(bg: T.red, fg: T.onRed, small: true))
            }
        }
        .card()
    }
}

struct ConfirmSheet: View {
    @EnvironmentObject var store: Store
    @Environment(\.dismiss) private var dismiss
    var pick: Pick
    @State private var sharesText = ""

    var body: some View {
        let sz = store.size(entry: pick.entry, stop: pick.stop)
        let sh = Double(sharesText) ?? 0
        let cost = sh * pick.entry / store.fx
        let loss = sh * (pick.entry - pick.stop) / store.fx
        let g1 = sh * (pick.t1 - pick.entry) / store.fx
        let eq = max(store.equity, 1)
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("Buy \(pick.symbol)").font(.system(size: 24, weight: .bold))
                Text("Paper order only. Nothing is sent to a real broker.").font(.system(size: 12)).foregroundStyle(T.faint)
                HStack(spacing: 16) {
                    KV(title: "LIMIT", value: Fmt.usd(pick.entry))
                    KV(title: "STOP", value: "\(Fmt.num(pick.stop)) (\(String(format: "%.1f", pick.stopPct))%)", color: T.red)
                }
                HStack(spacing: 16) {
                    KV(title: "T1 · SELL HALF", value: "\(Fmt.num(pick.t1)) (+\(String(format: "%.1f", pick.t1Pct))%)", color: T.green)
                    KV(title: "T2", value: "\(Fmt.num(pick.t2)) (+\(String(format: "%.1f", pick.t2Pct))%)", color: T.primary)
                }
                HStack {
                    Text("Shares").font(.system(size: 15, weight: .semibold))
                    TextField("0", text: $sharesText).keyboardType(.decimalPad)
                        .font(.system(size: 17, design: .monospaced))
                        .padding(10).background(RoundedRectangle(cornerRadius: 10).fill(T.cardHi))
                }
                Text(pick.allocShares > 0 ? "Budget plan \(Fmt.shares(pick.allocShares)) · max allowed \(Fmt.shares(sz.shares))"
                                          : "Suggested \(Fmt.shares(sz.shares)) (limited by \(sz.limitedBy))")
                    .font(.system(size: 12)).foregroundStyle(T.faint)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Cost \(Fmt.aud(cost)) (\(String(format: "%.1f", cost / eq * 100))% of equity)")
                    Text("Max loss at stop \(Fmt.aud(-loss)) (\(String(format: "%.2f", loss / eq * 100))%)").foregroundStyle(T.red)
                    Text("Gain at T1 \(Fmt.aud(g1, sign: true))").foregroundStyle(T.green)
                    Text("AUD/USD \(Fmt.num(store.fx, 4))").foregroundStyle(T.faint)
                }
                .font(.system(size: 13, design: .monospaced))
                ForEach(warnings, id: \.self) { w in
                    Text("⚠  \(w)").font(.system(size: 12)).foregroundStyle(T.onPrimaryContainer)
                        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: 10).fill(T.primaryContainer))
                }
                HStack {
                    Button("Cancel") { dismiss() }.buttonStyle(PillButtonStyle(bg: T.cardHi, fg: T.fg))
                    Spacer()
                    Button { store.placeOrder(pick, shares: sh); dismiss() } label: {
                        Label("Place paper order", systemImage: "checkmark")
                    }
                    .buttonStyle(PillButtonStyle(bg: T.green, fg: T.onGreen))
                    .disabled(sh <= 0 || cost > store.availableCash + 0.01)
                }
            }
            .padding(20)
        }
        .background(T.bg)
        .onAppear {
            let v = pick.allocShares > 0 ? pick.allocShares : sz.shares
            sharesText = Fmt.shares(v)
        }
    }

    var warnings: [String] {
        var w: [String] = []
        if let p = store.posture, p.label == "Cautious" || p.label == "Defensive" {
            w.append("Market posture is \(p.label) (exposure ceiling ~\(p.ceilingPct)%). Consider a smaller size.")
        }
        if store.sectorCount(pick.sector) >= store.s.maxPerSector {
            w.append("You already have \(store.s.maxPerSector) trades in \(pick.sector).")
        }
        if let e = pick.earnings {
            let f = DateFormatter()
            f.dateFormat = "yyyy-MM-dd"
            if let d = f.date(from: e) {
                let days = Int(d.timeIntervalSinceNow / 86400)
                if days >= 0 && days <= 45 { w.append("Earnings in \(days) days, inside a typical holding period.") }
            }
        }
        return w
    }
}
