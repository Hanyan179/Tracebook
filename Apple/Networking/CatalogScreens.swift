import SwiftUI

struct NodeRecordsView: View {
    @ObservedObject var store: NodeWorkspaceStore
    @StateObject private var loader = LibraryLoader<NodeRecord>()
    @State private var filter = LibraryFilter()
    @State private var managing = false
    @State private var editing: NodeRecord?
    private var groups: [String] { Array(Set(loader.items.map { $0.day ?? "日期未知" })).sorted(by: >) }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                LibraryPageHeader(title: "留住的每一段声音。", subtitle: "按主题整理，用标签找到关联。")
                HStack {
                    Picker("范围", selection: $filter.archived) {
                        Text("资料库").tag(false); Text("已归档").tag(true)
                    }.pickerStyle(.segmented).frame(maxWidth: 220)
                    Spacer()
                    Button("分类管理") { managing = true }.buttonStyle(.bordered)
                    Button { Task { await reload() } } label: { Image(systemName: "arrow.clockwise") }
                        .disabled(loader.loading).accessibilityLabel("刷新原音列表")
                }
                LibraryFilters(filter: $filter, facets: loader.facets)
                HStack {
                    Text(loader.total == 1 ? L("1 条录音") : LF("%lld 条录音", loader.total)).font(.caption).foregroundStyle(AppPalette.muted)
                    Spacer()
                    Picker("文字", selection: $filter.state) {
                        Text("全部文字状态").tag("")
                        Text("已有文字").tag("ready")
                        Text("待添加文字").tag("awaitingText")
                    }.pickerStyle(.menu).fixedSize().font(.caption)
                }
                LazyVStack(alignment: .leading, spacing: 26) {
                    ForEach(groups, id: \.self) { day in
                        VStack(alignment: .leading, spacing: 12) {
                            SectionHeading(title: day)
                            ForEach(loader.items.filter { ($0.day ?? "日期未知") == day }) { record in
                                NavigationLink { NodeRecordView(store: store, record: record) } label: { row(record) }
                                    .buttonStyle(QuietPressStyle())
                                    .contextMenu { Button("整理分类", systemImage: "folder") { editing = record } }
                            }
                        }
                    }
                }
                LibraryFooter(loader: loader) { Task { await loader.more(client: store.client, kind: "recording", filter: filter) } }
            }.padding(28).frame(maxWidth: 1000).frame(maxWidth: .infinity)
        }.background(AppPalette.canvas)
            .task(id: store.endpoint + filter.signature + store.libraryRevision.uuidString) { await reload() }
            .sheet(isPresented: $managing) { CategoryManager(store: store, kind: "recording") }
            .sheet(item: $editing) { record in OrganizationEditor(store: store, kind: "recording", id: record.id, initial: record.organization ?? .init()) }
    }
    private func reload() async { await loader.reload(client: store.client, kind: "recording", filter: filter) }
    private func row(_ record: NodeRecord) -> some View {
        HStack(alignment: .top, spacing: 18) {
            Image(systemName: "waveform").font(.system(size: 23, weight: .light))
                .frame(width: 48, height: 56).foregroundStyle(AppPalette.muted)
            VStack(alignment: .leading, spacing: 12) {
                Text(record.title).font(.headline).foregroundStyle(.primary).lineLimit(2)
                HStack(spacing: 12) {
                    Text(record.date, format: .dateTime.hour().minute())
                    Text(Duration.seconds(record.duration).formatted(.time(pattern: .minuteSecond)))
                    Text(L(record.textState == "ready" ? "已有文字" : "待添加文字"))
                }.font(.caption).foregroundStyle(AppPalette.muted)
                if let excerpt = record.excerpt, !excerpt.isEmpty { Text(excerpt).font(.subheadline).foregroundStyle(AppPalette.muted).lineLimit(2) }
                OrganizationBadges(organization: record.organization ?? .init(), categories: loader.facets.categories)
            }
            Spacer(minLength: 0)
            Image(systemName: "arrow.up.right").font(.caption).foregroundStyle(AppPalette.muted).padding(.top, 4)
        }.padding(22).frame(maxWidth: .infinity, alignment: .leading)
            .background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 21))
    }
}

