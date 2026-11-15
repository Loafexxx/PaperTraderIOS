import Foundation

// MARK: - Persisted ledger

struct AppSettings: Codable {
    var riskPct = 1.0
    var maxPositionPct = 20.0
    var maxOpenRiskPct = 5.0
    var maxPerSector = 2
    var orderExpiryDays = 5
    var maxHoldDays = 42
    var scanSize = 8
    var refreshSeconds = 120
    var scanBudget = 0.0
    var notifyScans = true
    var notifyTrades = true
    var serverURL = ""
    var serverToken = ""

    init() {}

    enum CodingKeys: String, CodingKey {
        case riskPct, maxPositionPct, maxOpenRiskPct, maxPerSector, orderExpiryDays, maxHoldDays, scanSize
        case refreshSeconds, scanBudget, notifyScans, notifyTrades, serverURL, serverToken
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let d = AppSettings.defaults
        riskPct = try c.decodeIfPresent(Double.self, forKey: .riskPct) ?? d.riskPct
        maxPositionPct = try c.decodeIfPresent(Double.self, forKey: .maxPositionPct) ?? d.maxPositionPct
        maxOpenRiskPct = try c.decodeIfPresent(Double.self, forKey: .maxOpenRiskPct) ?? d.maxOpenRiskPct
        maxPerSector = try c.decodeIfPresent(Int.self, forKey: .maxPerSector) ?? d.maxPerSector
        orderExpiryDays = try c.decodeIfPresent(Int.self, forKey: .orderExpiryDays) ?? d.orderExpiryDays
        maxHoldDays = try c.decodeIfPresent(Int.self, forKey: .maxHoldDays) ?? d.maxHoldDays
        scanSize = try c.decodeIfPresent(Int.self, forKey: .scanSize) ?? d.scanSize
        refreshSeconds = try c.decodeIfPresent(Int.self, forKey: .refreshSeconds) ?? d.refreshSeconds
        scanBudget = try c.decodeIfPresent(Double.self, forKey: .scanBudget) ?? d.scanBudget
        notifyScans = try c.decodeIfPresent(Bool.self, forKey: .notifyScans) ?? d.notifyScans
        notifyTrades = try c.decodeIfPresent(Bool.self, forKey: .notifyTrades) ?? d.notifyTrades
        serverURL = try c.decodeIfPresent(String.self, forKey: .serverURL) ?? d.serverURL
        serverToken = try c.decodeIfPresent(String.self, forKey: .serverToken) ?? d.serverToken
    }

    static let defaults = AppSettings()
}

struct Order: Codable, Identifiable {
    var id: String
    var symbol: String
    var name: String
    var sector: String
    var created: Double
    var limit: Double
    var stop: Double
    var t1: Double
    var t2: Double
    var shares: Double
    var status: String          // pending | filled | cancelled | expired
    var expires: Double
    var source: String
    var thesis: String
    var lastBarTs: Double = 0
    var fillPrice: Double?
}

struct Position: Codable, Identifiable {
    var id: String
    var symbol: String
    var name: String
    var sector: String
    var shares: Double
    var origShares: Double
    var entry: Double
    var entryFx: Double
    var costAud: Double
    var opened: Double
    var stop: Double
    var origStop: Double
    var t1: Double
    var t2: Double
    var halfTaken: Bool = false
    var realizedAud: Double = 0
    var thesis: String
    var mae: Double
    var mfe: Double
    var lastBarTs: Double
}

struct ClosedTrade: Codable, Identifiable {
    var id: String
    var symbol: String
    var name: String
    var sector: String
    var opened: Double
    var closed: Double
    var shares: Double
    var entry: Double
    var exit: Double
    var entryFx: Double
    var exitFx: Double
    var pnlAud: Double
    var pnlPct: Double
    var rMultiple: Double
    var reason: String
    var maePct: Double
    var mfePct: Double
    var stop: Double
    var t1: Double
    var t2: Double
}

