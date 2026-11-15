import Charts
import SwiftUI

struct DashboardView: View {
    @EnvironmentObject var store: Store

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                MarketHeader()
                StatsGrid()
                EquityCard().entrance(0.1)
                PostureCard().entrance(0.15)
                if store.companion.configured { UsageCard().entrance(0.2) }
                ActivityCard().entrance(0.25)
            }
            .padding(.horizontal, 14)
            .padding(.bottom, 24)
        }
        .background(T.bg)
        .navigationTitle("Paper Trader")
        .refreshable { await store.refresh() }
    }
}

struct MarketHeader: View {
    @EnvironmentObject var store: Store
    @State private var pulse = false
    var body: some View {
        let st = Market.status()
        HStack(spacing: 8) {
            Circle().fill(st.open ? T.green : T.faint).frame(width: 8, height: 8)
                .scaleEffect(st.open && pulse ? 1.6 : 1).opacity(st.open && pulse ? 0.5 : 1)
                .animation(st.open ? .easeInOut(duration: 1).repeatForever(autoreverses: true) : .default, value: pulse)
            Text(st.text).font(.system(size: 13)).foregroundStyle(T.muted)
            Spacer()
            Chip(text: "AUD/USD \(Fmt.num(store.fx, 4))")
            if store.refreshing { ProgressView().scaleEffect(0.7) }
        }
        .onAppear { pulse = true }
    }
}

struct StatsGrid: View {
    @EnvironmentObject var store: Store
    var body: some View {
        let st = store.stats
        let eq = store.equity
        let cols = [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]
        LazyVGrid(columns: cols, spacing: 10) {
            StatTile(title: "Equity", value: eq, format: { Fmt.aud($0) },
                     sub: Fmt.pct((eq / store.d.startingCash - 1) * 100) + " since start").entrance(0)
            StatTile(title: "Cash", value: store.d.cash, format: { Fmt.aud($0) },
                     sub: "\(store.pending.count) pending order(s)").entrance(0.04)
            StatTile(title: "Invested", value: store.positionsValueAud, format: { Fmt.aud($0) },
                     sub: "\(store.d.positions.count) position(s)").entrance(0.08)
            StatTile(title: "Open P&L", value: store.openPnl, format: { Fmt.aud($0, sign: true) },
                     color: T.pnl(store.openPnl)).entrance(0.12)
            StatTile(title: "Realised P&L", value: st.realized, format: { Fmt.aud($0, sign: true) },
                     sub: "\(st.trades) closed · win \(Int(st.winRate))%", color: T.pnl(st.realized)).entrance(0.16)
            StatTile(title: "Open risk", value: store.openRiskAud, format: { Fmt.aud($0) },
                     sub: Fmt.pct(store.openRiskAud / max(eq, 1) * 100, sign: false) + " if all stops hit").entrance(0.2)
        }
    }
}

struct EquityCard: View {
    @EnvironmentObject var store: Store
    @State private var selected: Date?

    var body: some View {
        let pts = store.d.equity
        let start = store.d.startingCash
        let last = pts.last?.value ?? start
        let col = T.pnl(last - start)
        let base = min(start, pts.map { $0.value }.min() ?? start) * 0.998
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                SectionTitle(text: "Account equity", icon: "chart.xyaxis.line")
                Chip(text: Fmt.pct((last / start - 1) * 100), bg: col.opacity(0.2), fg: col)
            }
            Chart {
                RuleMark(y: .value("Start", start)).foregroundStyle(T.outline)
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
                ForEach(pts) { p in
                    AreaMark(x: .value("Time", Date(timeIntervalSince1970: p.ts)), yStart: .value("Base", base),
                             yEnd: .value("Equity", p.value))
                        .foregroundStyle(col.opacity(0.15))
                    LineMark(x: .value("Time", Date(timeIntervalSince1970: p.ts)), y: .value("Equity", p.value))
                        .foregroundStyle(col).lineStyle(StrokeStyle(lineWidth: 2))
                }
                if let sel = selected, let p = nearest(sel, pts) {
                    RuleMark(x: .value("Sel", Date(timeIntervalSince1970: p.ts)))
                        .foregroundStyle(T.muted.opacity(0.5))
                        .annotation(position: .top, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                            Tooltip(lines: [Fmt.date(p.ts, "EEE d MMM HH:mm", ny: false), Fmt.aud(p.value),
                                            Fmt.pct((p.value / start - 1) * 100) + " since start"])
                        }
                }
            }
            .chartXSelection(value: $selected)
            .chartYScale(domain: .automatic(includesZero: false))
            .frame(height: 180)
        }
        .card()
    }

    func nearest(_ d: Date, _ pts: [EquityPoint]) -> EquityPoint? {
        let t = d.timeIntervalSince1970
        return pts.min { abs($0.ts - t) < abs($1.ts - t) }
    }
}