struct NodeDailyView: View {
    @ObservedObject var store: NodeWorkspaceStore
    @StateObject private var loader = LibraryLoader<NodeDaily>()
    @State private var filter = LibraryFilter()
    @State private var generating = false
    private var months: [String] { Array(Set(loader.items.map { String($0.id.prefix(7)) })).sorted(by: >) }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                LibraryPageHeader(title: "每天，都有迹可循。", subtitle: "按月份回看，也可以搜索当天的内容。")
                HStack {
                    Text("\(loader.total) 天记录").font(.caption).foregroundStyle(AppPalette.muted)
                    Spacer()
                    Button(L(generating ? "正在整理" : "整理今天"), systemImage: "calendar.badge.plus") {
                        let formatter = DateFormatter(); formatter.dateFormat = "yyyy-MM-dd"
                        formatter.timeZone = TimeZone(identifier: store.status?.timeZone ?? "Asia/Shanghai")
                        generating = true
                        Task { await store.buildDay(formatter.string(from: .now)); generating = false }
                    }.buttonStyle(.bordered).disabled(generating || !store.connected)
                    Button { Task { await reload() } } label: { Image(systemName: "arrow.clockwise") }.accessibilityLabel("刷新每日记录")
                }
                LibraryFilters(filter: $filter, facets: loader.facets, categorized: false)
                LazyVStack(alignment: .leading, spacing: 26) {
                    ForEach(months, id: \.self) { month in
                        VStack(alignment: .leading, spacing: 12) {
                            SectionHeading(title: month)
                            ForEach(loader.items.filter { $0.id.hasPrefix(month) }) { day in
                                HStack(spacing: 20) {
                                    Text(String(day.id.suffix(2))).font(.system(size: 28, weight: .light)).monospacedDigit()
                                        .frame(width: 48)
                                    NavigationLink(value: LibraryRoute.document(path: "v1/daily/\(day.id)", title: day.id, hint: "按来源整理的每日草稿，可以修订。")) {
                                        VStack(alignment: .leading, spacing: 8) {
                                            Text(day.id).font(.headline)
                                            Text(L("\(day.recordingCount ?? 0) 段原音 · " + state(day)))
                                                .font(.caption).foregroundStyle(AppPalette.muted)
                                        }.frame(maxWidth: .infinity, alignment: .leading)
                                    }.buttonStyle(.plain)
                                    if day.state == "pending" {
                                        Button("整理") { Task { await store.buildDay(day.id) } }.buttonStyle(.bordered)
                                    } else { Image(systemName: "arrow.up.right").font(.caption).foregroundStyle(AppPalette.muted) }
                                }.padding(22).background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 21))
                            }
                        }
                    }
                }
                LibraryFooter(loader: loader) { Task { await loader.more(client: store.client, kind: "daily", filter: filter) } }
            }.padding(28).frame(maxWidth: 1000).frame(maxWidth: .infinity)
        }.background(AppPalette.canvas)
            .task(id: store.endpoint + filter.signature + store.libraryRevision.uuidString) { await reload() }
    }
    private func reload() async { await loader.reload(client: store.client, kind: "daily", filter: filter) }
    private func state(_ day: NodeDaily) -> String {
        switch day.state { case "pending": "等待整理"; case "conflict": "新版本待合并"; case "manual": "已修订"; default: "每日草稿" }
    }
}

