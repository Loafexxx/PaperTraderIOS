import SwiftUI

struct CardModifier: ViewModifier {
    var tint: Color?
    func body(content: Content) -> some View {
        content
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: T.radius, style: .continuous)
                    .fill(tint.map { $0.opacity(0.10) } ?? T.card)
            )
            .overlay(
                RoundedRectangle(cornerRadius: T.radius, style: .continuous)
                    .stroke((tint ?? T.outline).opacity(tint == nil ? 0.5 : 0.45), lineWidth: 1)
            )
    }
}

extension View {
    func card(_ tint: Color? = nil) -> some View { modifier(CardModifier(tint: tint)) }

    /// Fade + rise when first shown (staggered with `delay`).
    func entrance(_ delay: Double = 0) -> some View { modifier(Entrance(delay: delay)) }
}

struct Entrance: ViewModifier {
    var delay: Double
    @State private var shown = false
    func body(content: Content) -> some View {
        content
            .opacity(shown ? 1 : 0)
            .offset(y: shown ? 0 : 14)
            .onAppear {
                withAnimation(.spring(response: 0.5, dampingFraction: 0.85).delay(delay)) { shown = true }
            }
    }
}

struct PillButtonStyle: ButtonStyle {
    var bg: Color
    var fg: Color
    var small = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: small ? 13 : 15, weight: .semibold))
            .padding(.horizontal, small ? 12 : 16)
            .padding(.vertical, small ? 7 : 10)
            .foregroundStyle(fg)
            .background(Capsule().fill(bg.opacity(configuration.isPressed ? 0.75 : 1)))
            .scaleEffect(configuration.isPressed ? 0.95 : 1)
            .animation(.spring(response: 0.25, dampingFraction: 0.7), value: configuration.isPressed)
    }
}

struct Chip: View {
    var text: String
    var bg: Color = T.cardHi
    var fg: Color = T.muted
    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .foregroundStyle(fg)
            .background(Capsule().fill(bg))
    }
}

struct KV: View {
    var title: String
    var value: String
    var color: Color = T.fg
    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).font(.system(size: 10, weight: .bold)).foregroundStyle(T.faint)
            Text(value).font(.system(size: 14, weight: .semibold, design: .monospaced)).foregroundStyle(color)
                .lineLimit(1).minimumScaleFactor(0.7)
        }
    }
}

struct StatTile: View {
    var title: String
    var value: Double
    var format: (Double) -> String
    var sub: String = ""
    var color: Color = T.fg
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title.uppercased()).font(.system(size: 10, weight: .bold)).foregroundStyle(T.faint)
            Text(format(value))
                .font(.system(size: 19, weight: .bold, design: .monospaced))
                .foregroundStyle(color)
                .contentTransition(.numericText(value: value))
                .animation(.spring(response: 0.6, dampingFraction: 0.8), value: value)
                .lineLimit(1).minimumScaleFactor(0.6)
            if !sub.isEmpty {
                Text(sub).font(.system(size: 11)).foregroundStyle(T.faint).lineLimit(1)
            }
        }
        .card()
    }
}

struct OddsBar: View {
    var odds: Odds
    @State private var grow: CGFloat = 0
    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width * grow
            HStack(spacing: 0) {
                Rectangle().fill(T.red).frame(width: w * CGFloat(max(0, odds.stop)))
                Rectangle().fill(T.faint).frame(width: w * CGFloat(max(0, odds.none)))
                Rectangle().fill(T.green).frame(width: w * CGFloat(max(0, odds.t1)))
                Rectangle().fill(T.primary).frame(width: w * CGFloat(max(0, odds.t2)))
                Spacer(minLength: 0)
            }
            .background(T.cardHi)
            .clipShape(Capsule())
        }
        .frame(height: 8)
        .onAppear { withAnimation(.easeOut(duration: 0.9).delay(0.15)) { grow = 1 } }
    }
}

