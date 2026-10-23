import SwiftUI

struct PhoneProfileScreen: View {
    @EnvironmentObject private var recorder: CaptureRecorder
    @EnvironmentObject private var workspace: NodeWorkspaceStore
    @EnvironmentObject private var folder: FolderSyncController
    @AppStorage("sync.route") private var route = SyncRoute.current.rawValue
    @State private var tour = false
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.locale) private var locale

    private var recordingCount: Int { recorder.recordings.filter { $0.trashedAt == nil }.count }
    private var storage: String {
        recorder.recordings.reduce(Int64(0)) { $0 + $1.byteCount }
            .formatted(.byteCount(style: .file, spellsOutZero: false).locale(locale))
    }
    private var syncDetail: String {
        if route == SyncRoute.folder.rawValue {
            if folder.problem != nil { return L("同步需要处理") }
            return folder.configured ? folder.folderName : L("待选择资料夹")
        }
        if route == SyncRoute.direct.rawValue { return L(workspace.connected ? "已连接电脑" : "等待连接电脑") }
        return L("仅保存在手机")
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                HStack(spacing: 16) {
                    BrandMark(size: 48).frame(width: 44).accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 7) {
                        Text("我的资料库").font(.title2.weight(.semibold))
                        Text("让日常，慢慢成为记忆。").font(.subheadline).foregroundStyle(.secondary)
                    }
                }.padding(.vertical, 14)
                SurfaceCard {
                    let layout = typeSize.isAccessibilitySize
                        ? AnyLayout(VStackLayout(alignment: .leading, spacing: 24))
                        : AnyLayout(HStackLayout(alignment: .top, spacing: 24))
                    layout {
                        metric(LF("%d", recordingCount), title: "本机记录")
                        Divider()
                        metric(storage, title: "本机占用")
                    }.fixedSize(horizontal: false, vertical: true)
                }
                VStack(alignment: .leading, spacing: 12) {
                    SectionHeading(title: "我的数据")
                    SurfaceCard {
                        VStack(spacing: 18) {
                            NavigationLink { PhoneSyncScreen() } label: {
                                row("同步与保存", symbol: "arrow.triangle.2.circlepath", detail: syncDetail)
                            }
                            if workspace.configured && route == SyncRoute.direct.rawValue {
                                Divider()
                                NavigationLink { WorkspaceHomeView(store: workspace) } label: {
                                    row("电脑资料库", symbol: "text.book.closed")
                                }
                            }
                            Divider()
                            NavigationLink { RecordingTrashScreen() } label: {
                                row("最近删除", symbol: "trash")
                            }
                        }
                    }
                    Text("云端同步使用你在系统中登录的云盘账号。")
                        .font(.footnote).foregroundStyle(.secondary).padding(.horizontal, 4)
                }
                SurfaceCard {
                    VStack(spacing: 18) {
                        NavigationLink { PhonePreferencesScreen() } label: {
                            row("设置", symbol: "gearshape", detail: L("语言、外观与录音"))
                        }
                        Divider()
                        Button { tour = true } label: { row("快速上手", symbol: "play.rectangle") }
                    }
                }
            }.padding(.horizontal, 24).padding(.bottom, 32)
        }
        .background(AppPalette.canvas)
        .navigationTitle(L("我的")).navigationBarTitleDisplayMode(.inline)
        .buttonStyle(QuietPressStyle()).tint(.primary)
        .sheet(isPresented: $tour) { ProductTour().appLanguage() }
    }

    private func metric(_ value: String, title: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(value).font(.title2.weight(.semibold)).monospacedDigit()
            Text(LocalizedStringKey(title)).font(.caption).foregroundStyle(.secondary)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private func row(_ title: String, symbol: String, detail: String? = nil) -> some View {
        HStack(spacing: 13) {
            Image(systemName: symbol).font(.body).frame(width: 24)
            VStack(alignment: .leading, spacing: 5) {
                Text(LocalizedStringKey(title)).font(.subheadline.weight(.medium))
                if let detail { Text(detail).font(.caption).foregroundStyle(.secondary) }
            }
            Spacer(minLength: 8)
            Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary)
        }.foregroundStyle(.primary).frame(minHeight: 44).contentShape(Rectangle())
    }
}
