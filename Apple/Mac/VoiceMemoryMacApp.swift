import AppKit
import SwiftUI
import UniformTypeIdentifiers

@main
struct VoiceMemoryMacApp: App {
    @NSApplicationDelegateAdaptor(MacAppDelegate.self) private var delegate
    @StateObject private var workspace = NodeWorkspaceStore(playback: AudioPlayback())
    var body: some Scene {
        WindowGroup { MacWorkspaceView(workspace: workspace).frame(minWidth: 900, minHeight: 650) }
            .defaultSize(width: 1280, height: 820)
        Settings { MacSettingsView(workspace: workspace) }
    }
}

final class MacAppDelegate: NSObject, NSApplicationDelegate {
    func applicationWillTerminate(_ notification: Notification) { LocalNodeController.shared.stop() }
}

private enum WorkspaceSection: String, CaseIterable, Identifiable {
    case overview = "对话", notes = "笔记", recordings = "原音", daily = "每日记录", memory = "长期记忆", sync = "设备与同步", sources = "资料来源"
    var id: String { rawValue }
    var title: String { self == .recordings ? "录音与文字" : rawValue }
    var symbol: String {
        switch self { case .overview: "bubble.left.and.bubble.right"; case .notes: "note.text"; case .recordings: "waveform"; case .daily: "calendar"; case .memory: "bookmark"; case .sync: "arrow.triangle.2.circlepath"; case .sources: "tray.and.arrow.down" }
    }
}