struct NodeMemoryView: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    @ObservedObject var store: NodeWorkspaceStore
    @StateObject private var loader = LibraryLoader<NodeCandidate>()
    @State private var filter = LibraryFilter(state: "confirmed")
    @State private var scope = "confirmed"
    @State private var managing = false
    @State private var editing: NodeCandidate?
    @State private var selected: NodeCandidate?
    @State private var deciding: UUID?
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                LibraryPageHeader(title: "重要的，慢慢留下。", subtitle: "分类与来源始终保留，由你决定哪些值得记住。")
                ViewThatFits(in: .horizontal) {
                    HStack { scopePicker; Spacer(); actions }
                    VStack(alignment: .leading, spacing: 14) { scopePicker; actions }
                }
                LibraryFilters(filter: $filter, facets: loader.facets)
                Text(LF("%d 条记忆", loader.total)).font(.caption).foregroundStyle(AppPalette.muted)
                if let notice = store.memoryNotice { Text(L(notice)).font(.callout).foregroundStyle(.secondary) }
                if let problem = store.problem { Text(problem).font(.footnote).foregroundStyle(AppPalette.muted) }
                if !(store.status?.conflicts.isEmpty ?? true) {
                    Text("部分导出文件包含人工修改，新内容已另存，请在归档目录核对。")
                        .font(.footnote).foregroundStyle(AppPalette.muted)
                }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 280), spacing: 18)], alignment: .leading, spacing: 18) {
                    ForEach(loader.items) { candidate in
                        SurfaceCard {
                            VStack(alignment: .leading, spacing: 20) {
                                OrganizationBadges(organization: candidate.organization ?? .init(), categories: loader.facets.categories)
                                Button { selected = candidate } label: {
                                    Text(candidate.text).font(.body).lineSpacing(5).lineLimit(6)
                                        .frame(maxWidth: .infinity, alignment: .leading).foregroundStyle(.primary)
                                }.buttonStyle(.plain).accessibilityLabel(LF("查看记忆：%@", candidate.text))
                                HStack {
                                    Label(LF("%d 条来源", candidate.sourceCount), systemImage: "link")
                                    Spacer()
                                    Button("整理分类") { editing = candidate }.buttonStyle(.plain)
                                }.font(.caption).foregroundStyle(AppPalette.muted)
                                if scope == "pending" {
                                    HStack {
                                        Button("确认记忆") { decide(candidate, true) }.buttonStyle(FilledButtonStyle())
                                        Button("忽略") { decide(candidate, false) }.buttonStyle(.bordered)
                                    }.disabled(deciding != nil)
                                }
                            }
                        }
                    }
                }
                LibraryFooter(loader: loader) { Task { await loader.more(client: store.client, kind: "memory", filter: filter) } }
            }.padding(28).frame(maxWidth: 1100).frame(maxWidth: .infinity)
        }.background(AppPalette.canvas)
            .task(id: store.endpoint + filter.signature + store.libraryRevision.uuidString) { await reload() }
            .onChange(of: scope) { _, value in filter.archived = value == "archived"; filter.state = value == "archived" ? "" : value }
            .sheet(isPresented: $managing) { CategoryManager(store: store, kind: "memory") }
            .sheet(item: $editing) { value in OrganizationEditor(store: store, kind: "memory", id: value.id, initial: value.organization ?? .init()) }
            .sheet(item: $selected) { value in MemoryDetail(store: store, candidate: value) }
    }
    private var scopePicker: some View {
        Picker("记忆状态", selection: $scope) {
            Text("已确认").tag("confirmed"); Text("待确认").tag("pending")
            Text("已停用").tag("inactive")
            Text("已归档").tag("archived"); Text("已忽略").tag("rejected")
        }.pickerStyle(.menu)
    }
    private var actions: some View {
        let layout = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: 14)) : AnyLayout(HStackLayout(spacing: 14))
        return layout {
            Button("分类管理") { managing = true }.buttonStyle(.bordered)
            NavigationLink("记忆文件", value: LibraryRoute.document(path: "v1/memory", title: "记忆文件", hint: "人工编辑会保留独立版本，分类列表来自确认条目。")).font(.caption)
            Button { Task { await reload() } } label: { Image(systemName: "arrow.clockwise") }.accessibilityLabel("刷新记忆列表")
        }
    }
    private func reload() async { await loader.reload(client: store.client, kind: "memory", filter: filter) }
    private func decide(_ candidate: NodeCandidate, _ accepted: Bool) {
        deciding = candidate.id
        Task { await store.decide(candidate, confirmed: accepted); deciding = nil }
    }
}

struct MemoryDetail: View {
    @ObservedObject var store: NodeWorkspaceStore
    let candidate: NodeCandidate
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            MemoryContentView(workspace: store, initial: candidate).navigationTitle(L("记忆详情"))
                #if os(iOS)
                .navigationBarTitleDisplayMode(.inline)
                #endif
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
        }.tint(.primary).appLanguage()
        #if os(macOS)
        .frame(minWidth: 580, idealWidth: 720, minHeight: 560)
        #endif
    }
}
