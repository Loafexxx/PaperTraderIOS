import Foundation

struct Tech {
    var symbol: String
    var name: String
    var bars: Bars
    var price: Double
    var prev: Double
    var sma20: Double
    var sma50: Double
    var sma200: Double
    var atr: Double
    var rsi: Double
    var avgVol: Double
    var mom3: Double
    var ext50: Double
    var lo10: Double
    var hi20: Double
    var hi1y: Double
    var score: Double
    var passes: Bool
}

/// Built-in scanner: the same rules as the desktop app (trend -> fundamentals -> earnings -> sector -> plan -> odds).
enum Scanner {
    static func tech(_ sym: String) async -> Tech? {
        guard let b = try? await Market.history(sym, range: "1y", interval: "1d", ttl: 900), b.count >= 210 else { return nil }
        let c = b.c
        let px = c[c.count - 1]
        let s20 = Quant.sma(c, 20), s50 = Quant.sma(c, 50), s200 = Quant.sma(c, 200)
        let a = Quant.atr(b), r = Quant.rsi(c)
        let avgVol = b.v.suffix(50).reduce(0, +) / 50
        let mom3 = px / c[c.count - 63] - 1
        let ext = px / s50 - 1
        let hi1y = b.h.max() ?? px
        let passes = px > 5 && avgVol > 1e6 && px > s50 && px > s200 && s50 > s200 * 0.98 && r >= 38 && r <= 72
            && ext < 0.12 && mom3 > -0.02
        var score = min(mom3, 0.3) * 100
        score += (px / s200 - 1) * 40
        score -= max(0, ext - 0.06) * 200
        score -= abs(r - 58) * 0.4
        score += (1 - (hi1y - px) / hi1y) * 15
        return Tech(symbol: sym, name: b.name, bars: b, price: px, prev: c[c.count - 2], sma20: s20, sma50: s50,
                    sma200: s200, atr: a, rsi: r, avgVol: avgVol, mom3: mom3, ext50: ext,
                    lo10: b.l.suffix(10).min() ?? px, hi20: b.h.suffix(20).max() ?? px, hi1y: hi1y,
                    score: score, passes: passes)
    }

    static func fetchUniverse(progress: @escaping (Double, String) -> Void) async throws -> [Tech] {
        let syms = Array(Market.sectorOf.keys).sorted()
        var out: [Tech] = []
        var done = 0
        for start in stride(from: 0, to: syms.count, by: 10) {
            try Task.checkCancellation()
            let chunk = Array(syms[start..<min(start + 10, syms.count)])
            let got: [Tech] = await withTaskGroup(of: Tech?.self) { g in
                for s in chunk { g.addTask { await tech(s) } }
                var acc: [Tech] = []
                for await t in g { if let t { acc.append(t) } }
                return acc
            }
            out += got
            done += chunk.count
            let passing = out.filter { $0.passes }.count
            progress(0.02 + 0.48 * Double(done) / Double(syms.count), "Trend filter: \(done)/\(syms.count) · \(passing) passing")
        }
        return out
    }

    /// Phone-side market posture: share of ~150 large caps above their 200/50-day averages, plus sector strength.
    static func posture(from techs: [Tech]) -> Posture {
        guard !techs.isEmpty else { return Posture(label: "Unknown", ceilingPct: 0, detail: "No data", source: "phone") }
        let n = Double(techs.count)
        let a200 = Double(techs.filter { $0.price > $0.sma200 }.count) / n * 100
        let a50 = Double(techs.filter { $0.price > $0.sma50 }.count) / n * 100
        let score = 0.6 * a200 + 0.4 * a50
        let label: String
        let ceiling: Int
        if score >= 65 {
            label = "Risk-on"; ceiling = min(100, Int(70 + (score - 65) * 1.3))
        } else if score >= 50 {
            label = "Selective"; ceiling = Int(50 + (score - 50) * 1.3)
        } else if score >= 35 {
            label = "Cautious"; ceiling = Int(30 + (score - 35) * 1.3)
        } else {
            label = "Defensive"; ceiling = Int(10 + max(0, score - 20) * 1.3)
        }
        var bySector: [String: (Int, Int)] = [:]
        for t in techs {
            let sec = Market.sectorOf[t.symbol] ?? "Other"
            var e = bySector[sec] ?? (0, 0)
            e.0 += (t.price > t.sma50 && t.price > t.sma200) ? 1 : 0
            e.1 += 1
            bySector[sec] = e
        }
        let ranked = bySector.map { ($0.key, Double($0.value.0) / Double(max($0.value.1, 1))) }.sorted { $0.1 > $1.1 }
        var rank: [String: Int] = [:]
        for (i, r) in ranked.enumerated() { rank[r.0] = i + 1 }
        return Posture(label: label, ceilingPct: ceiling,
                       detail: String(format: "%.0f%% of large caps above 200-day, %.0f%% above 50-day", a200, a50),
                       strong: ranked.prefix(3).map { $0.0 }, weak: ranked.suffix(3).map { $0.0 },
                       sectorRank: rank, source: "phone")
    }

