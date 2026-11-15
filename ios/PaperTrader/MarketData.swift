import Foundation

enum MarketError: Error, LocalizedError {
    case http(Int)
    case parse(String)
    var errorDescription: String? {
        switch self {
        case .http(let c): return "HTTP \(c)"
        case .parse(let s): return "Couldn't read data: \(s)"
        }
    }
}

actor BarsCache {
    static let shared = BarsCache()
    private var store: [String: (Date, Bars)] = [:]
    func get(_ k: String, ttl: TimeInterval) -> Bars? {
        if let e = store[k], Date().timeIntervalSince(e.0) < ttl { return e.1 }
        return nil
    }
    func set(_ k: String, _ b: Bars) { store[k] = (Date(), b) }
}

enum Market {
    static let ny = TimeZone(identifier: "America/New_York")!

    /// Yahoo throttles full browser user agents from apps; a bare one works.
    static let yahoo: URLSession = {
        let c = URLSessionConfiguration.default
        c.httpAdditionalHeaders = ["User-Agent": "Mozilla/5.0"]
        c.timeoutIntervalForRequest = 20
        return URLSession(configuration: c)
    }()

    static let web: URLSession = {
        let c = URLSessionConfiguration.default
        c.httpAdditionalHeaders = ["User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"]
        c.timeoutIntervalForRequest = 20
        return URLSession(configuration: c)
    }()

    static func history(_ symbol: String, range: String = "1y", interval: String = "1d",
                        ttl: TimeInterval = 300) async throws -> Bars {
        let key = "\(symbol)|\(range)|\(interval)"
        if let b = await BarsCache.shared.get(key, ttl: ttl) { return b }
        var lastCode = 0
        for attempt in 0..<4 {
            try Task.checkCancellation()
            let host = attempt % 2 == 0 ? "query1" : "query2"
            let enc = symbol.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? symbol
            guard let url = URL(string: "https://\(host).finance.yahoo.com/v8/finance/chart/\(enc)?range=\(range)&interval=\(interval)&includePrePost=false") else {
                throw MarketError.parse("bad symbol")
            }
            let (data, resp) = try await yahoo.data(from: url)
            lastCode = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if lastCode == 429 {
                try await Task.sleep(nanoseconds: UInt64(800_000_000 * (attempt + 1)))
                continue
            }
            guard lastCode == 200 else { throw MarketError.http(lastCode) }
            let b = try parseChart(symbol, data)
            await BarsCache.shared.set(key, b)
            return b
        }
        throw MarketError.http(lastCode)
    }

    private static func nums(_ any: Any?) -> [Double] {
        guard let arr = any as? [Any] else { return [] }
        return arr.map { ($0 as? NSNumber)?.doubleValue ?? .nan }
    }

    static func parseChart(_ symbol: String, _ data: Data) throws -> Bars {
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let chart = root["chart"] as? [String: Any],
              let results = chart["result"] as? [[String: Any]], let res = results.first else {
            throw MarketError.parse(symbol)
        }
        let meta = res["meta"] as? [String: Any] ?? [:]
        let ts = nums(res["timestamp"])
        let ind = (res["indicators"] as? [String: Any])?["quote"] as? [[String: Any]]
        let q = ind?.first ?? [:]
        let o = nums(q["open"]), h = nums(q["high"]), l = nums(q["low"]), c = nums(q["close"]), v = nums(q["volume"])
        var b = Bars(symbol: symbol, ts: [], o: [], h: [], l: [], c: [], v: [], price: .nan,
                     name: (meta["longName"] as? String) ?? (meta["shortName"] as? String) ?? symbol)
        let n = min(ts.count, c.count, o.count, h.count, l.count)
        for i in 0..<n where c[i].isFinite && o[i].isFinite && h[i].isFinite && l[i].isFinite {
            b.ts.append(ts[i]); b.o.append(o[i]); b.h.append(h[i]); b.l.append(l[i]); b.c.append(c[i])
            b.v.append(i < v.count && v[i].isFinite ? v[i] : 0)
        }
        b.price = (meta["regularMarketPrice"] as? NSNumber)?.doubleValue ?? (b.c.last ?? .nan)
        return b
    }

    /// USD per 1 AUD (e.g. 0.6774). AUD price = USD price / audusd.
    static func audusd() async -> Double {
        if let b = try? await history("AUDUSD=X", range: "5d", interval: "1d", ttl: 120), b.price > 0.3, b.price < 1.5 {
            return b.price
        }
        return 0.65
    }

    // MARK: FinViz public quote page

    static let fvKeys: Set<String> = ["P/E", "Forward P/E", "EPS this Y", "EPS Q/Q", "ROE", "Earnings", "Beta",
                                      "Debt/Eq", "Target Price", "Recom", "Sector"]

