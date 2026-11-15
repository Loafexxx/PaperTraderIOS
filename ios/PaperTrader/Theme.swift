import SwiftUI

extension Color {
    init(hex: UInt32, alpha: Double = 1) {
        self.init(.sRGB,
                  red: Double((hex >> 16) & 0xff) / 255,
                  green: Double((hex >> 8) & 0xff) / 255,
                  blue: Double(hex & 0xff) / 255,
                  opacity: alpha)
    }
}

/// Serpantinum / Matugen palette (same colours as the desktop app).
enum T {
    static let bg = Color(hex: 0x202018)
    static let surface = Color(hex: 0x14140c)
    static let card = Color(hex: 0x2a2a21)
    static let cardHi = Color(hex: 0x33332a)
    static let outline = Color(hex: 0x47473b)
    static let fg = Color(hex: 0xe5e3d6)
    static let muted = Color(hex: 0xc9c7b6)
    static let faint = Color(hex: 0x929182)
    static let primary = Color(hex: 0xc7cc79)
    static let onPrimary = Color(hex: 0x303300)
    static let primaryContainer = Color(hex: 0x464a02)
    static let onPrimaryContainer = Color(hex: 0xe3e892)
    static let tertiary = Color(hex: 0xa3d0c0)
    static let onTertiary = Color(hex: 0x06261d)
    static let green = Color(hex: 0x8fd27f)
    static let onGreen = Color(hex: 0x0d2a12)
    static let red = Color(hex: 0xff8a80)
    static let onRed = Color(hex: 0x3b0905)
    static let radius: CGFloat = 16

    static func pnl(_ v: Double) -> Color { v >= 0 ? green : red }
}

enum Fmt {
    private static let money: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.minimumFractionDigits = 2
        f.maximumFractionDigits = 2
        f.locale = Locale(identifier: "en_AU")
        return f
    }()

    static func num(_ v: Double, _ digits: Int = 2) -> String {
        guard v.isFinite else { return "–" }
        return String(format: "%.\(digits)f", v)
    }

    static func aud(_ v: Double, sign: Bool = false) -> String {
        guard v.isFinite else { return "–" }
        let s = money.string(from: NSNumber(value: abs(v))) ?? String(format: "%.2f", abs(v))
        let pre = sign ? (v >= 0 ? "+" : "−") : (v < 0 ? "−" : "")
        return "\(pre)A$\(s)"
    }

    static func usd(_ v: Double) -> String {
        guard v.isFinite else { return "–" }
        let s = money.string(from: NSNumber(value: v)) ?? String(format: "%.2f", v)
        return "US$\(s)"
    }

    static func pct(_ v: Double, sign: Bool = true) -> String {
        guard v.isFinite else { return "–" }
        return sign ? String(format: "%+.2f%%", v) : String(format: "%.2f%%", v)
    }

    static func shares(_ v: Double) -> String {
        var s = String(format: "%.4f", v)
        while s.contains(".") && (s.hasSuffix("0") || s.hasSuffix(".")) { s.removeLast() }
        return s
    }

    static func date(_ ts: Double, _ format: String = "EEE d MMM yyyy", ny: Bool = true) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_AU")
        f.dateFormat = format
        if ny { f.timeZone = TimeZone(identifier: "America/New_York") }
        return f.string(from: Date(timeIntervalSince1970: ts))
    }
}
