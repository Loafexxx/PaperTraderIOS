import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var store: Store
    @State private var url = ""
    @State private var token = ""
    @State private var testing = false
    @State private var resetAmount = "10000"
    @State private var resetSettings = false
    @State private var confirmReset = false

    var body: some View {
        Form {
            Section("Risk & trading rules") {
                stepper("Risk per trade", value: binding(\.riskPct), range: 0.25...5, step: 0.25, fmt: "%.2f%%")
                stepper("Max position size", value: binding(\.maxPositionPct), range: 5...100, step: 5, fmt: "%.0f%%")
                stepper("Max total open risk", value: binding(\.maxOpenRiskPct), range: 1...20, step: 0.5, fmt: "%.1f%%")
                Stepper("Max trades per sector: \(store.s.maxPerSector)", value: intBinding(\.maxPerSector), in: 1...10)
                Stepper("Orders expire after \(store.s.orderExpiryDays) trading days", value: intBinding(\.orderExpiryDays), in: 1...60)
                Stepper("Time-exit warning after \(store.s.maxHoldDays) days", value: intBinding(\.maxHoldDays), in: 5...365)
                Stepper("Ideas per scan: \(store.s.scanSize)", value: intBinding(\.scanSize), in: 3...20)
                Stepper("Refresh every \(store.s.refreshSeconds / 60) min", value: intBinding(\.refreshSeconds), in: 60...1800, step: 60)
            }
            .listRowBackground(T.card)

            Section("Notifications") {
                Toggle("When a scan or Claude estimate finishes", isOn: boolBinding(\.notifyScans))
                Toggle("When an order fills or a trade closes", isOn: boolBinding(\.notifyTrades))
                Button("Send test notification") { Notifier.shared.send("Paper Trader", "Notifications are working.") }
            }
            .listRowBackground(T.card)

            Section {
                TextField("PC address, e.g. 192.168.1.20:8787", text: $url)
                    .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                SecureField("Token (from the desktop app)", text: $token)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                HStack {
                    Button {
                        var st = store.s
                        st.serverURL = url.trimmingCharacters(in: .whitespacesAndNewlines)
                        st.serverToken = token.trimmingCharacters(in: .whitespacesAndNewlines)
                        store.s = st
                        testing = true
                        Task {
                            await store.checkCompanion()
                            if store.companionOK { await store.loadPosture(force: true) }
                            testing = false
                        }
                    } label: { Label("Save & test", systemImage: "antenna.radiowaves.left.and.right") }
                    Spacer()
                    if testing { ProgressView() }
                }
                HStack(spacing: 6) {
                    Circle().fill(store.companionOK ? T.green : T.faint).frame(width: 8, height: 8)
                    Text(store.companionState).font(.system(size: 12)).foregroundStyle(T.muted)
                }
                if !store.usage.isEmpty { UsageBars(rows: store.usage) }
            } header: {
                Text("PC companion (Claude)")
            } footer: {
                Text("On your PC: Paper Trader → Settings → iPhone companion → tick 'Run the companion server', then copy the address and token here. Works on the same Wi-Fi, or anywhere with Tailscale (100.x address).")
            }
            .listRowBackground(T.card)

            Section {
                TextField("Starting balance (A$)", text: $resetAmount).keyboardType(.numberPad)
                HStack {
                    ForEach(["1000", "10000", "50000", "100000"], id: \.self) { v in
                        Button("\((Int(v) ?? 0) / 1000)k") { resetAmount = v }
                            .buttonStyle(PillButtonStyle(bg: T.cardHi, fg: T.fg, small: true))
                    }
                }
                Toggle("Also reset risk rules to defaults", isOn: $resetSettings)
                Button("Reset everything", role: .destructive) { confirmReset = true }
            } header: {
                Text("Reset account")
            } footer: {
                Text("Erases every position, pending order, closed trade, activity entry and the equity curve on this phone.")
            }
            .listRowBackground(T.card)

            Section("About") {
                Text("Paper money only. Not financial advice. Prices from Yahoo Finance and FinViz; fills are simulated from 15-minute bars.")
                    .font(.system(size: 12)).foregroundStyle(T.faint)
            }
            .listRowBackground(T.card)
        }
        .scrollContentBackground(.hidden)
        .background(T.bg)
        .navigationTitle("Settings")
        .onAppear {
            url = store.s.serverURL
            token = store.s.serverToken
            resetAmount = String(Int(store.d.startingCash))
        }
        .confirmationDialog("Reset the paper account to \(Fmt.aud(Double(resetAmount) ?? 10000))? This can't be undone.",
                            isPresented: $confirmReset, titleVisibility: .visible) {
            Button("Reset everything", role: .destructive) {
                let amt = max(100, Double(resetAmount) ?? 10000)
                withAnimation { store.reset(to: amt, resetSettings: resetSettings) }
                resetSettings = false
                store.showToast("Paper account reset to \(Fmt.aud(amt))")
            }
        }
    }

    // MARK: Bindings into the persisted settings

    private func binding(_ kp: WritableKeyPath<AppSettings, Double>) -> Binding<Double> {
        Binding(get: { store.s[keyPath: kp] }, set: { v in var st = store.s; st[keyPath: kp] = v; store.s = st })
    }

    private func intBinding(_ kp: WritableKeyPath<AppSettings, Int>) -> Binding<Int> {
        Binding(get: { store.s[keyPath: kp] }, set: { v in var st = store.s; st[keyPath: kp] = v; store.s = st })
    }

    private func boolBinding(_ kp: WritableKeyPath<AppSettings, Bool>) -> Binding<Bool> {
        Binding(get: { store.s[keyPath: kp] }, set: { v in var st = store.s; st[keyPath: kp] = v; store.s = st })
    }

    private func stepper(_ title: String, value: Binding<Double>, range: ClosedRange<Double>, step: Double, fmt: String) -> some View {
        Stepper(value: value, in: range, step: step) {
            HStack {
                Text(title)
                Spacer()
                Text(String(format: fmt, value.wrappedValue)).font(.system(.body, design: .monospaced)).foregroundStyle(T.primary)
            }
        }
    }
}
