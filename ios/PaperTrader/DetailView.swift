import Charts
import SwiftUI

struct HistPoint: Identifiable {
    var id: Double { ts }
    var ts: Double
    var date: Date
    var close: Double
    var sma50: Double
}

struct FcPoint: Identifiable {
    var id: Double { ts }
    var ts: Double
    var date: Date
    var day: Int
    var p10: Double
    var p25: Double
    var p50: Double
    var p75: Double
    var p90: Double
}

struct Marker: Identifiable {
    var id: String { label }
    var label: String
    var date: Date
    var price: Double
    var color: Color
}

struct DetailView: View {
    @EnvironmentObject var store: Store
    var symbol: String
    var name: String
    var plan: (Double, Double, Double, Double)?     // entry, stop, t1, t2
    var subtitle: String = ""

    @State private var hist: [HistPoint] = []
    @State private var fpts: [FcPoint] = []
    @State private var fc: Quant.Forecast?
    @State private var bars: Bars?
    @State private var error: String?
    @State private var reveal = 0
    @State private var showForecast = false
    @State private var selected: Date?
    @State private var claude: [String: Any]?
    @State private var claudeRunning = false
    @State private var claudeStatus = ""
    @State private var claudeTask: Task<Void, Never>?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                header
                chartCard
                outlookCard
                claudeCard
            }
            .padding(.horizontal, 14)
            .padding(.bottom, 24)
        }
        .background(T.bg)
        .navigationTitle(symbol)
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .onDisappear { claudeTask?.cancel() }
    }

    // MARK: Sections

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text(bars?.name ?? name).font(.system(size: 15, weight: .semibold)).foregroundStyle(T.fg)
                if !subtitle.isEmpty { Text(subtitle).font(.system(size: 12)).foregroundStyle(T.faint) }
            }
            Spacer()
            if let b = bars, b.count > 1 {
                let chg = (b.price / b.c[b.count - 2] - 1) * 100
                VStack(alignment: .trailing) {
                    Text(Fmt.usd(b.price)).font(.system(size: 20, weight: .bold, design: .monospaced))
                    Text(Fmt.pct(chg)).font(.system(size: 12, design: .monospaced)).foregroundStyle(T.pnl(chg))
                }
            }
        }
    }

    private var yDomain: ClosedRange<Double> {
        var ys = hist.suffix(260).map { $0.close }
        if showForecast, let f = fc { ys += [f.p10.min() ?? 0, f.p90.max() ?? 0] }
        if let p = plan { ys += [p.1, p.3] }
        for m in markers { ys.append(m.price) }
        let lo = ys.min() ?? 0, hi = ys.max() ?? 1
        let pad = max((hi - lo) * 0.08, 0.5)
        return (lo - pad)...(hi + pad)
    }

    private var markers: [Marker] {
        guard let c = claude, let last = hist.last else { return [] }
        let hd = (c["horizon_days"] as? NSNumber)?.intValue ?? 30
        let ts = Market.futureTradingDays(after: last.ts, count: max(1, hd)).last ?? last.ts
        var out: [Marker] = []
        for (k, col) in [("bear", T.red), ("base", T.tertiary), ("bull", T.green)] {
            if let v = c[k] as? [String: Any], let p = (v["price"] as? NSNumber)?.doubleValue, p > 0 {
                out.append(Marker(label: k, date: Date(timeIntervalSince1970: ts), price: p, color: col))
            }
        }
        return out
    }

    private var chartCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let e = error {
                Text(e).foregroundStyle(T.red).font(.system(size: 13))
            } else if hist.isEmpty {
                ProgressView().frame(maxWidth: .infinity, minHeight: 280)
            } else {
                priceChart.frame(height: 300)
                Text("Touch and drag for dates and prices. ━ price · ┅ 50-day · ▒ model range (10–90% / 25–75%) · ● Claude")
                    .font(.system(size: 10)).foregroundStyle(T.faint)
            }
        }
        .card()
    }

    private var priceChart: some View {
        let dom = yDomain
        let shown = Array(hist.suffix(260).prefix(reveal))
        return Chart {
            ForEach(shown) { p in
                AreaMark(x: .value("Date", p.date), yStart: .value("Low", dom.lowerBound), yEnd: .value("Close", p.close),
                         series: .value("Series", "area"))
                    .foregroundStyle(T.primary.opacity(0.12))
                LineMark(x: .value("Date", p.date), y: .value("Close", p.close), series: .value("Series", "price"))
                    .foregroundStyle(T.primary)
                    .lineStyle(StrokeStyle(lineWidth: 2))
            }
            ForEach(shown.filter { $0.sma50.isFinite }) { p in
                LineMark(x: .value("Date", p.date), y: .value("SMA50", p.sma50), series: .value("Series", "sma50"))
                    .foregroundStyle(T.tertiary.opacity(0.6))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
            }
            if showForecast {
                ForEach(fpts) { f in
                    AreaMark(x: .value("Date", f.date), yStart: .value("P10", f.p10), yEnd: .value("P90", f.p90),
                             series: .value("Series", "band90"))
                        .foregroundStyle(T.tertiary.opacity(0.16))
                    AreaMark(x: .value("Date", f.date), yStart: .value("P25", f.p25), yEnd: .value("P75", f.p75),
                             series: .value("Series", "band50"))
                        .foregroundStyle(T.tertiary.opacity(0.28))
                    LineMark(x: .value("Date", f.date), y: .value("Median", f.p50), series: .value("Series", "median"))
                        .foregroundStyle(T.tertiary)
                        .lineStyle(StrokeStyle(lineWidth: 2, dash: [5, 4]))
                }
            }
            if let p = plan {
                planRule(p.1, "Stop", T.red)
                planRule(p.0, "Entry", T.muted)
                planRule(p.2, "T1", T.green)
                planRule(p.3, "T2", T.primary)
            }
            ForEach(markers) { m in
                PointMark(x: .value("Date", m.date), y: .value("Claude", m.price))
                    .foregroundStyle(m.color)
                    .symbolSize(110)
                    .annotation(position: .trailing) {
                        Text("\(m.label) \(Fmt.num(m.price))").font(.system(size: 10, weight: .semibold)).foregroundStyle(m.color)
                    }
            }
            if let sel = selected, let tip = tooltip(for: sel) {
                RuleMark(x: .value("Selected", tip.date))
                    .foregroundStyle(T.muted.opacity(0.5))
                    .annotation(position: .top, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                        Tooltip(lines: tip.lines)
                    }
            }
        }
        .chartXSelection(value: $selected)
        .chartYScale(domain: dom)
        .chartXAxis { AxisMarks(values: .automatic(desiredCount: 5)) }
    }

    private func planRule(_ v: Double, _ name: String, _ col: Color) -> some ChartContent {
        RuleMark(y: .value(name, v))
            .foregroundStyle(col.opacity(0.7))
            .lineStyle(StrokeStyle(lineWidth: 1, dash: [5, 4]))
            .annotation(position: .top, alignment: .leading) {
                Text("\(name) \(Fmt.num(v))").font(.system(size: 9, weight: .semibold)).foregroundStyle(col)
            }
    }

    private func tooltip(for d: Date) -> (date: Date, lines: [String])? {
        let t = d.timeIntervalSince1970
        if showForecast, let last = hist.last, t > last.ts,
           let f = fpts.min(by: { abs($0.ts - t) < abs($1.ts - t) }) {
            return (f.date, [Fmt.date(f.ts) + " · +\(f.day) trading days (model)",
                             "Median \(Fmt.num(f.p50))",
                             "25–75% \(Fmt.num(f.p25)) – \(Fmt.num(f.p75))",
                             "10–90% \(Fmt.num(f.p10)) – \(Fmt.num(f.p90))"])
        }
        guard let i = hist.indices.min(by: { abs(hist[$0].ts - t) < abs(hist[$1].ts - t) }) else { return nil }
        let p = hist[i]
        let chg = i > 0 ? (p.close / hist[i - 1].close - 1) * 100 : 0
        var lines = [Fmt.date(p.ts), "Close \(Fmt.usd(p.close))  \(Fmt.pct(chg))"]
        if p.sma50.isFinite { lines.append("50-day avg \(Fmt.num(p.sma50))") }
        if let lastClose = hist.last?.close { lines.append("vs last close " + Fmt.pct((lastClose / p.close - 1) * 100)) }
        return (p.date, lines)
    }

    private var outlookCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionTitle(text: "Model outlook · 30 trading days", icon: "waveform.path.ecg")
            if let f = fc, let b = bars {
                let a = Quant.atr(b)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Median in 30 days: \(Fmt.usd(f.p50.last ?? 0))").bold()
                    Text("Likely range (25–75%): \(Fmt.usd(f.p25.last ?? 0)) – \(Fmt.usd(f.p75.last ?? 0))")
                    Text("Wide range (10–90%): \(Fmt.usd(f.p10.last ?? 0)) – \(Fmt.usd(f.p90.last ?? 0))")
                    Text(String(format: "Volatility %.0f%%/yr · ATR %.2f (%.1f%%) · RSI %.0f", f.volAnnual * 100, a, a / b.price * 100, Quant.rsi(b.c)))
                }
                .font(.system(size: 13)).foregroundStyle(T.fg)
                if let o = f.odds {
                    OddsBar(odds: o)
                    Text(String(format: "Plan odds (first touch): stop %.0f%% · T1 %.0f%% · T2 %.0f%% · neither %.0f%% · EV %+.2f%%",
                                o.stop * 100, o.t1 * 100, o.t2 * 100, o.none * 100, o.expPct))
                        .font(.system(size: 11)).foregroundStyle(T.muted)
                }
                Text("Random walk on recent volatility with almost no drift: a range of plausible outcomes, not a forecast.")
                    .font(.system(size: 10)).foregroundStyle(T.faint)
            } else {
                Text("Loading…").foregroundStyle(T.faint)
            }
        }
        .card()
    }

    private var claudeCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                SectionTitle(text: "Claude estimate", icon: "sparkles")
                if claudeRunning { ProgressView().scaleEffect(0.8) }
            }
            if let c = claude {
                ClaudeEstimateView(c: c)
            } else {
                Text(claudeStatus.isEmpty
                     ? (store.companion.configured ? "Ask Claude on your PC to analyse \(symbol) with your trading skills and estimate bear/base/bull outcomes."
                                                   : "Set up the PC companion in Settings to ask Claude.")
                     : claudeStatus)
                    .font(.system(size: 13)).foregroundStyle(T.faint)
            }
            HStack {
                Spacer()
                if claudeRunning {
                    Button { claudeTask?.cancel(); claudeRunning = false; claudeStatus = "Cancelled. Claude was stopped on your PC." } label: {
                        Label("Cancel", systemImage: "xmark")
                    }
                    .buttonStyle(PillButtonStyle(bg: T.red.opacity(0.2), fg: T.red, small: true))
                }
                Button { askClaude() } label: { Label("Ask Claude", systemImage: "sparkles") }
                    .buttonStyle(PillButtonStyle(bg: T.tertiary, fg: T.onTertiary, small: true))
                    .disabled(claudeRunning || !store.companion.configured || bars == nil)
                    .opacity(claudeRunning || !store.companion.configured ? 0.5 : 1)
            }
        }
        .card(T.tertiary)
    }

    // MARK: Data

    private func load() async {
        do {
            let b = try await Market.history(symbol, range: "1y", interval: "1d", ttl: 600)
            let sma = Quant.smaSeries(b.c, 50)
            var h: [HistPoint] = []
            for i in 0..<b.count {
                h.append(HistPoint(ts: b.ts[i], date: Date(timeIntervalSince1970: b.ts[i]), close: b.c[i], sma50: sma[i]))
            }
            let f = Quant.simulate(b.c, horizon: 30, paths: 2000, entry: plan?.0, stop: plan?.1, t1: plan?.2, t2: plan?.3)
            let future = Market.futureTradingDays(after: b.ts.last ?? Date().timeIntervalSince1970, count: f.horizon)
            var fp: [FcPoint] = []
            let tsAll = [b.ts.last ?? 0] + future
            for d in 0...f.horizon where d < tsAll.count {
                fp.append(FcPoint(ts: tsAll[d], date: Date(timeIntervalSince1970: tsAll[d]), day: d,
                                  p10: f.p10[d], p25: f.p25[d], p50: f.p50[d], p75: f.p75[d], p90: f.p90[d]))
            }
            bars = b
            fc = f
            fpts = fp
            hist = h
            // Draw-in: sweep the price line left to right, then fade the forecast in.
            let n = min(260, h.count)
            let steps = 30
            for s in 1...steps {
                try? await Task.sleep(nanoseconds: 25_000_000)
                withAnimation(.linear(duration: 0.025)) { reveal = n * s / steps }
            }
            withAnimation(.easeOut(duration: 0.5)) { showForecast = true }
        } catch {
            self.error = "Couldn't load \(symbol): \(error.localizedDescription)"
        }
    }

    private func askClaude() {
        guard let b = bars else { return }
        let p = plan ?? (b.price, b.price * 0.95, b.price * 1.05, b.price * 1.1)
        let body: [String: Any] = ["symbol": symbol, "name": b.name, "price": b.price,
                                   "entry": p.0, "stop": p.1, "t1": p.2, "t2": p.3]
        let comp = store.companion
        claudeRunning = true
        claude = nil
        claudeStatus = "Claude is researching \(symbol) on your PC… (usually 1–4 minutes)"
        claudeTask = Task { @MainActor in
            do {
                let id = try await comp.startEstimate(body)
                let job = try await comp.wait(id) { el in
                    Task { @MainActor in
                        claudeStatus = String(format: "Claude is researching \(symbol)… %dm %02ds", Int(el) / 60, Int(el) % 60)
                    }
                }
                claudeRunning = false
                if job.status == "done", let r = job.result {
                    withAnimation(.spring()) { claude = r }
                    if store.s.notifyScans {
                        Notifier.shared.send("Claude estimate ready · \(symbol)",
                                             "\((r["verdict"] as? String ?? "").uppercased()) · \(r["summary"] as? String ?? "")")
                    }
                } else {
                    claudeStatus = "Claude job \(job.status): \(job.error ?? "")"
                }
                await store.checkCompanion()
            } catch is CancellationError {
                claudeRunning = false
            } catch {
                claudeRunning = false
                claudeStatus = "Claude error: \(error.localizedDescription)"
            }
        }
    }
}

