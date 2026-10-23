import SwiftUI

enum DesktopLibraryKind: String {
    case recording, daily, memory
    var title: String {
        switch self { case .recording: "原音"; case .daily: "每日记录"; case .memory: "长期记忆" }
    }
}

struct MacLibraryBrowser: View {
    @ObservedObject var store: NodeWorkspaceStore
    let kind: DesktopLibraryKind
    @StateObject private var recordings = LibraryLoader<NodeRecord>()
    @StateObject private var days = LibraryLoader<NodeDaily>()
    @StateObject private var memories = LibraryLoader<NodeCandidate>()
    @State private var filter = LibraryFilter()
    @State private var selection: String?
    @State private var scope = "active"
    @State private var managing = false
    @FocusState private var searching: Bool
    private var facets: LibraryFacets { switch kind { case .recording: recordings.facets; case .daily: days.facets; case .memory: memories.facets } }
    private var total: Int { switch kind { case .recording: recordings.total; case .daily: days.total; case .memory: memories.total } }
    private var loading: Bool { switch kind { case .recording: recordings.loading; case .daily: days.loading; case .memory: memories.loading } }
    private var problem: String? { switch kind { case .recording: recordings.problem; case .daily: days.problem; case .memory: memories.problem } }
    private var moreAvailable: Bool { switch kind { case .recording: recordings.nextCursor != nil; case .daily: days.nextCursor != nil; case .memory: memories.nextCursor != nil } }
    private var emptyTitle: String {
        if filter.hasSearchFilters || scope != "active" { return "没有匹配的资料" }
        switch kind { case .recording: return "还没有原音"; case .daily: return "还没有每日记录"; case .memory: return "还没有已确认的记忆" }
    }
    private var emptyHint: String {
        if filter.hasSearchFilters || scope != "active" { return "试试其他关键词或清除筛选。" }
        switch kind {
        case .recording: return "从手机同步或导入音频后，原音会出现在这里。"
        case .daily: return "接收录音或保存笔记后，按日期查看来源记录。"
        case .memory: return "从录音文字、笔记、本地输入或导入资料提出建议，确认后保留为记忆。"
        }
    }
    var body: some View {
        GeometryReader { available in
        HSplitView {
            VStack(spacing: 0) {
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Text(LocalizedStringKey(kind.title)).font(.title3.weight(.semibold))
                        Text(total, format: .number).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        Spacer()
                        if loading { ProgressView().controlSize(.small) }
                        Button { Task { await reload() } } label: { Image(systemName: "arrow.clockwise") }
                            .buttonStyle(.plain).help("刷新资料").accessibilityLabel("刷新资料")
                    }
                    TextField("搜索资料", text: $filter.query)
                        .textFieldStyle(.roundedBorder).focused($searching)
                    if kind != .daily {
                        Picker("范围", selection: $scope) {
                            Text(L(kind == .memory ? "已确认" : "资料库")).tag("active")
                            if kind == .memory { Text("待确认").tag("pending") }
                            if kind == .memory { Text("已停用").tag("inactive") }
                            Text("已归档").tag("archived")
                            if kind == .memory { Text("已忽略").tag("rejected") }
                        }.labelsHidden().pickerStyle(.menu)
                        if kind == .memory, let notice = store.memoryNotice {
                            Text(L(notice)).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    HStack(spacing: 8) {
                        if kind != .daily {
                            Picker("分类", selection: $filter.category) {
                                Text("全部分类").tag("")
                                Text("未分类").tag("uncategorized")
                                ForEach(facets.categories) { Text($0.displayName).tag($0.id) }
                            }.labelsHidden()
                        }
                        Picker("月份", selection: $filter.month) {
                            Text("全部月份").tag("")
                            ForEach(facets.months, id: \.self) { Text(verbatim: $0).tag($0) }
                        }.labelsHidden()
                    }.controlSize(.small)
                    if !facets.tags.isEmpty {
                        HStack {
                            Picker("标签", selection: $filter.tag) {
                                Text("全部标签").tag("")
                                ForEach(facets.tags, id: \.self) { Text(verbatim: $0).tag($0) }
                            }.labelsHidden().controlSize(.small)
                        }
                    }
                    if filter.hasSearchFilters {
                        Button("清除筛选") { filter.query = ""; filter.category = ""; filter.month = ""; filter.tag = "" }
                            .buttonStyle(.plain).font(.caption)
                    }
                }.padding(18)
                Divider()
                List(selection: $selection) {
                    if kind == .recording {
                        ForEach(recordings.items) { item in
                            VStack(alignment: .leading, spacing: 8) {
                                Text(verbatim: item.title).font(.headline).lineLimit(2)
                                HStack {
                                    Text(item.date, format: .dateTime.month().day().hour().minute())
                                    Spacer()
                                    Text(Duration.seconds(item.duration).formatted(.time(pattern: .minuteSecond)))
                                }.font(.caption).foregroundStyle(.secondary)
                                OrganizationBadges(organization: item.organization ?? .init(), categories: facets.categories)
                            }.padding(.vertical, 9).tag(item.id.uuidString)
                        }
                    } else if kind == .daily {
                        ForEach(days.items) { item in
                            VStack(alignment: .leading, spacing: 10) {
                                Text(verbatim: item.id).font(.system(.headline, design: .rounded))
                                Text(LF("%d 条来源", item.sourceCount)).font(.caption).foregroundStyle(.secondary)
                                Text(LocalizedStringKey(dayState(item))).font(.caption2).foregroundStyle(.secondary)
                            }.padding(.vertical, 12).tag(item.id)
                        }
                    } else {
                        ForEach(memories.items) { item in
                            VStack(alignment: .leading, spacing: 10) {
                                Text(verbatim: item.text).font(.body).lineSpacing(3).lineLimit(3)
                                OrganizationBadges(organization: item.organization ?? .init(), categories: facets.categories)
                                Text(LF("%d 条来源", item.sourceCount)).font(.caption).foregroundStyle(.secondary)
                            }.padding(.vertical, 9).tag(item.id.uuidString)
                        }
                    }
                }.listStyle(.inset).scrollContentBackground(.hidden).tint(.gray).accentColor(.gray)
                if let problem {
                    VStack(spacing: 8) {
                        Text(L(problem)).font(.caption).foregroundStyle(.secondary)
                        Button("重新读取") { Task { await reload() } }.font(.caption)
                    }.padding(12)
                } else if !loading && total == 0 {
                    Text(L(emptyTitle)).font(.callout).foregroundStyle(.secondary).padding(16)
                }
                if moreAvailable {
                    Button("再看 40 条") { Task { await more() } }.disabled(loading).padding(12)
                }
                Divider()
                HStack {
                    Text(LF("%d 条资料", total)).font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    if kind != .daily { Button("分类管理") { managing = true }.buttonStyle(.plain).font(.caption) }
                    else {
                        Button("整理今天") {
                            let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"
                            f.timeZone = TimeZone(identifier: store.status?.timeZone ?? "Asia/Shanghai")
                            Task { await store.buildDay(f.string(from: .now)) }
                        }.buttonStyle(.plain).font(.caption).disabled(!store.connected)
                    }
                }.padding(16)
            }.frame(minWidth: 290, idealWidth: 330, maxWidth: 410).background(AppPalette.surface.opacity(0.55))
            GeometryReader { bounds in
                detail.frame(width: bounds.size.width, height: bounds.size.height)
            }.frame(minWidth: 380).background(AppPalette.canvas)
        }
        .frame(width: available.size.width, height: available.size.height)
        }
        .task(id: store.endpoint + String(store.connected) + kind.rawValue + filter.signature + store.libraryRevision.uuidString) { await reload() }
        .onChange(of: scope) { _, value in
            filter.archived = value == "archived"
            filter.state = kind == .memory ? (value == "active" ? "confirmed" : value == "archived" ? "" : value) : ""
        }
        .sheet(isPresented: $managing) { CategoryManager(store: store, kind: kind.rawValue) }
        .background {
            Button("") { searching = true }.keyboardShortcut("f", modifiers: .command).hidden()
        }
    }
    @ViewBuilder private var detail: some View {
        if kind == .recording, let item = recordings.items.first(where: { $0.id.uuidString == selection }) {
            MacRecordingReader(store: store, record: item, categories: facets.categories).id(item.id)
        } else if kind == .daily, let item = days.items.first(where: { $0.id == selection }) {
            MacDailyReader(store: store, day: item).id(item.id)
        } else if kind == .memory, let item = memories.items.first(where: { $0.id.uuidString == selection }) {
            MacMemoryReader(store: store, item: item, categories: facets.categories).id(item.id)
        } else {
            ContentUnavailableView {
                Label(L(problem != nil ? "暂时无法读取资料" : loading ? "正在读取资料" : total == 0 ? emptyTitle : "选择一条资料"), systemImage: kind == .daily ? "calendar" : "text.book.closed")
            } description: {
                Text(L(problem ?? (total == 0 ? emptyHint : "在左侧选择，内容会在这里展开。")))
            } actions: {
                if problem != nil { Button("重新读取") { Task { await reload() } } }
            }
        }
    }
    private func reload() async {
        var effective = filter
        if kind == .memory && scope == "active" { effective.state = "confirmed" }
        switch kind {
        case .recording: await recordings.reload(client: store.client, kind: kind.rawValue, filter: effective)
        case .daily: await days.reload(client: store.client, kind: kind.rawValue, filter: effective)
        case .memory: await memories.reload(client: store.client, kind: kind.rawValue, filter: effective)
        }
        let ids: [String] = switch kind {
        case .recording: recordings.items.map { $0.id.uuidString }
        case .daily: days.items.map(\.id)
        case .memory: memories.items.map { $0.id.uuidString }
        }
        if selection == nil || !ids.contains(selection!) { selection = ids.first }
    }
    private func more() async {
        var effective = filter
        if kind == .memory && scope == "active" { effective.state = "confirmed" }
        switch kind {
        case .recording: await recordings.more(client: store.client, kind: kind.rawValue, filter: effective)
        case .daily: await days.more(client: store.client, kind: kind.rawValue, filter: effective)
        case .memory: await memories.more(client: store.client, kind: kind.rawValue, filter: effective)
        }
    }
}