struct MacWorkspaceView: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @StateObject private var local = LocalNodeController.shared
    @StateObject private var sync = NodeSyncController.shared
    @StateObject private var folder = FolderSyncController.shared
    @State private var section: WorkspaceSection? = .recordings
    @State private var selectedNote: UUID?
    @State private var columnVisibility = NavigationSplitViewVisibility.all
    @State private var sidebarTransitionTask: Task<Void, Never>?
    @State private var pendingSidebarToggle = false
    @State private var navigationPath = NavigationPath()
    @AppStorage("appearance") private var appearance = AppAppearance.system.rawValue
    @AppStorage("app.language") private var language = AppLanguage.system.rawValue
    @AppStorage("node.useLocal") private var useLocal = true
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var tour = false
    @State private var importing = false
    @State private var importBusy = false
    @State private var importError: String?
    @Environment(\.openSettings) private var openSettings
    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            VStack(spacing: 0) {
                HStack(spacing: 9) {
                    BrandMark(size: 24).frame(width: 20)
                    Text("声记").font(.system(size: 14, weight: .semibold))
                    Spacer(minLength: 4)
                }.padding(.horizontal, 24).padding(.top, 10).padding(.bottom, 18)
                VStack(alignment: .leading, spacing: 16) {
                    sidebarGroup("资料", items: [.recordings, .notes, .sources])
                    sidebarGroup("整理与回顾", items: [.daily, .memory, .overview])
                    sidebarGroup("连接", items: [.sync])
                }.padding(.horizontal, 10)
                Spacer()
                VStack(alignment: .leading, spacing: 18) {
                    SettingsLink { Label("设置", systemImage: "gearshape") }.buttonStyle(.plain)
                    Button { tour = true } label: { Label("快速上手", systemImage: "play.rectangle") }.buttonStyle(.plain)
                    Divider()
                    Button {
                        Task {
                            if useLocal && !local.running { await local.start(workspace: workspace) }
                            else { await workspace.refresh() }
                        }
                    } label: {
                        Label(L(workspace.connected ? "资料库已就绪" : (local.starting || workspace.loading ? "正在准备资料库" : "资料库未连接")),
                              systemImage: workspace.connected ? "checkmark.circle" : "exclamationmark.circle")
                    }.buttonStyle(.plain).font(.caption).foregroundStyle(.secondary)
                        .disabled(local.starting || workspace.loading)
                }.padding(20)
            }.background(AppPalette.surface)
                .navigationSplitViewColumnWidth(min: 190, ideal: 210, max: 260)
                .toolbar(removing: .sidebarToggle)
                .toolbar { sidebarToolbar }
        } detail: {
            NavigationStack(path: $navigationPath) {
                Group {
                    switch section ?? .recordings {
                    case .overview: MacOverview(workspace: workspace, showNotes: { selectedNote = $0; section = .notes }, showMemory: { section = .memory }, showDaily: { section = .daily })
                    case .notes: NotesLibrary(workspace: workspace, initialSelection: selectedNote)
                    case .sync: syncPage
                    case .sources: LocalSourcesView(workspace: workspace)
                    case .recordings: MacLibraryBrowser(store: workspace, kind: .recording,
                        importAudio: { importing = true }, setupSync: { section = .sync })
                    case .daily: MacLibraryBrowser(store: workspace, kind: .daily)
                    case .memory: MacLibraryBrowser(store: workspace, kind: .memory)
                    }
                }.navigationTitle(L((section ?? .recordings).title))
                    .libraryDestinations(store: workspace)
                    .background(AppPalette.canvas)
                    .toolbar {
                        if section == .recordings {
                            ToolbarItem {
                                Button { importing = true } label: { Label(L(importBusy ? "正在导入" : "导入音频"), systemImage: "plus") }
                                    .disabled(importBusy).keyboardShortcut("i", modifiers: [.command, .shift])
                            }
                        }
                    }
            }.id((section?.rawValue ?? "") + language)
                .navigationSplitViewColumnWidth(min: 680, ideal: 1000)
                .toolbar(removing: .sidebarToggle)
        }.toolbar(removing: .sidebarToggle).tint(.primary)
            .preferredColorScheme(AppAppearance(rawValue: appearance)?.scheme)
            .environment(\.locale, (AppLanguage(rawValue: language) ?? .system).locale)
            .sheet(isPresented: $tour) { ProductTour() }
            .animation(reduceMotion ? nil : .easeInOut(duration: 0.16), value: section)
            .onChange(of: section) { _, _ in navigationPath = NavigationPath() }
            .onDisappear {
                sidebarTransitionTask?.cancel()
                sidebarTransitionTask = nil
                pendingSidebarToggle = false
            }
            .onChange(of: language) { _, _ in
                Task { await workspace.refresh(); await folder.refresh() }
            }
            .task {
                if useLocal {
                    await local.start(workspace: workspace)
                }
                else { await workspace.refresh() }
                sync.refresh(); await folder.refresh()
            }
            .fileImporter(isPresented: $importing, allowedContentTypes: [.audio]) { result in
                if case .success(let url) = result { importFile(url) }
                else if case .failure(let error) = result { importError = error.localizedDescription }
            }
            .alert("暂时无法导入", isPresented: Binding(get: { importError != nil }, set: { if !$0 { importError = nil } })) {
                Button("知道了") { importError = nil }
            } message: { Text(importError ?? "") }
            .onReceive(sync.$jobs.debounce(for: .milliseconds(600), scheduler: RunLoop.main)) { _ in
                Task { await workspace.refresh() }
            }
    }
    private func sidebarGroup(_ title: String, items: [WorkspaceSection]) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(L(title)).font(.caption.weight(.medium)).foregroundStyle(.secondary).padding(.horizontal, 14)
            ForEach(items) { item in
                Button { navigationPath = NavigationPath(); section = item } label: {
                    HStack(spacing: 8) {
                        Label(LocalizedStringKey(item.title), systemImage: item.symbol)
                        Spacer(minLength: 0)
                        if item == .memory, let pending = workspace.overview?.pendingMemoryCount, pending > 0 {
                            Text(pending, format: .number).font(.caption.monospacedDigit())
                                .padding(.horizontal, 6).padding(.vertical, 2).background(AppPalette.line, in: Capsule())
                                .accessibilityLabel(LF("%d 条待确认", pending))
                        }
                    }.font(.subheadline.weight(section == item ? .semibold : .regular))
                        .padding(.horizontal, 14).padding(.vertical, 9).foregroundStyle(.primary)
                        .background(section == item ? AppPalette.line : .clear, in: RoundedRectangle(cornerRadius: 8))
                }.buttonStyle(QuietPressStyle())
                    .keyboardShortcut(KeyEquivalent(Character(String((WorkspaceSection.allCases.firstIndex(of: item) ?? 0) + 1))), modifiers: .command)
            }
        }
    }
    @ToolbarContentBuilder
    private var sidebarToolbar: some ToolbarContent {
        // Automatic placement in the sidebar keeps the control before the split divider.
        if #available(macOS 26.0, *) {
            ToolbarItem(placement: .automatic) { sidebarToggle }
                .sharedBackgroundVisibility(.hidden)
        } else {
            ToolbarItem(placement: .automatic) { sidebarToggle }
        }
    }
    private var sidebarToggle: some View {
        let title = L(columnVisibility == .detailOnly ? "显示侧栏" : "收起侧栏")
        return Button(action: toggleSidebar) {
            Image(systemName: "sidebar.left")
                .font(.system(size: 16, weight: .regular))
        }
        .buttonStyle(SidebarToggleButtonStyle())
        .foregroundStyle(.secondary)
        .help(title)
        .accessibilityLabel(title)
        .accessibilityIdentifier("workspace.sidebar.toggle")
        .keyboardShortcut("s", modifiers: [.command, .control])
    }
    private func toggleSidebar() {
        // Let the native split view finish before reversing it. Repeated requests
        // cancel in pairs, so rapid clicks retain the user's final intended state.
        guard sidebarTransitionTask == nil else {
            pendingSidebarToggle.toggle()
            return
        }
        let destination: NavigationSplitViewVisibility = columnVisibility == .detailOnly ? .all : .detailOnly
        if reduceMotion {
            var transaction = Transaction(animation: nil)
            transaction.disablesAnimations = true
            withTransaction(transaction) { columnVisibility = destination }
        } else {
            withAnimation(.easeInOut(duration: 0.22)) {
                columnVisibility = destination
            }
            // AppKit's split animation outlives SwiftUI's completion callback.
            // Include its settling frame before applying a pending reversal.
            sidebarTransitionTask = Task { @MainActor in
                try? await Task.sleep(for: .milliseconds(260))
                guard !Task.isCancelled else { return }
                sidebarTransitionTask = nil
                if pendingSidebarToggle {
                    pendingSidebarToggle = false
                    toggleSidebar()
                }
            }
        }
    }
    private var syncPage: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    HStack {
                        Text("同步状态").font(.title3.weight(.semibold))
                        Spacer()
                        Label(L(folder.statusTitle), systemImage: folder.problem != nil ? "exclamationmark.circle" : "folder")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    SyncJourney().frame(maxWidth: .infinity).padding(.vertical, 8)
                    if geometry.size.width >= 1050 {
                        HStack(alignment: .top, spacing: 24) {
                            FolderSetupCard(folder: folder, isDesktop: true).frame(maxWidth: .infinity)
                            textDeliveryCard.frame(width: max(340, (geometry.size.width - 88) * 0.36))
                        }
                    } else {
                        FolderSetupCard(folder: folder, isDesktop: true)
                        textDeliveryCard
                    }
                    Text("使用自己的云盘账号同步，无需另建应用账号。手机与电脑选择同一资料夹；电脑离线时，原音等待接收。")
                        .font(.callout).foregroundStyle(.secondary)
                }.padding(32).frame(width: geometry.size.width, alignment: .leading)
            }
        }
    }
    private var textDeliveryCard: some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 14) {
                Text("文字交付").font(.headline)
                Label(L(workspace.status?.recognitionEnabled == true ? "自动转写已启用" : "自动转写尚未启用"), systemImage: "text.alignleft")
                    .font(.subheadline)
                Text("接收成功表示原音已保存到电脑，不代表已经生成文字。")
                    .font(.caption).foregroundStyle(.secondary)
                if let path = workspace.status?.outputDirectory {
                    Text("文字保存位置").font(.caption.weight(.medium))
                    Text(verbatim: path).font(.caption).foregroundStyle(.secondary)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                }
                Button("查看模型设置") { UserDefaults.standard.set("模型", forKey: "settings.section"); openSettings() }
                    .font(.subheadline)
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
    private func importFile(_ url: URL) {
        importBusy = true
        let scoped = url.startAccessingSecurityScopedResource()
        let library = sync.library
        Task {
            defer { if scoped { url.stopAccessingSecurityScopedResource() }; importBusy = false }
            do {
                _ = try await Task.detached { try AudioFileImporter.importFile(url, into: library) }.value
                sync.refresh()
            } catch { importError = error.localizedDescription }
        }
    }
}

private struct SidebarToggleButtonStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.65 : 1)
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.9 : 1)
            // Keep the hit area fixed while the icon responds to the press.
            .frame(width: 28, height: 28)
            .background(configuration.isPressed ? AppPalette.line : .clear,
                        in: RoundedRectangle(cornerRadius: 6))
            .contentShape(Rectangle())
            .animation(reduceMotion ? nil : .easeOut(duration: 0.10), value: configuration.isPressed)
    }
}