struct ClaudeEstimateView: View {
    var c: [String: Any]

    func row(_ k: String, _ col: Color) -> some View {
        let v = c[k] as? [String: Any] ?? [:]
        let price = (v["price"] as? NSNumber)?.doubleValue ?? 0
        let prob = (v["prob"] as? NSNumber)?.doubleValue ?? 0
        return HStack {
            Text(k.capitalized).bold().foregroundStyle(col).frame(width: 50, alignment: .leading)
            Text(Fmt.usd(price)).font(.system(size: 13, design: .monospaced))
            Spacer()
            Text(String(format: "%.0f%%", prob * 100)).font(.system(size: 13, design: .monospaced)).foregroundStyle(col)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("\((c["verdict"] as? String ?? "").uppercased()) · confidence \(c["confidence"] as? String ?? "?") · \((c["horizon_days"] as? NSNumber)?.intValue ?? 0) trading days")
                .font(.system(size: 12, weight: .bold)).foregroundStyle(T.primary)
            Text(c["summary"] as? String ?? "").font(.system(size: 13)).foregroundStyle(T.fg)
            row("bull", T.green)
            row("base", T.tertiary)
            row("bear", T.red)
            if let ev = c["evidence"] as? [String], !ev.isEmpty {
                Text("Evidence").font(.system(size: 12, weight: .bold)).foregroundStyle(T.muted)
                ForEach(ev, id: \.self) { Text("• \($0)").font(.system(size: 12)).foregroundStyle(T.muted) }
            }
            if let rk = c["key_risks"] as? [String], !rk.isEmpty {
                Text("Key risks").font(.system(size: 12, weight: .bold)).foregroundStyle(T.muted)
                ForEach(rk, id: \.self) { Text("• \($0)").font(.system(size: 12)).foregroundStyle(T.muted) }
            }
            Text("Earnings: \(c["earnings"] as? String ?? "unknown")").font(.system(size: 12)).foregroundStyle(T.faint)
            if let pc = c["plan_changes"] as? String { Text("Plan changes: \(pc)").font(.system(size: 12)).foregroundStyle(T.faint) }
            if let un = c["unverified"] as? [String], !un.isEmpty {
                Text("Couldn't verify: \(un.joined(separator: ", "))").font(.system(size: 12)).foregroundStyle(T.primary)
            }
            Text("Estimates, not guarantees. Price data as of \(c["price_as_of"] as? String ?? "?").")
                .font(.system(size: 10)).foregroundStyle(T.faint)
        }
    }
}