    static func finviz(_ symbol: String) async -> [String: String] {
        guard let url = URL(string: "https://finviz.com/quote.ashx?t=\(symbol)&p=d"),
              let res = try? await web.data(from: url),
              let html = String(data: res.0, encoding: .utf8) else { return [:] }
        guard let re = try? NSRegularExpression(pattern: "<td[^>]*>(.*?)</td>", options: [.dotMatchesLineSeparators, .caseInsensitive]),
              let tag = try? NSRegularExpression(pattern: "<[^>]+>") else { return [:] }
        let ns = html as NSString
        var cells: [String] = []
        for m in re.matches(in: html, range: NSRange(location: 0, length: ns.length)) {
            let inner = ns.substring(with: m.range(at: 1))
            let text = tag.stringByReplacingMatches(in: inner, range: NSRange(location: 0, length: (inner as NSString).length), withTemplate: "")
            cells.append(text.replacingOccurrences(of: "&amp;", with: "&").trimmingCharacters(in: .whitespacesAndNewlines))
        }
        var out: [String: String] = [:]
        if cells.count > 1 {
            for i in 0..<(cells.count - 1) where fvKeys.contains(cells[i]) && out[cells[i]] == nil {
                out[cells[i]] = cells[i + 1]
            }
        }
        return out
    }

    static func pnum(_ s: String?) -> Double {
        guard var t = s?.split(separator: " ").first.map(String.init) else { return .nan }
        t = t.replacingOccurrences(of: ",", with: "").replacingOccurrences(of: "%", with: "")
        var mult = 1.0
        if let last = t.last, let m = ["B": 1e9, "M": 1e6, "K": 1e3, "T": 1e12][String(last)] {
            mult = m
            t.removeLast()
        }
        return (Double(t) ?? .nan) * mult
    }

    /// FinViz shows "Oct 13 BMO" without a year: choose the closest plausible date.
    static func nextEarnings(_ s: String?, today: Date = Date()) -> Date? {
        guard let s, s != "-" else { return nil }
        let parts = s.split(separator: " ")
        guard parts.count >= 2 else { return nil }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = ny
        f.dateFormat = "MMM d yyyy"
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = ny
        let y = cal.component(.year, from: today)
        let cands = [y - 1, y, y + 1].compactMap { f.date(from: "\(parts[0]) \(parts[1]) \($0)") }
        return cands.min { abs($0.timeIntervalSince(today)) < abs($1.timeIntervalSince(today)) }
    }

    // MARK: Market clock

    static let holidays: Set<String> = ["2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15",
                                        "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
                                        "2027-11-25", "2027-12-24"]

    static func isTradingDay(_ d: Date) -> Bool {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = ny
        let wd = cal.component(.weekday, from: d)
        let f = DateFormatter()
        f.timeZone = ny
        f.dateFormat = "yyyy-MM-dd"
        return wd != 1 && wd != 7 && !holidays.contains(f.string(from: d))
    }

    static func status(now: Date = Date()) -> (open: Bool, text: String) {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = ny
        let open = cal.date(bySettingHour: 9, minute: 30, second: 0, of: now)!
        let close = cal.date(bySettingHour: 16, minute: 0, second: 0, of: now)!
        if isTradingDay(now) && now >= open && now < close {
            let m = Int(close.timeIntervalSince(now) / 60)
            return (true, "Open · closes in \(m / 60)h \(String(format: "%02d", m % 60))m")
        }
        var next = (isTradingDay(now) && now < open) ? open : cal.date(byAdding: .day, value: 1, to: open)!
        while !isTradingDay(next) { next = cal.date(byAdding: .day, value: 1, to: next)! }
        let m = Int(next.timeIntervalSince(now) / 60)
        let h = m / 60
        let when = h >= 24 ? "\(h / 24)d \(h % 24)h" : "\(h)h \(String(format: "%02d", m % 60))m"
        return (false, "Closed · opens in \(when)")
    }

    static func futureTradingDays(after ts: Double, count: Int) -> [Double] {
        var out: [Double] = []
        var d = Date(timeIntervalSince1970: ts)
        while out.count < count {
            d = d.addingTimeInterval(86400)
            if isTradingDay(d) { out.append(d.timeIntervalSince1970) }
        }
        return out
    }

    // MARK: Universe

    static let universe: [String: String] = [
        "Technology": "AAPL MSFT NVDA AVGO ORCL CRM ADBE AMD CSCO ACN IBM INTU TXN QCOM NOW AMAT ADP PAYX CTSH ZM MU ANET",
        "Communication Services": "GOOGL META NFLX TMUS VZ T DIS CMCSA FOXA EA TTWO APP",
        "Healthcare": "UNH LLY JNJ ABBV MRK TMO ABT DHR AMGN ISRG GILD HCA ELV CI GEHC DGX COR CVS",
        "Consumer Defensive": "PG KO PEP COST WMT PM MO MDLZ CL KMB GIS DG DLTR KR TGT",
        "Financial": "JPM BAC WFC GS MS C SCHW BLK SPGI CME ICE TRV CB PGR AIG MET AXP V MA BK",
        "Industrials": "GE CAT HON UNP RTX LMT DE ETN UPS GD NOC CSX ITW WM EMR",
        "Consumer Cyclical": "AMZN TSLA HD MCD NKE LOW SBUX BKNG TJX CMG RCL YUM GM",
        "Energy": "XOM CVX COP EOG SLB PSX MPC OXY KMI WMB",
        "Utilities": "NEE SO DUK AEP D EXC XEL SRE CEG",
        "Real Estate": "PLD AMT EQIX PSA O SPG WELL",
        "Basic Materials": "LIN SHW APD ECL NEM FCX DOW",
    ]

    static let sectorOf: [String: String] = {
        var m: [String: String] = [:]
        for (sec, syms) in universe { for s in syms.split(separator: " ") { m[String(s)] = sec } }
        return m
    }()
}