private func dayState(_ value: NodeDaily) -> String {
    switch value.state {
    case "pending": "等待整理"
    case "manual": "已修订"
    case "conflict": "新版本待合并"
    default: "来源草稿"
    }
}

struct MacDocumentReader: View {
    @ObservedObject var store: NodeWorkspaceStore
    let path: String
    let title: String
    var subtitle = ""
    var day: NodeDaily?
    var editable = true
    @State private var document: NodeDocument?
    @State private var problem: String?
    @State private var editing = false
    @State private var organizing = false
    @State private var sources: [NodeRecord] = []
    @State private var sourceCursor: String?
    @State private var loadingSources = false
    @State private var source: UUID?
    @State private var noteSource: PersonalNote?
    @State private var otherSource: KnowledgeSource?
    @State private var noteSources: [PersonalNote] = []
    @State private var noteCursor: String?
    @Environment(\.openSettings) private var openSettings
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: 16) {
                VStack(alignment: .leading, spacing: 7) {
                    Text(verbatim: title).font(.title2.weight(.semibold))
                    if !subtitle.isEmpty { Text(verbatim: subtitle).font(.caption).foregroundStyle(.secondary) }
                }
                Spacer()
                if day != nil {
                    Button(L(organizing ? "正在整理" : "重新整理"), systemImage: "arrow.clockwise") {
                        organizing = true
                        Task { await store.buildDay(title); await load(); organizing = false }
                    }.disabled(organizing || !store.connected)
                }
                if editable { Button("编辑", systemImage: "square.and.pencil") { editing = true }.disabled(document == nil) }
            }.padding(.horizontal, 30).padding(.vertical, 24)
            Divider()
            GeometryReader { proxy in
                HStack(alignment: .top, spacing: 0) {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 20) {
                            if let problem { Text(L(problem)).font(.callout).foregroundStyle(.secondary) }
                            if let document, !document.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                                MarkdownReader(text: readableBody(document.text))
                            } else if document != nil {
                                ContentUnavailableView("还没有文字", systemImage: "text.alignleft",
                                    description: Text(path.hasSuffix("/transcript")
                                        ? L("尚未生成文字。可在原音页面启动转写，也可以手动补充。")
                                        : L("可以先添加文字。")))
                            } else if problem == nil { ProgressView().frame(maxWidth: .infinity) }
                            else { Button("重新读取") { Task { await load() } } }
                        }.padding(30).frame(maxWidth: 1100, alignment: .leading).frame(maxWidth: .infinity)
                    }
                    if day != nil && proxy.size.width > 900 {
                        Divider()
                        VStack(alignment: .leading, spacing: 24) {
                            Label("本日来源", systemImage: "doc.on.doc").font(.headline)
                            Text(LF("%d 条来源", day?.sourceCount ?? 0)).font(.caption).foregroundStyle(.secondary)
                            ScrollView {
                                VStack(alignment: .leading, spacing: 18) {
                                    ForEach(noteSources) { value in
                                        Button { noteSource = value } label: { Label(value.title, systemImage: "note.text").lineLimit(2) }.buttonStyle(.plain)
                                    }
                                    if noteCursor != nil { Button("更多笔记来源") { Task { await loadNoteSources(more: true) } } }
                                    ForEach(sources) { value in
                                        Button { source = value.id } label: {
                                            VStack(alignment: .leading, spacing: 5) {
                                                Text(verbatim: value.title).lineLimit(2)
                                                Text(value.date, format: .dateTime.hour().minute()).font(.caption).foregroundStyle(.secondary)
                                            }.frame(maxWidth: .infinity, alignment: .leading)
                                        }.buttonStyle(.plain)
                                    }
                                    if sourceCursor != nil { Button("更多来源") { Task { await loadSources(more: true) } }.disabled(loadingSources) }
                                }
                            }
                            Divider()
                            Text(LocalizedStringKey(day.map(dayState) ?? "来源草稿")).font(.caption.weight(.medium))
                            Text("这里保留来源原文；每日分析在另一页单独生成。").font(.caption).foregroundStyle(.secondary).lineSpacing(4)
                            Button {
                                UserDefaults.standard.set("每日整理", forKey: "settings.section")
                                openSettings()
                            } label: { Label("每日整理设置", systemImage: "slider.horizontal.3") }.font(.caption)
                        }.padding(24).frame(width: 260, alignment: .leading).frame(maxHeight: .infinity).background(AppPalette.surface.opacity(0.5))
                    }
                }
            }
        }
        .task(id: path + store.libraryRevision.uuidString) { await load() }
        .sheet(isPresented: $editing, onDismiss: { Task { await load() } }) {
            NavigationStack {
                NodeDocumentEditor(store: store, path: path, title: title, hint: "修改会保留历史版本。")
                    .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { editing = false } } }
            }.frame(minWidth: 640, minHeight: 580).appLanguage()
        }
        .sheet(isPresented: Binding(get: { source != nil }, set: { if !$0 { source = nil } })) {
            NavigationStack {
                if let source {
                    NodeRecordDestination(store: store, id: source).libraryDestinations(store: store)
                        .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { self.source = nil } } }
                }
            }.frame(minWidth: 620, minHeight: 580).appLanguage()
        }
        .sheet(item: $noteSource) { value in
            NavigationStack { NoteReader(workspace: store, note: value)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { noteSource = nil } } }
            }.frame(minWidth: 660, minHeight: 580).appLanguage()
        }
        .sheet(item: $otherSource) { value in KnowledgeSourceReader(workspace: store, source: value).appLanguage() }
        .environment(\.openURL, OpenURLAction { url in
            if let reference = LibraryReference.identity(url) {
                if reference.kind == "note" {
                    Task {
                        do { noteSource = try await store.client?.read("v1/notes/" + reference.id.uuidString, as: PersonalNote.self) }
                        catch { problem = error.localizedDescription }
                    }
                } else if reference.kind == "recording" { source = reference.id }
                else {
                    Task {
                        do { otherSource = try await store.client?.read("v1/sources/" + reference.kind + "/" + reference.id.uuidString, as: KnowledgeSource.self) }
                        catch { problem = error.localizedDescription }
                    }
                }
                return .handled
            }
            return url.scheme == "https" || url.scheme == "http" ? .systemAction : .discarded
        })
    }
    private func load() async {
        do {
            document = try await store.document(path); problem = nil
            if day != nil { await loadSources(more: false); await loadNoteSources(more: false) }
        } catch { problem = error.localizedDescription }
    }
    private func readableBody(_ text: String) -> String {
        guard (day != nil || path.hasPrefix("v1/analysis/")), text.hasPrefix("# " + title),
              let newline = text.firstIndex(of: "\n") else { return text }
        return String(text[text.index(after: newline)...]).trimmingCharacters(in: .whitespacesAndNewlines)
    }
    private func loadNoteSources(more: Bool) async {
        guard let day, let client = store.client else { return }
        do {
            var query = ["day": day.id, "limit": "40", "archived": "all"]
            if more, let noteCursor { query["cursor"] = noteCursor }
            let page = try await client.read("v1/library/note", query: query, as: LibraryPage<PersonalNote>.self)
            noteSources = more ? noteSources + page.items : page.items; noteCursor = page.nextCursor
        } catch { problem = error.localizedDescription }
    }
    private func loadSources(more: Bool) async {
        guard let day, let client = store.client, !loadingSources else { return }
        loadingSources = true; defer { loadingSources = false }
        do {
            var query = ["day": day.id, "limit": "40", "archived": "all"]
            if more, let sourceCursor { query["cursor"] = sourceCursor }
            let page = try await client.read("v1/library/recording", query: query, as: LibraryPage<NodeRecord>.self)
            sources = more ? sources + page.items : page.items; sourceCursor = page.nextCursor
        } catch { problem = error.localizedDescription }
    }
}

