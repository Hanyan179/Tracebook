import AppIntents
import SwiftUI
import UniformTypeIdentifiers

enum PhoneLibraryRoute: Hashable { case workspace }

struct SetupView: View {
    @StateObject private var recorder = CaptureRecorder.shared
    @StateObject private var preferences = CapturePreferences()
    @StateObject private var watch = WatchAvailability.shared
    @StateObject private var workspace = NodeWorkspaceStore(playback: CaptureRecorder.shared.playback)
    @StateObject private var sync = NodeSyncController.shared
    @StateObject private var folder = FolderSyncController.shared
    @AppStorage("appearance") private var appearance = AppAppearance.system.rawValue
    @AppStorage("app.language") private var language = AppLanguage.system.rawValue
    @AppStorage("tour.seen") private var tourSeen = false
    @AppStorage("sync.route") private var route = SyncRoute.current.rawValue
    @Environment(\.scenePhase) private var scenePhase
    @State private var tab = 0
    @State private var recordingPresented = false
    @State private var tour = false
    var body: some View {
        TabView(selection: $tab) {
            NavigationStack {
                RecordingLibraryScreen()
                    .libraryDestinations(store: workspace)
                    .navigationDestination(for: PhoneLibraryRoute.self) { _ in WorkspaceHomeView(store: workspace) }
            }
                .tabItem { Label("记录", systemImage: "square.stack") }.tag(0)
            NavigationStack {
              Group {
                if route == SyncRoute.direct.rawValue && workspace.configured {
                    ConversationView(workspace: workspace)
                        .navigationTitle(L("对话")).navigationBarTitleDisplayMode(.inline)
                        .toolbar { ToolbarItem(placement: .topBarTrailing) { NavigationLink("笔记") { NotesLibrary(workspace: workspace) } } }
                } else {
                    ContentUnavailableView {
                        Label(L(route == SyncRoute.folder.rawValue ? "在电脑查询资料" : "连接电脑后查询资料"),
                              systemImage: "laptopcomputer")
                    } description: {
                        Text(L(route == SyncRoute.folder.rawValue
                            ? "电脑接收资料后，可在电脑查询原文和来源。手机直连电脑资料库后也可查询。"
                            : "录音目前保存在手机。设置电脑连接后，可以在这里查询电脑资料库中的原文和来源。"))
                    } actions: {
                        Button("查看同步方式") { tab = 1 }.buttonStyle(.bordered)
                    }
                }
              }.navigationTitle(L("对话")).navigationBarTitleDisplayMode(.inline)
            }.tabItem { Label("对话", systemImage: "bubble.left.and.bubble.right") }.tag(3)
            NavigationStack { PhoneSyncScreen().libraryDestinations(store: workspace) }
                .tabItem { Label("同步", systemImage: "arrow.triangle.2.circlepath") }.tag(1)
            NavigationStack { PhoneProfileScreen().libraryDestinations(store: workspace) }
                .tabItem { Label("我的", systemImage: "person.crop.circle") }.tag(2)
        }
        .tint(PhoneStyle.ink)
        .environmentObject(recorder)
        .environmentObject(preferences)
        .environmentObject(watch)
        .environmentObject(recorder.playback)
        .environmentObject(workspace)
        .environmentObject(sync)
        .environmentObject(folder)
        .preferredColorScheme(AppAppearance(rawValue: appearance)?.scheme)
        .environment(\.locale, (AppLanguage(rawValue: language) ?? .system).locale)
        .fullScreenCover(isPresented: Binding(get: { !preferences.onboarded },
                                              set: { _ in }), onDismiss: {
            if preferences.onboarded && !tourSeen { tour = true }
        }) {
            OnboardingScreen()
                .environmentObject(recorder).environmentObject(preferences).environmentObject(watch)
                .environmentObject(recorder.playback)
                .appLanguage()
        }
        .sheet(isPresented: $recordingPresented) {
            PhoneRecordingScreen().environmentObject(recorder).appLanguage()
        }
        .sheet(isPresented: $tour, onDismiss: { tourSeen = true }) { ProductTour() }
        .onChange(of: scenePhase, initial: true) { _, phase in
            recorder.foreground = phase == .active
            sync.foreground = phase == .active
            workspace.active = phase == .active && SyncRoute.current == .direct
            folder.active = phase == .active
            if phase == .active {
                recorder.reload(); watch.refresh(); sync.refresh()
                Task { await folder.refresh() }
            }
        }
        .onChange(of: recorder.active?.id) { _, id in
            if id != nil, preferences.onboarded { tab = 0; recordingPresented = true }
            if id == nil { recordingPresented = false }
        }
        .alert("暂时无法完成", isPresented: Binding(get: { recorder.problem != nil && preferences.onboarded && !recordingPresented },
                                                 set: { if !$0 { recorder.problem = nil } })) {
            Button("知道了") { recorder.problem = nil }
        } message: { Text(L(recorder.problem ?? "")) }
        .onAppear { workspace.canPlay = { CaptureRecorder.shared.active == nil } }
        .onChange(of: language) { _, _ in
            watch.refresh()
            Task {
                if route == SyncRoute.direct.rawValue { await workspace.refresh() }
                await folder.refresh()
            }
        }
    }
}