struct Tooltip: View {
    var lines: [String]
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(Array(lines.enumerated()), id: \.offset) { i, l in
                Text(l).font(.system(size: i == 0 ? 10 : 12, weight: i == 1 ? .bold : .regular, design: .monospaced))
                    .foregroundStyle(i == 0 ? T.faint : (i == 1 ? T.primary : T.fg))
            }
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8).fill(T.cardHi))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(T.outline, lineWidth: 1))
    }
}

struct PostureCard: View {
    @EnvironmentObject var store: Store
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                SectionTitle(text: "Market posture", icon: "gauge.with.dots.needle.50percent")
                if store.postureBusy { ProgressView().scaleEffect(0.8) }
                Button { Task { await store.loadPosture(force: true) } } label: { Image(systemName: "arrow.clockwise") }
                    .buttonStyle(PillButtonStyle(bg: T.cardHi, fg: T.fg, small: true))
            }
            if let p = store.posture {
                let col = p.label == "Defensive" ? T.red : (p.label == "Risk-on" ? T.green : T.primary)
                Text("\(p.label) · exposure ≤ \(p.ceilingPct)%").font(.system(size: 20, weight: .bold)).foregroundStyle(col)
                Text(p.detail).font(.system(size: 12)).foregroundStyle(T.faint)
                ProgressView(value: Double(p.ceilingPct), total: 100).tint(col)
                if !p.strong.isEmpty {
                    Text("Strong: \(p.strong.joined(separator: ", "))\nWeak: \(p.weak.joined(separator: ", "))")
                        .font(.system(size: 12)).foregroundStyle(T.muted)
                }
                Text(p.source == "pc" ? "From your trading skills on the PC" : "Phone estimate (connect the PC for the full skills)")
                    .font(.system(size: 10)).foregroundStyle(T.faint)
            } else {
                Text("Reading the market…").font(.system(size: 13)).foregroundStyle(T.faint)
            }
        }
        .card()
    }
}

struct UsageCard: View {
    @EnvironmentObject var store: Store
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                SectionTitle(text: "Claude usage", icon: "sparkles")
                Button { Task { await store.checkCompanion() } } label: { Image(systemName: "arrow.clockwise") }
                    .buttonStyle(PillButtonStyle(bg: T.cardHi, fg: T.fg, small: true))
            }
            if store.usage.isEmpty {
                Text(store.companionState).font(.system(size: 12)).foregroundStyle(T.faint)
            } else {
                UsageBars(rows: store.usage)
            }
        }
        .card()
    }
}

struct ActivityCard: View {
    @EnvironmentObject var store: Store
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionTitle(text: "Activity", icon: "list.bullet")
            ForEach(Array(store.d.events.suffix(25).reversed())) { e in
                HStack(alignment: .top, spacing: 8) {
                    Circle().fill(color(e.kind)).frame(width: 7, height: 7).padding(.top, 5)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(Fmt.date(e.ts, "EEE d MMM HH:mm", ny: false)).font(.system(size: 10, design: .monospaced))
                            .foregroundStyle(T.faint)
                        Text(e.text).font(.system(size: 12)).foregroundStyle(T.fg)
                    }
                }
            }
        }
        .card()
    }

    func color(_ k: String) -> Color {
        switch k {
        case "fill": return T.primary
        case "win": return T.green
        case "loss", "error": return T.red
        case "order": return T.tertiary
        default: return T.muted
        }
    }
}
