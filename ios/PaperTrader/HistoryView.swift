import SwiftUI

struct HistoryView: View {
    @EnvironmentObject var store: Store

    var csv: String {
        var s = "symbol,sector,opened,closed,shares,entry,exit,pnl_aud,pnl_pct,r_multiple,reason,mae_pct,mfe_pct\n"
        for h in store.d.history {
            var cols: [String] = [h.symbol, h.sector]
            cols.append(Fmt.date(h.opened, "yyyy-MM-dd HH:mm", ny: false))
            cols.append(Fmt.date(h.closed, "yyyy-MM-dd HH:mm", ny: false))
            cols += [Fmt.shares(h.shares), Fmt.num(h.entry), Fmt.num(h.exit), Fmt.num(h.pnlAud), Fmt.num(h.pnlPct)]
            cols += [Fmt.num(h.rMultiple), h.reason, Fmt.num(h.maePct), Fmt.num(h.mfePct)]
            s += cols.joined(separator: ",") + "\n"
        }
        return s
    }

    var body: some View {
        let st = store.stats
        ScrollView {
            VStack(spacing: 12) {
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
                    StatTile(title: "Closed trades", value: Double(st.trades), format: { String(Int($0)) })
                    StatTile(title: "Win rate", value: st.winRate, format: { String(format: "%.0f%%", $0) })
                    StatTile(title: "Realised", value: st.realized, format: { Fmt.aud($0, sign: true) }, color: T.pnl(st.realized))
                    StatTile(title: "Avg R", value: st.avgR, format: { String(format: "%+.2fR", $0) }, color: T.pnl(st.avgR))
                }
                if store.d.history.isEmpty {
                    Text("No closed trades yet. When a stop, target or manual sell closes a position it lands here with its P&L, R multiple and biggest moves against and for you.")
                        .font(.system(size: 14)).foregroundStyle(T.muted).card()
                }
                ForEach(Array(store.d.history.reversed().enumerated()), id: \.element.id) { i, h in
                    ClosedRow(h: h).entrance(min(0.04 * Double(i), 0.5))
                }
            }
            .padding(.horizontal, 14)
            .padding(.bottom, 24)
        }
        .background(T.bg)
        .navigationTitle("History")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                if !store.d.history.isEmpty {
                    ShareLink(item: csv, preview: SharePreview("Paper trades.csv")) { Image(systemName: "square.and.arrow.up") }
                }
            }
        }
    }
}

struct ClosedRow: View {
    var h: ClosedTrade
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(h.symbol).font(.system(size: 17, weight: .black, design: .monospaced)).foregroundStyle(T.primary)
                Chip(text: h.reason)
                Spacer()
                Text(Fmt.aud(h.pnlAud, sign: true)).font(.system(size: 15, weight: .bold, design: .monospaced))
                    .foregroundStyle(T.pnl(h.pnlAud))
            }
            HStack(spacing: 12) {
                KV(title: "DATES", value: "\(Fmt.date(h.opened, "d MMM", ny: false)) → \(Fmt.date(h.closed, "d MMM", ny: false))")
                KV(title: "IN → OUT", value: "\(Fmt.num(h.entry)) → \(Fmt.num(h.exit))")
                KV(title: "RETURN", value: Fmt.pct(h.pnlPct), color: T.pnl(h.pnlPct))
                KV(title: "R", value: String(format: "%+.2f", h.rMultiple), color: T.pnl(h.rMultiple))
            }
            Text(String(format: "Biggest move against %.1f%% · for +%.1f%%", h.maePct, h.mfePct))
                .font(.system(size: 11)).foregroundStyle(T.faint)
        }
        .card()
    }
}
