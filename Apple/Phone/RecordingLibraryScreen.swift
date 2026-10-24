import SwiftUI
import UniformTypeIdentifiers

struct RecordingLibraryScreen: View {
    @EnvironmentObject private var recorder: CaptureRecorder
    @EnvironmentObject private var workspace: NodeWorkspaceStore
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.locale) private var locale
    @Environment(\.colorScheme) private var scheme
    @State private var displayLimit = 60
    @State private var query = ""
    @State private var searching = false
    @FocusState private var searchFocused: Bool
    @Namespace private var filterSelection
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var filter = 0
    @State private var importing = false
    @State private var importBusy = false
    @State private var renameTarget: Recording?
    @State private var title = ""
    @State private var trashTarget: Recording?

    private var visible: [Recording] {
        recorder.recordings.filter {
            $0.trashedAt == nil && $0.id != recorder.active?.id
                && (filter == 0 || (filter == 1 ? $0.trigger != "import" : $0.trigger == "import"))
                && (query.isEmpty || $0.displayTitle.localizedStandardContains(query))
        }
    }
    private struct DayGroup: Identifiable {
        let id: Date
        let records: [Recording]
    }
    private var groups: [DayGroup] {
        Dictionary(grouping: Array(visible.prefix(displayLimit))) { Calendar.current.startOfDay(for: $0.startedAt) }
            .map { DayGroup(id: $0.key, records: $0.value) }.sorted { $0.id > $1.id }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 12) {
                    Button {
                        Task {
                            if !recorder.ready { await recorder.prepare() }
                            if recorder.ready, !Task.isCancelled { recorder.toggleFromButton() }
                        }
                    } label: {
                        HStack(spacing: 12) {
                            Image(systemName: "mic.fill").font(.system(size: 22)).accessibilityHidden(true)
                            Text("开始录音").fontWeight(.semibold)
                            Spacer(minLength: 0)
                        }.padding(.horizontal, 20).padding(.vertical, 16).frame(minHeight: 56)
                            .foregroundStyle(AppPalette.actionText(scheme))
                            .background(AppPalette.actionFill(scheme), in: Capsule())
                    }.buttonStyle(QuietPressStyle()).disabled(recorder.busy || importBusy)
                        .accessibilityIdentifier("phone.record.toggle")
                }.padding(.bottom, 4)
                let actionsLayout = typeSize.isAccessibilitySize
                    ? AnyLayout(VStackLayout(alignment: .leading, spacing: 12))
                    : AnyLayout(HStackLayout(spacing: 8))
                actionsLayout {
                    Text("最近记录").font(.headline)
                    if !typeSize.isAccessibilitySize { Spacer() }
                    HStack(spacing: 12) {
                    if !searching {
                        Button {
                            withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) { searching = true }
                        } label: { Image(systemName: "magnifyingglass").font(.system(size: 20)).frame(width: 44, height: 44) }
                            .accessibilityLabel("搜索录音")
                    }
                    Menu {
                        Button("导入音频", systemImage: "square.and.arrow.down") { importing = true }
                            .accessibilityIdentifier("phone.import")
                        NavigationLink { RecordingTrashScreen() } label: {
                            Label("最近删除", systemImage: "trash")
                        }
                    } label: {
                        if importBusy { ProgressView().frame(minWidth: 44, minHeight: 44) }
                        else { Text("管理").font(.subheadline).frame(minWidth: 44, minHeight: 44) }
                    }.disabled(importBusy).accessibilityLabel("录音管理")
                    }
                }.buttonStyle(QuietPressStyle())
                if searching {
                    let searchLayout = typeSize.isAccessibilitySize
                        ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
                        : AnyLayout(HStackLayout(spacing: 8))
                    searchLayout {
                        HStack(spacing: 8) {
                            Image(systemName: "magnifyingglass").foregroundStyle(AppPalette.muted)
                            TextField("搜索标题", text: $query).textInputAutocapitalization(.never)
                                .autocorrectionDisabled().submitLabel(.search).focused($searchFocused)
                                .onAppear { searchFocused = true }
                                .onSubmit { searchFocused = false }
                                .accessibilityLabel("搜索标题")
                            if !query.isEmpty {
                                Button { query = ""; searchFocused = true } label: {
                                    Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary).frame(width: 44, height: 44)
                                }.buttonStyle(.plain).accessibilityLabel("清除搜索")
                            }
                        }.padding(.horizontal, 12).frame(minHeight: 48)
                            .background(PhoneStyle.surface, in: RoundedRectangle(cornerRadius: 12))
                        Button("取消") {
                            searchFocused = false
                            withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) { searching = false; query = "" }
                        }.buttonStyle(.plain).frame(minWidth: 44, minHeight: 44)
                    }.transition(reduceMotion ? .opacity : .opacity.combined(with: .move(edge: .top)))
                }
                if typeSize.isAccessibilitySize {
                    Picker("筛选录音", selection: $filter) {
                        Text("全部").tag(0); Text("录音").tag(1); Text("导入").tag(2)
                    }.pickerStyle(.menu)
                } else {
                  HStack(spacing: 10) {
                    ForEach(Array(["全部", "录音", "导入"].enumerated()), id: \.offset) { index, name in
                        Button { filter = index } label: {
                            Text(LocalizedStringKey(name)).font(.caption.weight(filter == index ? .semibold : .regular))
                                .padding(.horizontal, 18).padding(.vertical, 10).frame(minHeight: 44)
                                .foregroundStyle(filter == index ? PhoneStyle.ink : .secondary)
                                .background {
                                    if filter == index {
                                        if reduceMotion { Capsule().fill(PhoneStyle.surface) }
                                        else {
                                            Capsule().fill(PhoneStyle.surface)
                                                .matchedGeometryEffect(id: "recording-filter", in: filterSelection)
                                        }
                                    }
                                }
                        }.buttonStyle(QuietPressStyle()).accessibilityAddTraits(filter == index ? .isSelected : [])
                    }
                    Spacer()
                  }
                }
                if let issue = recorder.libraryProblem {
                    VStack(alignment: .leading, spacing: 12) {
                        Label(issue, systemImage: "exclamationmark.circle")
                            .font(.footnote).foregroundStyle(AppPalette.muted)
                        Button("重新读取") { recorder.reload() }.font(.subheadline)
                    }
                }
                if visible.isEmpty {
                    VStack(alignment: .leading, spacing: 12) {
                        Image(systemName: query.isEmpty ? "waveform" : "magnifyingglass")
                            .font(.system(size: 30, weight: .light)).foregroundStyle(PhoneStyle.accent)
                        Text(L(!query.isEmpty ? "没有找到这条录音" : filter == 1 ? "还没有录音" : filter == 2 ? "还没有导入音频" : "把值得留下的，说下来。"))
                            .font(.headline)
                        Text(L(query.isEmpty && filter == 0 ? "录下的声音和导入的音频会出现在这里。" : "试试其他标题或切换筛选。"))
                            .font(.subheadline).foregroundStyle(AppPalette.muted)
                    }.padding(.vertical, 30)
                } else {
                    LazyVStack(alignment: .leading, spacing: 22) {
                        ForEach(groups) { group in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(dayLabel(group.id)).font(.subheadline.weight(.medium)).foregroundStyle(AppPalette.muted)
                                ForEach(group.records) { record in
                                    NavigationLink { RecordingDetailScreen(recordingID: record.id) } label: {
                                        RecordingRow(recording: record)
                                    }.buttonStyle(.plain)
                                        .contextMenu {
                                            Button("重命名", systemImage: "pencil") {
                                                title = record.title ?? ""
                                                renameTarget = record
                                            }
                                            Button("移到最近删除", systemImage: "trash", role: .destructive) {
                                                trashTarget = record
                                            }
                                        }
                                    Divider()
                                }
                            }
                        }
                    }
                    if visible.count > displayLimit {
                        Button("再看 60 条") { displayLimit += 60 }.font(.subheadline).frame(maxWidth: .infinity).padding(.vertical, 14)
                    }
                }
            }.padding(.horizontal, 20).padding(.top, 12).padding(.bottom, 24)
        }
        .background(PhoneStyle.canvas)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.2), value: filter)
        .sensoryFeedback(.selection, trigger: filter)
        .onChange(of: query) { _, _ in displayLimit = 60 }
        .onChange(of: filter) { _, _ in displayLimit = 60 }
        .navigationTitle(L("记录")).navigationBarTitleDisplayMode(.inline)
        .alert("重命名", isPresented: Binding(get: { renameTarget != nil }, set: { if !$0 { renameTarget = nil } })) {
            TextField("录音标题", text: $title)
            Button("取消", role: .cancel) { renameTarget = nil }
            Button("保存") {
                if let record = renameTarget { recorder.rename(record, to: title) }
                renameTarget = nil
            }
        }
        .confirmationDialog("移到最近删除？", isPresented: Binding(get: { trashTarget != nil }, set: { if !$0 { trashTarget = nil } })) {
            Button("移到最近删除", role: .destructive) {
                if let record = trashTarget { recorder.moveToTrash(record) }
                trashTarget = nil
            }
        } message: { Text("原音会保留，可以随时恢复。") }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.audio]) { result in
            switch result {
            case .success(let url):
                importBusy = true
                Task { await recorder.importAudio(from: url); importBusy = false }
            case .failure(let error): recorder.problem = error.localizedDescription
            }
        }
    }

    private func dayLabel(_ date: Date) -> String {
        if Calendar.current.isDateInToday(date) { return L("今天") }
        if Calendar.current.isDateInYesterday(date) { return L("昨天") }
        return date.formatted(.dateTime.year().month().day().locale(locale))
    }
}

