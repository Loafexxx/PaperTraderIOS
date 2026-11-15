import Foundation

struct Bars {
    var symbol: String
    var ts: [Double]
    var o: [Double]
    var h: [Double]
    var l: [Double]
    var c: [Double]
    var v: [Double]
    var price: Double
    var name: String

    var count: Int { c.count }
}

enum Quant {
    static func sma(_ x: [Double], _ n: Int) -> Double {
        guard x.count >= n, n > 0 else { return .nan }
        return x.suffix(n).reduce(0, +) / Double(n)
    }

    static func smaSeries(_ x: [Double], _ n: Int) -> [Double] {
        var out = [Double](repeating: .nan, count: x.count)
        guard x.count >= n, n > 0 else { return out }
        var sum = x.prefix(n).reduce(0, +)
        out[n - 1] = sum / Double(n)
        if x.count > n {
            for i in n..<x.count {
                sum += x[i] - x[i - n]
                out[i] = sum / Double(n)
            }
        }
        return out
    }

    static func atr(_ b: Bars, _ n: Int = 14) -> Double {
        guard b.count > n else { return .nan }
        var trs: [Double] = []
        for i in 1..<b.count {
            let tr = max(b.h[i] - b.l[i], max(abs(b.h[i] - b.c[i - 1]), abs(b.l[i] - b.c[i - 1])))
            trs.append(tr)
        }
        return trs.suffix(n).reduce(0, +) / Double(n)
    }

    static func rsi(_ c: [Double], _ n: Int = 14) -> Double {
        guard c.count > n else { return .nan }
        let x = Array(c.suffix(n * 5))
        var up: [Double] = [], dn: [Double] = []
        for i in 1..<x.count {
            let d = x[i] - x[i - 1]
            up.append(max(d, 0))
            dn.append(max(-d, 0))
        }
        guard up.count >= n else { return .nan }
        var au = up.prefix(n).reduce(0, +) / Double(n)
        var ad = dn.prefix(n).reduce(0, +) / Double(n)
        if up.count > n {
            for i in n..<up.count {
                au = (au * Double(n - 1) + up[i]) / Double(n)
                ad = (ad * Double(n - 1) + dn[i]) / Double(n)
            }
        }
        if ad == 0 { return 100 }
        return 100 - 100 / (1 + au / ad)
    }

    // MARK: Monte Carlo

    struct Forecast {
        var p10: [Double]
        var p25: [Double]
        var p50: [Double]
        var p75: [Double]
        var p90: [Double]
        var volAnnual: Double
        var pStop: Double = .nan
        var pT1: Double = .nan
        var pT2: Double = .nan
        var pNone: Double = .nan
        var expPct: Double = .nan

        var horizon: Int { p50.count - 1 }
        var odds: Odds? {
            pStop.isFinite ? Odds(stop: pStop, t1: pT1, t2: pT2, none: pNone, expPct: expPct) : nil
        }
    }

    struct SplitMix64: RandomNumberGenerator {
        var state: UInt64
        mutating func next() -> UInt64 {
            state &+= 0x9E37_79B9_7F4A_7C15
            var z = state
            z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
            z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
            return z ^ (z >> 31)
        }
    }

    static func gauss(_ g: inout SplitMix64) -> Double {
        let u1 = max(Double.random(in: 0..<1, using: &g), 1e-12)
        let u2 = Double.random(in: 0..<1, using: &g)
        return sqrt(-2 * log(u1)) * cos(2 * Double.pi * u2)
    }

