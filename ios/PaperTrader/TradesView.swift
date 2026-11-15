import SwiftUI

struct TradeRoute: Hashable {
    var symbol: String
    var entry: Double
    var stop: Double
    var t1: Double
    var t2: Double
    var subtitle: String
}

struct TradesView: View {
    @EnvironmentObject var store: Store
    @State private var tab = 0

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                Picker("", selection: $tab) {
                    Text("Active (\(store.d.positions.count))").tag(0)
                    Text("Pending (\(store.pending.count))").tag(1)
                }
                .pickerStyle(.segmented)
                if tab == 0 {
                    if store.d.positions.isEmpty {
                        empty("No open positions yet. Confirm an idea in the Scanner; filled orders show up here.")
                    }
                    ForEach(Array(store.d.positions.enumerated()), id: \.element.id) { i, p in
                        PositionCardView(p: p).entrance(0.06 * Double(i))
                    }
                } else {
                    if store.pending.isEmpty { empty("No pending orders.") }
                    ForEach(Array(store.pending.enumerated()), id: \.element.id) { i, o in
                        OrderCardView(o: o).entrance(0.06 * Double(i))
                    }
                }
            }
            .padding(.horizontal, 14)
            .padding(.bottom, 24)
            .animation(.spring(response: 0.45, dampingFraction: 0.85), value: tab)
        }
        .background(T.bg)
        .navigationTitle("Trades")
        .navigationDestination(for: TradeRoute.self) { r in
            DetailView(symbol: r.symbol, name: r.symbol, plan: (r.entry, r.stop, r.t1, r.t2), subtitle: r.subtitle)
        }
        .refreshable { await store.refresh() }
    }

    func empty(_ t: String) -> some View {
        Text(t).font(.system(size: 14)).foregroundStyle(T.muted).card()
    }
}

struct PositionCardView: View {
    @EnvironmentObject var store: Store
    var p: Position
    @State private var confirmSell = false

    var body: some View {
        let px = store.price(p.symbol, fallback: p.entry)
        let val = p.shares * px / store.fx
        let cost = p.shares * p.entry / p.entryFx
        let pnl = val - cost + p.realizedAud
        let days = (Date().timeIntervalSince1970 - p.opened) / 86400
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(p.symbol).font(.system(size: 20, weight: .black, design: .monospaced)).foregroundStyle(T.primary)
                Chip(text: "\(Int(days))d held")
                if p.halfTaken { Chip(text: "T1 hit · stop at breakeven", bg: T.green.opacity(0.2), fg: T.green) }
                if days > Double(store.s.maxHoldDays) && px < p.t1 { Chip(text: "Time exit due", bg: T.red.opacity(0.2), fg: T.red) }
                Spacer()
                VStack(alignment: .trailing, spacing: 1) {
                    Text(Fmt.aud(pnl, sign: true)).font(.system(size: 16, weight: .bold, design: .monospaced)).foregroundStyle(T.pnl(pnl))
                    Text(Fmt.pct(cost > 0 ? pnl / cost * 100 : 0)).font(.system(size: 11, design: .monospaced)).foregroundStyle(T.pnl(pnl))
                }
            }
            HStack(spacing: 12) {
                KV(title: "SHARES", value: Fmt.shares(p.shares))
                KV(title: "ENTRY", value: Fmt.num(p.entry))
                KV(title: "NOW", value: Fmt.num(px))
                KV(title: "VALUE", value: Fmt.aud(val))
                KV(title: "STOP", value: Fmt.num(p.stop), color: T.red)
            }
            PriceTrack(stop: p.origStop, entry: p.entry, t1: p.t1, t2: p.t2, price: px)
            HStack {
                Spacer()
                NavigationLink(value: TradeRoute(symbol: p.symbol, entry: p.entry, stop: p.stop, t1: p.t1, t2: p.t2,
                                                 subtitle: "open position")) {
                    Label("View", systemImage: "eye")
                }
                .buttonStyle(PillButtonStyle(bg: T.red, fg: T.onRed, small: true))
                Button("Sell now") { confirmSell = true }
                    .buttonStyle(PillButtonStyle(bg: T.cardHi, fg: T.fg, small: true))
            }
        }
        .card()
        .confirmationDialog("Sell \(Fmt.shares(p.shares)) \(p.symbol) at about \(Fmt.usd(px))?", isPresented: $confirmSell,
                            titleVisibility: .visible) {
            Button("Sell (paper)", role: .destructive) { store.sellNow(p.id) }
        }
    }
}

struct OrderCardView: View {
    @EnvironmentObject var store: Store
    var o: Order

    var body: some View {
        let px = store.prices[o.symbol]
        let hrs = (o.expires - Date().timeIntervalSince1970) / 3600
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(o.symbol).font(.system(size: 20, weight: .black, design: .monospaced)).foregroundStyle(T.primary)
                Chip(text: "Pending limit", bg: T.tertiary.opacity(0.2), fg: T.tertiary)
                Spacer()
                Text(hrs > 0 ? String(format: "expires in %.1fd", hrs / 24) : "expiring").font(.system(size: 11)).foregroundStyle(T.faint)
            }
            HStack(spacing: 12) {
                KV(title: "SHARES", value: Fmt.shares(o.shares))
                KV(title: "LIMIT", value: Fmt.num(o.limit))
                KV(title: "NOW", value: px.map { Fmt.num($0) } ?? "…")
                if let px { KV(title: "TO FILL", value: Fmt.pct((o.limit / px - 1) * 100)) }
                KV(title: "COST", value: Fmt.aud(o.shares * o.limit / store.fx))
            }
            Text("Stop \(Fmt.num(o.stop)) · T1 \(Fmt.num(o.t1)) · T2 \(Fmt.num(o.t2))")
                .font(.system(size: 12, design: .monospaced)).foregroundStyle(T.muted)
            HStack {
                Spacer()
                NavigationLink(value: TradeRoute(symbol: o.symbol, entry: o.limit, stop: o.stop, t1: o.t1, t2: o.t2,
                                                 subtitle: "pending order")) {
                    Label("View", systemImage: "eye")
                }
                .buttonStyle(PillButtonStyle(bg: T.red, fg: T.onRed, small: true))
                Button("Cancel") { withAnimation { store.cancelOrder(o.id) } }
                    .buttonStyle(PillButtonStyle(bg: T.cardHi, fg: T.fg, small: true))
            }
        }
        .card()
    }
}