private struct MacRecordingReader: View {
    @ObservedObject var store: NodeWorkspaceStore
    @ObservedObject private var playback: AudioPlayback
    let record: NodeRecord
    let categories: [LibraryCategory]
    @State private var organizing = false
    @State private var details = false
    init(store: NodeWorkspaceStore, record: NodeRecord, categories: [LibraryCategory]) {
        self.store = store; self.record = record; self.categories = categories; self.playback = store.playback
    }
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 18) {
                OrganizationBadges(organization: record.organization ?? .init(), categories: categories)
                Spacer()
                Button("整理分类", systemImage: "folder") { organizing = true }
                Button("录音详情", systemImage: "info.circle") { details = true }
            }.padding(24)
            HStack {
                Button { Task { await store.play(record) } } label: {
                    Label(L(playback.isPlaying && playback.recordingID == record.id ? "暂停" : "播放原音"), systemImage: playback.isPlaying && playback.recordingID == record.id ? "pause.fill" : "play.fill")
                }.disabled(store.downloadingID != nil)
                Slider(value: Binding(get: { playback.recordingID == record.id ? playback.position : 0 },
                                      set: { playback.seek(to: $0) }), in: 0...max(1, record.duration))
                    .disabled(playback.recordingID != record.id).accessibilityLabel("回放位置")
                    .frame(maxWidth: 360)
                Text(Duration.seconds(record.duration).formatted(.time(pattern: .minuteSecond))).font(.caption.monospacedDigit())
                Spacer()
                Text(record.date, format: .dateTime.year().month().day().hour().minute()).font(.caption).foregroundStyle(.secondary)
            }.padding(.horizontal, 24).padding(.bottom, 20)
            if let problem = playback.problem ?? store.problem {
                Text(L(problem)).font(.caption).foregroundStyle(.secondary).padding(.horizontal, 24).padding(.bottom, 12)
            }
            Divider()
            TranscriptionView(store: store, recordingID: record.id).padding(.horizontal, 24).padding(.vertical, 12)
            Divider()
            MacDocumentReader(store: store, path: "v1/recordings/\(record.id.uuidString)/transcript", title: record.title)
        }
        .onDisappear { store.playback.stop() }
        .sheet(isPresented: $organizing) { OrganizationEditor(store: store, kind: "recording", id: record.id, initial: record.organization ?? .init()) }
        .sheet(isPresented: $details) {
            NavigationStack {
                NodeRecordView(store: store, record: record).libraryDestinations(store: store)
                    .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { details = false } } }
            }.frame(minWidth: 640, minHeight: 580).appLanguage()
        }
    }
}

private struct MacMemoryReader: View {
    @ObservedObject var store: NodeWorkspaceStore
    let item: NodeCandidate
    let categories: [LibraryCategory]
    @State private var organizing = false
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("长期记忆").font(.title2.weight(.semibold))
                Spacer()
                Button("整理分类", systemImage: "folder") { organizing = true }
            }.padding(30)
            Divider()
            MemoryContentView(workspace: store, initial: item)
        }.sheet(isPresented: $organizing) {
            OrganizationEditor(store: store, kind: "memory", id: item.id, initial: item.organization ?? .init())
        }
    }
}
