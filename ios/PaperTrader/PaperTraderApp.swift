import SwiftUI

@main
struct PaperTraderApp: App {
    @StateObject private var store = Store()
    @Environment(\.scenePhase) private var phase

    init() {
        Notifier.shared.setUp()
        let nav = UINavigationBarAppearance()
        nav.configureWithOpaqueBackground()
        nav.backgroundColor = UIColor(T.bg)
        nav.titleTextAttributes = [.foregroundColor: UIColor(T.fg)]
        nav.largeTitleTextAttributes = [.foregroundColor: UIColor(T.fg)]
        UINavigationBar.appearance().standardAppearance = nav
        UINavigationBar.appearance().scrollEdgeAppearance = nav
        let tab = UITabBarAppearance()
        tab.configureWithOpaqueBackground()
        tab.backgroundColor = UIColor(T.surface)
        UITabBar.appearance().standardAppearance = tab
        UITabBar.appearance().scrollEdgeAppearance = tab
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .preferredColorScheme(.dark)
                .tint(T.primary)
        }
        .onChange(of: phase) { _, p in
            if p == .active { Task { await store.refresh(); await store.checkCompanion() } }
        }
    }
}

struct RootView: View {
    @EnvironmentObject var store: Store
    @State private var tab = 0
    let timer = Timer.publish(every: 60, on: .main, in: .common).autoconnect()
    @State private var tick = 0

    var body: some View {
        ZStack(alignment: .top) {
            TabView(selection: $tab) {
                NavigationStack { DashboardView() }
                    .tabItem { Label("Dashboard", systemImage: "square.grid.2x2.fill") }.tag(0)
                NavigationStack { ScannerView() }
                    .tabItem { Label("Scanner", systemImage: "magnifyingglass") }.tag(1)
                NavigationStack { TradesView() }
                    .tabItem { Label("Trades", systemImage: "chart.line.uptrend.xyaxis") }.tag(2)
                NavigationStack { HistoryView() }
                    .tabItem { Label("History", systemImage: "clock.arrow.circlepath") }.tag(3)
                NavigationStack { SettingsView() }
                    .tabItem { Label("Settings", systemImage: "gearshape.fill") }.tag(4)
            }
            if let t = store.toast {
                ToastView(text: t)
                    .padding(.top, 6)
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .onTapGesture { withAnimation { store.toast = nil } }
                    .zIndex(10)
            }
        }
        .background(T.bg.ignoresSafeArea())
        .task {
            await store.refresh()
            await store.checkCompanion()
            await store.loadPosture()
        }
        .onReceive(timer) { _ in
            tick += 1
            let every = max(1, store.s.refreshSeconds / 60)
            if tick % every == 0 { Task { await store.refresh() } }
            if tick % 5 == 0 { Task { await store.checkCompanion() } }
        }
    }
}
