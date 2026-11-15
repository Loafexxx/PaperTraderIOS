import Foundation

/// Client for the Paper Trader companion server running on your PC (Claude features).
struct Companion {
    var baseURL: String
    var token: String

    struct Failure: Error, LocalizedError {
        var message: String
        var errorDescription: String? { message }
    }

    struct Job {
        var status: String          // running | done | error | cancelled
        var elapsed: Double
        var result: [String: Any]?
        var error: String?
    }

    var configured: Bool { !baseURL.trimmingCharacters(in: .whitespaces).isEmpty && !token.isEmpty }

    private func url(_ path: String) throws -> URL {
        var b = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        if !b.hasPrefix("http://") && !b.hasPrefix("https://") { b = "http://" + b }
        while b.hasSuffix("/") { b.removeLast() }
        guard let u = URL(string: b + path) else { throw Failure(message: "Bad PC address") }
        return u
    }

    private func request(_ path: String, method: String = "GET", body: [String: Any]? = nil,
                         timeout: TimeInterval = 20) async throws -> Any {
        var req = URLRequest(url: try url(path), timeoutInterval: timeout)
        req.httpMethod = method
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let obj = (try? JSONSerialization.jsonObject(with: data)) ?? [:]
        if code != 200 {
            let msg = (obj as? [String: Any])?["error"] as? String ?? "HTTP \(code)"
            throw Failure(message: code == 401 ? "Wrong token" : msg)
        }
        return obj
    }

    func status() async throws -> (claude: Bool, busy: Bool) {
        let o = try await request("/api/status", timeout: 6) as? [String: Any] ?? [:]
        return ((o["claude"] as? Bool) ?? false, (o["busy"] as? Bool) ?? false)
    }

    func usage() async throws -> [UsageRow] {
        let arr = try await request("/api/usage", timeout: 60) as? [[String: Any]] ?? []
        return arr.map {
            UsageRow(key: $0["key"] as? String ?? "", label: $0["label"] as? String ?? "",
                     pct: ($0["pct"] as? NSNumber)?.doubleValue ?? 0, resets: $0["resets"] as? String ?? "")
        }
    }

    func posture() async throws -> Posture {
        let o = try await request("/api/posture", timeout: 200) as? [String: Any] ?? [:]
        var rank: [String: Int] = [:]
        if let secs = o["sectors"] as? [String: [String: Any]] {
            for (k, v) in secs { rank[k] = (v["rank"] as? NSNumber)?.intValue ?? 99 }
        }
        let ranked = rank.sorted { $0.value < $1.value }.map { $0.key }
        return Posture(label: o["label"] as? String ?? "Unknown",
                       ceilingPct: (o["ceiling_pct"] as? NSNumber)?.intValue ?? 0,
                       detail: o["detail"] as? String ?? "", strong: Array(ranked.prefix(3)),
                       weak: Array(ranked.suffix(3)), sectorRank: rank, source: "pc")
    }

    func startScan(_ body: [String: Any]) async throws -> String {
        let o = try await request("/api/scan", method: "POST", body: body) as? [String: Any] ?? [:]
        guard let id = o["job"] as? String else { throw Failure(message: "No job id") }
        return id
    }

    func startEstimate(_ body: [String: Any]) async throws -> String {
        let o = try await request("/api/estimate", method: "POST", body: body) as? [String: Any] ?? [:]
        guard let id = o["job"] as? String else { throw Failure(message: "No job id") }
        return id
    }

    func job(_ id: String) async throws -> Job {
        let o = try await request("/api/job/\(id)") as? [String: Any] ?? [:]
        return Job(status: o["status"] as? String ?? "error", elapsed: (o["elapsed"] as? NSNumber)?.doubleValue ?? 0,
                   result: o["result"] as? [String: Any], error: o["error"] as? String)
    }

    func cancel(_ id: String) async {
        _ = try? await request("/api/job/\(id)/cancel", method: "POST", body: [:], timeout: 6)
    }

    /// Poll a job until it finishes (or the Swift task is cancelled, which also cancels it on the PC).
    func wait(_ id: String, onTick: @escaping (Double) -> Void) async throws -> Job {
        while true {
            if Task.isCancelled {
                await cancel(id)
                throw CancellationError()
            }
            let j = try await job(id)
            onTick(j.elapsed)
            if j.status != "running" { return j }
            do {
                try await Task.sleep(nanoseconds: 3_000_000_000)
            } catch {
                await cancel(id)
                throw CancellationError()
            }
        }
    }

    // MARK: JSON -> models

    static func d(_ x: Any?) -> Double { (x as? NSNumber)?.doubleValue ?? .nan }

    static func pick(_ o: [String: Any]) -> Pick? {
        guard let sym = o["symbol"] as? String else { return nil }
        let odds = (o["odds"] as? [String: Any]).map {
            Odds(stop: d($0["stop"]), t1: d($0["t1"]), t2: d($0["t2"]), none: d($0["none"]), expPct: d($0["exp_pct"]))
        }
        let co = (o["claude_odds"] as? [String: Any]).map {
            ClaudeOdds(bear: d($0["bear"]), base: d($0["base"]), bull: d($0["bull"]),
                       confidence: $0["confidence"] as? String ?? "?")
        }
        return Pick(symbol: sym, name: o["name"] as? String ?? sym, sector: o["sector"] as? String ?? "",
                    price: d(o["price"]), prev: d(o["prev"]), entry: d(o["entry"]), stop: d(o["stop"]),
                    t1: d(o["t1"]), t2: d(o["t2"]), earnings: o["earnings"] as? String,
                    bullets: (o["bullets"] as? [String] ?? []).filter { !$0.isEmpty },
                    thesis: o["thesis"] as? String ?? "", source: "claude", odds: odds, claudeOdds: co)
    }
}