struct RecordingRow: View {
    let recording: Recording
    @EnvironmentObject private var sync: NodeSyncController
    @EnvironmentObject private var folder: FolderSyncController
    @AppStorage("sync.route") private var route = SyncRoute.current.rawValue
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.locale) private var locale
    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: recording.trigger == "import" ? "doc" : "waveform")
                .font(.system(size: 22)).frame(width: 42, height: 48)
                .foregroundStyle(recording.status == .failed ? .orange : PhoneStyle.accent)
            VStack(alignment: .leading, spacing: 7) {
                Text(recording.displayTitle).font(.body.weight(.medium)).lineLimit(typeSize.isAccessibilitySize ? nil : 2)
                if typeSize.isAccessibilitySize {
                    VStack(alignment: .leading, spacing: 5) { metadata }
                        .font(.caption).foregroundStyle(AppPalette.muted)
                } else {
                    HStack(spacing: 9) { metadata }.font(.caption).foregroundStyle(AppPalette.muted)
                }
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
        }.padding(.vertical, 15)
    }
    @ViewBuilder private var metadata: some View {
        Text(recording.startedAt, format: .dateTime.hour().minute().locale(locale))
        Text(recording.duration.timeLabel).monospacedDigit()
        if recording.isPlayable {
            Text(L(route == SyncRoute.folder.rawValue ? folder.label(for: recording.id)
                : (route == SyncRoute.direct.rawValue ? sync.label(for: recording.id).replacingOccurrences(of: "节点", with: "电脑") : "保存在手机")))
        } else { Text(recording.statusLabel) }
    }
}

struct RecordingTrashScreen: View {
    @EnvironmentObject private var recorder: CaptureRecorder
    private var records: [Recording] { recorder.recordings.filter { $0.trashedAt != nil } }
    var body: some View {
        List {
            Section {
                if records.isEmpty { Text("这里没有录音").foregroundStyle(AppPalette.muted) }
                ForEach(records) { record in
                    VStack(alignment: .leading, spacing: 12) {
                        Text(record.displayTitle).font(.headline)
                        HStack {
                            Text(record.duration.timeLabel).foregroundStyle(AppPalette.muted)
                            Spacer()
                            Button("恢复") { recorder.restore(record) }.buttonStyle(.bordered)
                        }
                    }.padding(.vertical, 6)
                }
            } footer: { Text("这里的原音仍保存在设备上，不会自动清除。") }
        }.navigationTitle("最近删除").navigationBarTitleDisplayMode(.inline)
    }
}
