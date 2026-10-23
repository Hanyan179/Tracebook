import SwiftUI

struct PhoneSyncScreen: View {
    @EnvironmentObject private var folder: FolderSyncController
    @EnvironmentObject private var recorder: CaptureRecorder
    @EnvironmentObject private var workspace: NodeWorkspaceStore
    @EnvironmentObject private var sync: NodeSyncController
    @AppStorage("sync.route") private var route = SyncRoute.current.rawValue
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var help = false
    private var recentRecords: [Recording] {
        Array(recorder.recordings.filter { $0.isPlayable && $0.trashedAt == nil }.prefix(5))
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                if (route == SyncRoute.folder.rawValue && !folder.configured)
                    || (route == SyncRoute.direct.rawValue && !workspace.configured) {
                    SyncJourney(route: SyncRoute(rawValue: route) ?? .local)
                }
                VStack(alignment: .leading, spacing: 16) {
                    Text("同步方式").font(.caption.weight(.medium)).foregroundStyle(AppPalette.muted)
                    if typeSize.isAccessibilitySize { routePicker.pickerStyle(.menu) }
                    else { routePicker.pickerStyle(.segmented) }
                }
                Group {
                    if route == SyncRoute.folder.rawValue { FolderSetupCard(folder: folder) }
                    else if route == SyncRoute.direct.rawValue {
                        SurfaceCard {
                            VStack(alignment: .leading, spacing: 18) {
                                Image(systemName: "laptopcomputer.and.iphone").font(.title2)
                                Text("电脑在身边时").font(.title3.weight(.semibold))
                                Text("同一网络下直接传输。离开这个网络后，原音留在手机等待重连。")
                                    .font(.subheadline).foregroundStyle(AppPalette.muted)
                                NavigationLink("设置电脑连接") { NodeConnectionView(store: workspace) }
                                    .font(.subheadline.weight(.semibold))
                                if workspace.configured {
                                    HStack {
                                        Text("\(sync.pendingCount) 条等待同步").font(.caption).foregroundStyle(AppPalette.muted)
                                        Spacer()
                                        Button("重试") { sync.retry() }
                                    }
                                    if let problem = sync.problem ?? workspace.problem {
                                        Text(L(problem)).font(.footnote).foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }
                    } else {
                        SurfaceCard {
                            VStack(alignment: .leading, spacing: 16) {
                                Image(systemName: "iphone").font(.title2)
                                Text("先安心留在手机").font(.title3.weight(.semibold))
                                Text("原音照常保存、播放和导出。需要电脑整理时，再选择同步方式。")
                                    .font(.subheadline).foregroundStyle(AppPalette.muted)
                            }
                        }
                    }
                }.transition(.opacity)
                VStack(alignment: .leading, spacing: 20) {
                    SectionHeading(title: route == SyncRoute.local.rawValue ? "最近的保存" : "最近的同步")
                    ForEach(recentRecords) { record in
                        let layout = typeSize.isAccessibilitySize
                            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
                            : AnyLayout(HStackLayout(alignment: .top, spacing: 12))
                        layout {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(record.displayTitle).font(.subheadline).lineLimit(1)
                                Text(record.startedAt, format: .dateTime.month().day().hour().minute()).font(.caption).foregroundStyle(.tertiary)
                            }
                            if !typeSize.isAccessibilitySize { Spacer() }
                            Text(L(label(record.id))).font(.caption).foregroundStyle(AppPalette.muted)
                                .multilineTextAlignment(typeSize.isAccessibilitySize ? .leading : .trailing).fixedSize(horizontal: false, vertical: true)
                        }
                        Divider()
                    }
                    if recentRecords.isEmpty { Text("保存第一条录音后，这里会显示它的去向。").font(.subheadline).foregroundStyle(AppPalette.muted) }
                }
                Button("人在外面、电脑关机时会怎样？") { help = true }
                    .font(.footnote).foregroundStyle(AppPalette.muted)
            }.padding(.horizontal, 26).padding(.top, 18).padding(.bottom, 40)
        }.background(AppPalette.canvas).navigationTitle(L("同步")).navigationBarTitleDisplayMode(.inline)
            .sheet(isPresented: $help) { ProductTour() }
            .animation(reduceMotion ? nil : .easeInOut(duration: 0.22), value: route)
            .onChange(of: route) { _, _ in
                workspace.active = route == SyncRoute.direct.rawValue
                NotificationCenter.default.post(name: .nodeConfigurationChanged, object: nil)
                Task {
                    if workspace.active { await workspace.refresh() }
                    await folder.refresh()
                }
            }
    }
    private var routePicker: some View {
        Picker("同步方式", selection: $route) {
            Text("资料夹").tag(SyncRoute.folder.rawValue)
            Text("仅本机").tag(SyncRoute.local.rawValue)
            Text("直连").tag(SyncRoute.direct.rawValue)
        }.disabled(folder.busy)
    }
    private func label(_ id: UUID) -> String {
        if route == SyncRoute.folder.rawValue { return folder.label(for: id) }
        if route == SyncRoute.direct.rawValue { return sync.label(for: id).replacingOccurrences(of: "节点", with: "电脑") }
        return "保存在手机"
    }
}