/// Stop → entry → T1 → T2 track with an animated dot for the current price.
struct PriceTrack: View {
    var stop: Double
    var entry: Double
    var t1: Double
    var t2: Double
    var price: Double
    @State private var shown: Double = .nan

    private func x(_ v: Double, _ w: CGFloat) -> CGFloat {
        let lo = stop - (t2 - stop) * 0.04, hi = t2 + (t2 - stop) * 0.04
        return CGFloat((min(max(v, lo), hi) - lo) / max(hi - lo, 1e-9)) * w
    }

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let cur = shown.isFinite ? shown : entry
            ZStack(alignment: .topLeading) {
                Capsule().fill(T.cardHi).frame(height: 6).offset(y: 9)
                Capsule().fill(T.red.opacity(0.45)).frame(width: max(0, x(entry, w) - x(stop, w)), height: 6)
                    .offset(x: x(stop, w), y: 9)
                Capsule().fill(T.green.opacity(0.45)).frame(width: max(0, x(t2, w) - x(entry, w)), height: 6)
                    .offset(x: x(entry, w), y: 9)
                ForEach(Array([(stop, T.red), (entry, T.muted), (t1, T.green), (t2, T.primary)].enumerated()), id: \.offset) { _, item in
                    VStack(spacing: 2) {
                        Rectangle().fill(item.1).frame(width: 2, height: 12)
                        Text(Fmt.num(item.0)).font(.system(size: 9, design: .monospaced)).foregroundStyle(item.1)
                            .fixedSize()
                    }
                    .offset(x: x(item.0, w) - 1, y: 6)
                }
                Circle().fill((cur >= entry ? T.green : T.red).opacity(0.3)).frame(width: 18, height: 18)
                    .offset(x: x(cur, w) - 9, y: 3)
                Circle().fill(cur >= entry ? T.green : T.red).frame(width: 10, height: 10)
                    .offset(x: x(cur, w) - 5, y: 7)
            }
        }
        .frame(height: 38)
        .onAppear { withAnimation(.easeOut(duration: 0.9)) { shown = price } }
        .onChange(of: price) { _, v in withAnimation(.easeOut(duration: 0.9)) { shown = v } }
    }
}

struct SectionTitle: View {
    var text: String
    var icon: String = ""
    var body: some View {
        HStack(spacing: 8) {
            if !icon.isEmpty { Image(systemName: icon).foregroundStyle(T.primary) }
            Text(text).font(.system(size: 17, weight: .bold)).foregroundStyle(T.fg)
            Spacer()
        }
    }
}

struct ToastView: View {
    var text: String
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "bell.fill").foregroundStyle(T.primary)
            Text(text).font(.system(size: 13)).foregroundStyle(T.fg).lineLimit(3)
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
        .background(Capsule().fill(T.cardHi))
        .overlay(Capsule().stroke(T.outline, lineWidth: 1))
        .shadow(color: .black.opacity(0.4), radius: 12, y: 6)
        .padding(.horizontal, 16)
    }
}

struct UsageBars: View {
    var rows: [UsageRow]
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(rows.filter { $0.key == "session" || $0.key == "week" }) { r in
                VStack(alignment: .leading, spacing: 3) {
                    HStack {
                        Text(r.key == "session" ? "5-hour session" : "Weekly limit").font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(T.muted)
                        Spacer()
                        Text("\(Int(r.pct))%").font(.system(size: 12, weight: .bold, design: .monospaced))
                            .foregroundStyle(color(r.pct))
                    }
                    ProgressView(value: min(r.pct, 100), total: 100).tint(color(r.pct))
                        .animation(.easeOut(duration: 0.8), value: r.pct)
                    Text("resets \(r.resets)").font(.system(size: 10)).foregroundStyle(T.faint)
                }
            }
        }
    }

    func color(_ p: Double) -> Color { p < 60 ? T.green : (p < 85 ? T.primary : T.red) }
}