    static func scan(topN: Int, maxPerSector: Int, exclude: Set<String>, pcPosture: Posture?,
                     progress: @escaping (Double, String) -> Void) async throws -> (Posture, [Pick]) {
        progress(0.02, "Checking trends on \(Market.sectorOf.count) liquid US stocks…")
        let techs = try await fetchUniverse(progress: progress)
        let post = pcPosture ?? Scanner.posture(from: techs)
        let passing = techs.filter { $0.passes && !exclude.contains($0.symbol) }.sorted { $0.score > $1.score }
        let shortlist = Array(passing.prefix(24))
        progress(0.52, "\(passing.count) in healthy uptrends. Reading fundamentals for the top \(shortlist.count)…")

        var funds: [String: [String: String]] = [:]
        var done = 0
        for start in stride(from: 0, to: shortlist.count, by: 4) {
            try Task.checkCancellation()
            let chunk = Array(shortlist[start..<min(start + 4, shortlist.count)])
            let got: [(String, [String: String])] = await withTaskGroup(of: (String, [String: String]).self) { g in
                for t in chunk {
                    g.addTask {
                        let f = await Market.finviz(t.symbol)
                        return (t.symbol, f)
                    }
                }
                var acc: [(String, [String: String])] = []
                for await r in g { acc.append(r) }
                return acc
            }
            for (s, f) in got { funds[s] = f }
            done += chunk.count
            progress(0.52 + 0.38 * Double(done) / Double(max(shortlist.count, 1)), "Fundamentals: \(done)/\(shortlist.count)")
        }

        let today = Date()
        var picks: [(Pick, Double)] = []
        for t in shortlist {
            try Task.checkCancellation()
            let fv = funds[t.symbol] ?? [:]
            let pe = Market.pnum(fv["P/E"]), fpe = Market.pnum(fv["Forward P/E"])
            let epsY = Market.pnum(fv["EPS this Y"]), epsQ = Market.pnum(fv["EPS Q/Q"]), roe = Market.pnum(fv["ROE"])
            let earn = Market.nextEarnings(fv["Earnings"], today: today)
            if !fv.isEmpty {
                let valuationOK = (fpe > 0 && fpe < 25) || (fpe.isNaN && pe > 0 && pe < 30)
                if !valuationOK || !(pe < 35 || pe.isNaN) || !(epsY > 0 && epsQ > 0) { continue }
            }
            if let e = earn {
                let days = e.timeIntervalSince(today) / 86400
                if days >= -1 && days <= 14 { continue }
            }
            let sector = Market.sectorOf[t.symbol] ?? (fv["Sector"] ?? "")
            let pl = Quant.plan(price: t.price, sma20: t.sma20, atr: t.atr, lo10: t.lo10, hi20: t.hi20, hi1y: t.hi1y)
            let fc = Quant.simulate(t.bars.c, horizon: 30, paths: 1200, entry: pl.entry, stop: pl.stop, t1: pl.t1, t2: pl.t2)
            var q = 0.0
            if epsY.isFinite { q += min(epsY, 40) * 0.3 }
            if roe.isFinite { q += min(roe, 40) * 0.2 }
            if fpe.isFinite { q += max(0, 25 - fpe) * 0.5 }
            var sScore = 0.0
            if let r = post.sectorRank[sector] { sScore = Double(12 - r) * 1.5 }
            let score = t.score + q + sScore + (fc.expPct.isFinite ? fc.expPct * 4 : 0)
            var bullets = [
                String(format: "Uptrend: %+.1f%% vs 200-day, %+.1f%% over 3 months", (t.price / t.sma200 - 1) * 100, t.mom3 * 100),
                String(format: "RSI %.0f, %+.1f%% from 50-day average", t.rsi, t.ext50 * 100),
            ]
            if !fv.isEmpty {
                bullets.append("Fwd P/E \(fv["Forward P/E"] ?? "-"), EPS this yr \(fv["EPS this Y"] ?? "-"), ROE \(fv["ROE"] ?? "-")")
            }
            if let r = post.sectorRank[sector] { bullets.append("Sector rank #\(r) (\(sector))") }
            let df = DateFormatter()
            df.dateFormat = "yyyy-MM-dd"
            let p = Pick(symbol: t.symbol, name: t.name, sector: sector, price: t.price, prev: t.prev,
                         entry: pl.entry, stop: pl.stop, t1: pl.t1, t2: pl.t2,
                         earnings: earn.map { df.string(from: $0) }, bullets: bullets,
                         thesis: bullets.joined(separator: "; "), source: "scanner", odds: fc.odds, claudeOdds: nil)
            picks.append((p, score))
        }
        picks.sort { $0.1 > $1.1 }
        var out: [Pick] = []
        var per: [String: Int] = [:]
        for (p, _) in picks {
            if per[p.sector, default: 0] >= maxPerSector || p.rr1 < 1.2 { continue }
            per[p.sector, default: 0] += 1
            out.append(p)
            if out.count >= topN { break }
        }
        progress(1, "Done · \(out.count) ideas from \(passing.count) uptrending stocks")
        return (post, out)
    }
}