struct PhonePreferencesScreen: View {
    @EnvironmentObject private var preferences: CapturePreferences
    @AppStorage("appearance") private var appearance = AppAppearance.system.rawValue
    @AppStorage("app.language") private var language = AppLanguage.system.rawValue
    @Environment(\.dynamicTypeSize) private var typeSize
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 30) {
                VStack(alignment: .leading, spacing: 12) {
                    Text("按你的习惯来。").font(.system(.largeTitle, weight: .semibold)).tracking(-1)
                    Text("记录方式、同步与外观，都由你决定。")
                        .font(.subheadline).foregroundStyle(AppPalette.muted)
                }.padding(.top, 16)
                SurfaceCard {
                    VStack(alignment: .leading, spacing: 22) {
                        SectionHeading(title: "记录与同步")
                        NavigationLink { CaptureMethodsScreen() } label: {
                            preferenceRow("录音入口", symbol: "hand.tap", detail: preferences.preferred.title)
                        }
                        Divider()
                        NavigationLink { PhoneSyncScreen() } label: {
                            preferenceRow("同步方式", symbol: "arrow.triangle.2.circlepath", detail: SyncRoute.current.title)
                        }
                        Divider()
                        Link(destination: URL(string: UIApplication.openSettingsURLString)!) { preferenceRow("麦克风权限", symbol: "mic") }
                    }
                }
                SurfaceCard {
                    VStack(alignment: .leading, spacing: 18) {
                        SectionHeading(title: "外观")
                        if typeSize.isAccessibilitySize { appearancePicker.pickerStyle(.menu) }
                        else { appearancePicker.pickerStyle(.segmented) }
                        Picker("界面语言", selection: $language) {
                            ForEach(AppLanguage.allCases) { Text(LocalizedStringKey($0.title)).tag($0.rawValue) }
                        }
                    }
                }
            }.padding(.horizontal, 24).padding(.bottom, 36)
        }.background(AppPalette.canvas).navigationTitle(L("设置")).navigationBarTitleDisplayMode(.inline)
            .buttonStyle(QuietPressStyle()).tint(.primary)
    }
    private var appearancePicker: some View {
        Picker("外观", selection: $appearance) {
            ForEach(AppAppearance.allCases) { item in Text(LocalizedStringKey(item.title)).tag(item.rawValue) }
        }
    }
    private func preferenceRow(_ title: String, symbol: String, detail: String? = nil) -> some View {
        HStack(spacing: 14) {
            Image(systemName: symbol).font(.system(size: 18, weight: .regular)).frame(width: 24)
            VStack(alignment: .leading, spacing: 5) {
                Text(LocalizedStringKey(title)).font(.subheadline.weight(.medium))
                if let detail { Text(LocalizedStringKey(detail)).font(.caption).foregroundStyle(AppPalette.muted) }
            }
            Spacer()
            Image(systemName: "chevron.right").font(.system(size: 10)).foregroundStyle(.tertiary)
        }.foregroundStyle(.primary).frame(minHeight: 44).contentShape(Rectangle())
    }
}