struct LedgerEvent: Codable, Identifiable {
    var id = UUID().uuidString
    var ts: Double
    var text: String
    var kind: String            // info | order | fill | win | loss | error
}

struct EquityPoint: Codable, Identifiable {
    var id: Double { ts }
    var ts: Double
    var value: Double
}

struct LedgerData: Codable {
    var startingCash: Double = 10000
    var cash: Double = 10000
    var created: Double = Date().timeIntervalSince1970
    var orders: [Order] = []
    var positions: [Position] = []
    var history: [ClosedTrade] = []
    var events: [LedgerEvent] = []
    var equity: [EquityPoint] = []
    var settings = AppSettings()

    init(start: Double = 10000, settings: AppSettings = AppSettings()) {
        startingCash = start
        cash = start
        equity = [EquityPoint(ts: Date().timeIntervalSince1970, value: start)]
        self.settings = settings
    }

    enum CodingKeys: String, CodingKey { case startingCash, cash, created, orders, positions, history, events, equity, settings }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        startingCash = try c.decodeIfPresent(Double.self, forKey: .startingCash) ?? 10000
        cash = try c.decodeIfPresent(Double.self, forKey: .cash) ?? startingCash
        created = try c.decodeIfPresent(Double.self, forKey: .created) ?? Date().timeIntervalSince1970
        orders = try c.decodeIfPresent([Order].self, forKey: .orders) ?? []
        positions = try c.decodeIfPresent([Position].self, forKey: .positions) ?? []
        history = try c.decodeIfPresent([ClosedTrade].self, forKey: .history) ?? []
        events = try c.decodeIfPresent([LedgerEvent].self, forKey: .events) ?? []
        equity = try c.decodeIfPresent([EquityPoint].self, forKey: .equity) ?? []
        settings = try c.decodeIfPresent(AppSettings.self, forKey: .settings) ?? AppSettings()
    }
}

// MARK: - Scan results (not persisted)

struct Odds {
    var stop: Double
    var t1: Double
    var t2: Double
    var none: Double
    var expPct: Double
}

struct ClaudeOdds {
    var bear: Double
    var base: Double
    var bull: Double
    var confidence: String
}

struct Pick: Identifiable {
    var id: String { symbol }
    var symbol: String
    var name: String
    var sector: String
    var price: Double
    var prev: Double
    var entry: Double
    var stop: Double
    var t1: Double
    var t2: Double
    var earnings: String?
    var bullets: [String]
    var thesis: String
    var source: String          // scanner | claude
    var odds: Odds?
    var claudeOdds: ClaudeOdds?
    var allocShares: Double = 0
    var allocAud: Double = 0
    var allocWhy: String = ""
    var planned = false         // a budget plan was computed for this scan

    var risk: Double { entry - stop }
    var rr1: Double { (t1 - entry) / max(risk, 1e-9) }
    var rr2: Double { (t2 - entry) / max(risk, 1e-9) }
    var stopPct: Double { (stop - entry) / entry * 100 }
    var t1Pct: Double { (t1 - entry) / entry * 100 }
    var t2Pct: Double { (t2 - entry) / entry * 100 }
}

struct Allocation: Identifiable {
    var id: String { symbol }
    var symbol: String
    var aud: Double
    var why: String
}

struct Advice {
    var action: String          // dont_invest | wait_for_pullback | invest_partial | invest
    var headline: String
    var reasoning: String
    var confidence: String
    var whatWouldChange: String
    var source: String          // builtin | claude
    var notes: [String] = []
}

struct Posture {
    var label: String
    var ceilingPct: Int
    var detail: String
    var strong: [String] = []
    var weak: [String] = []
    var sectorRank: [String: Int] = [:]
    var source: String          // phone | pc
}

struct UsageRow: Identifiable {
    var id: String { key }
    var key: String
    var label: String
    var pct: Double
    var resets: String
}