    /// Random walk on recent volatility with a heavily shrunk drift: a range of plausible outcomes, not a prediction.
    static func simulate(_ closes: [Double], horizon: Int = 30, paths n: Int = 1500,
                         entry: Double? = nil, stop: Double? = nil, t1: Double? = nil, t2: Double? = nil) -> Forecast {
        let recent = Array(closes.suffix(127))
        var rets: [Double] = []
        if recent.count > 1 {
            for i in 1..<recent.count where recent[i - 1] > 0 { rets.append(log(recent[i] / recent[i - 1])) }
        }
        let mean = rets.isEmpty ? 0 : rets.reduce(0, +) / Double(rets.count)
        var varSum = 0.0
        for r in rets { varSum += (r - mean) * (r - mean) }
        var sigma = rets.count > 5 ? sqrt(varSum / Double(rets.count - 1)) : 0.02
        sigma = max(sigma, 0.006)
        let mu = min(max(0.2 * mean, -0.0006), 0.0006)
        let drift = mu - 0.5 * sigma * sigma
        let start = closes.last ?? 0
        var g = SplitMix64(state: 7)

        func shock() -> Double {
            let z = gauss(&g)
            return Double.random(in: 0..<1, using: &g) < 0.05 ? z * 1.8 : z
        }

        // Cone
        var cols = [[Double]](repeating: [], count: horizon + 1)
        for d in 0...horizon { cols[d].reserveCapacity(n) }
        for _ in 0..<n {
            var p = start
            cols[0].append(p)
            for d in 1...horizon {
                p *= exp(drift + sigma * shock())
                cols[d].append(p)
            }
        }
        func q(_ arr: [Double], _ f: Double) -> Double { arr[Int(Double(arr.count - 1) * f)] }
        var p10: [Double] = [], p25: [Double] = [], p50: [Double] = [], p75: [Double] = [], p90: [Double] = []
        for d in 0...horizon {
            let s = cols[d].sorted()
            p10.append(q(s, 0.10)); p25.append(q(s, 0.25)); p50.append(q(s, 0.50))
            p75.append(q(s, 0.75)); p90.append(q(s, 0.90))
        }
        var fc = Forecast(p10: p10, p25: p25, p50: p50, p75: p75, p90: p90, volAnnual: sigma * sqrt(252))

        // Trade odds (first touch wins; after T1 half is sold and the stop moves to breakeven)
        if let e = entry, let s = stop, let a = t1, e > s, a > e {
            let b = max(t2 ?? a, a)
            var nStop = 0, nT1 = 0, nT2 = 0, nNone = 0
            var retSum = 0.0
            let rStop = (s - e) / e
            let rT1 = (a - e) / e
            let rT2 = 0.5 * rT1 + 0.5 * (b - e) / e
            for _ in 0..<n {
                var p = e
                var hitT1 = false
                var outcome = 0       // 0 none, 1 stop, 2 t1, 3 t2
                for _ in 1...horizon {
                    p *= exp(drift + sigma * shock())
                    if !hitT1 {
                        if p <= s { outcome = 1; break }
                        if p >= a { hitT1 = true }
                    }
                    if hitT1 {
                        if p >= b { outcome = 3; break }
                        if p <= e { outcome = 2; break }
                    }
                }
                if outcome == 0 && hitT1 { outcome = 2 }
                switch outcome {
                case 1: nStop += 1; retSum += rStop
                case 2: nT1 += 1; retSum += 0.5 * rT1
                case 3: nT2 += 1; retSum += rT2
                default: nNone += 1; retSum += (p - e) / e
                }
            }
            let N = Double(n)
            fc.pStop = Double(nStop) / N
            fc.pT1 = Double(nT1) / N
            fc.pT2 = Double(nT2) / N
            fc.pNone = Double(nNone) / N
            fc.expPct = retSum / N * 100
        }
        return fc
    }

    // MARK: Trade plan from chart levels

    struct Plan { var entry: Double; var stop: Double; var t1: Double; var t2: Double }

    static func plan(price px: Double, sma20: Double, atr a: Double, lo10: Double, hi20: Double, hi1y: Double) -> Plan {
        let r2: (Double) -> Double = { ($0 * 100).rounded() / 100 }
        let entry = r2(min(px, max(sma20, px - 0.75 * a)))
        var stop = r2(min(lo10, entry - 1.5 * a) - 0.15 * a)
        stop = r2(max(stop, entry - 3.5 * a))
        let risk = entry - stop
        let t1 = r2(max(hi20, entry + 1.6 * risk))
        let t2 = r2(max(hi1y > t1 * 1.01 ? hi1y : 0, entry + 3.0 * risk))
        return Plan(entry: entry, stop: stop, t1: t1, t2: t2)
    }
}
