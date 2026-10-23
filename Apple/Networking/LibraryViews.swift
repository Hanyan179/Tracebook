import SwiftUI

enum LibraryRoute: Hashable {
    case recording(UUID)
    case document(path: String, title: String, hint: String)
}

extension View {
    func libraryDestinations(store: NodeWorkspaceStore) -> some View {
        navigationDestination(for: LibraryRoute.self) { route in
            switch route {
            case .recording(let id): NodeRecordDestination(store: store, id: id)
            case .document(let path, let title, let hint):
                NodeDocumentEditor(store: store, path: path, title: title, hint: hint)
            }
        }
    }
}

struct LibraryPageHeader: View {
    let title: String
    let subtitle: String
    @Environment(\.dynamicTypeSize) private var typeSize
    var body: some View {
        if !typeSize.isAccessibilitySize {
        HStack(alignment: .center, spacing: 24) {
            VStack(alignment: .leading, spacing: 12) {
                Text("声记 · 资料库").font(.caption).foregroundStyle(AppPalette.muted)
                Text(LocalizedStringKey(title)).font(.system(titleStyle, weight: .semibold)).tracking(-1)
                Text(LocalizedStringKey(subtitle)).font(.subheadline).foregroundStyle(AppPalette.muted)
            }
            Spacer(minLength: 8)
            BrandMark(size: markSize)
        }.padding(.vertical, 10)
        }
    }
    private var titleStyle: Font.TextStyle {
        #if os(iOS)
        .title2
        #else
        .largeTitle
        #endif
    }
    private var markSize: CGFloat {
        #if os(iOS)
        44
        #else
        76
        #endif
    }
}

struct MemoryScopeControls: View {
    @Binding var selection: String
    var confirmedValue = "confirmed"
    var pendingCount: Int?
    @Environment(\.dynamicTypeSize) private var typeSize
    var body: some View {
        ViewThatFits(in: .horizontal) {
            if !typeSize.isAccessibilitySize { HStack(spacing: 6) { primaryOptions; otherStates } }
            VStack(alignment: .leading, spacing: 8) {
                if typeSize.isAccessibilitySize { primaryOptions }
                else { HStack(spacing: 6) { primaryOptions } }
                otherStates
            }
        }
    }
    @ViewBuilder private var primaryOptions: some View {
        option("已确认", value: confirmedValue)
        option((pendingCount ?? 0) > 0 ? LF("待确认 %d", pendingCount ?? 0) : L("待确认"), value: "pending", localized: true)
    }
    private var otherStates: some View {
        Menu {
            Button("已停用") { selection = "inactive" }
            Button("已归档") { selection = "archived" }
            Button("已忽略") { selection = "rejected" }
        } label: {
            Label(L(["inactive": "已停用", "archived": "已归档", "rejected": "已忽略"][selection] ?? "更多状态"), systemImage: "ellipsis")
                .font(.caption).padding(.vertical, 8)
                #if os(iOS)
                .frame(minWidth: 44, minHeight: 44)
                #endif
        }.fixedSize()
    }
    private func option(_ title: String, value: String, localized: Bool = false) -> some View {
        Button { selection = value } label: {
            Text(localized ? title : L(title)).font(.subheadline.weight(selection == value ? .semibold : .regular))
                .padding(.horizontal, 10).padding(.vertical, 9)
                #if os(iOS)
                .frame(minHeight: 44)
                #else
                .frame(minHeight: 36)
                #endif
                .background(selection == value ? AppPalette.line : .clear, in: RoundedRectangle(cornerRadius: 8))
        }.buttonStyle(.plain).accessibilityAddTraits(selection == value ? .isSelected : [])
    }
}

struct MemoryEmptyState {
    let scope: String
    let filtered: Bool
    var title: String {
        if filtered { return "没有匹配的记忆" }
        switch scope {
        case "pending": return "暂无待确认记忆"
        case "inactive": return "还没有停用的记忆"
        case "rejected": return "还没有忽略的建议"
        case "archived": return "还没有归档的记忆"
        default: return "还没有已确认的记忆"
        }
    }
    var hint: String {
        if filtered { return "试试其他关键词，或清除当前筛选。" }
        switch scope {
        case "pending": return "新的建议会出现在这里。已确认的内容保存在已确认记忆中。"
        case "inactive": return "停用的记忆会保留来源与历史，需要时可以恢复。"
        case "rejected": return "已忽略的建议会保留在这里，原文与历史不会删除。"
        case "archived": return "归档的记忆会从日常列表收起，原文与历史仍保留。"
        default: return "从录音文字、笔记、本地输入或导入资料提出建议，确认后保留为记忆。"
        }
    }
}

/// Search misses keep the existing filter controls; empty queues offer a return path.
struct MemoryEmptyView: View {
    let filter: LibraryFilter
    @Binding var scope: String
    var confirmedValue = "confirmed"
    var pendingCount: Int?
    private var state: MemoryEmptyState { .init(scope: scope, filtered: filter.hasSearchFilters) }
    var body: some View {
        ContentUnavailableView {
            Label(L(state.title), systemImage: filter.hasSearchFilters ? "magnifyingglass" : "text.book.closed")
        } description: {
            Text(L(state.hint)).fixedSize(horizontal: false, vertical: true)
        } actions: {
            if !filter.hasSearchFilters && scope != confirmedValue {
                Button("查看已确认记忆") { scope = confirmedValue }.buttonStyle(FilledButtonStyle())
            } else if !filter.hasSearchFilters && (pendingCount ?? 0) > 0 {
                Button("查看待确认记忆") { scope = "pending" }.buttonStyle(FilledButtonStyle())
            }
        }
    }
}

struct LibraryFilters: View {
    @Binding var filter: LibraryFilter
    let facets: LibraryFacets
    var categorized = true
    var recording = false
    @State private var showing = false
    private var filterCount: Int {
        [!filter.category.isEmpty, !filter.month.isEmpty, !filter.tag.isEmpty,
         recording && !filter.state.isEmpty, recording && filter.archived].filter { $0 }.count
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField(categorized ? "搜索标题、文字或标签" : "搜索日期或每日内容", text: $filter.query)
                    .textFieldStyle(.plain).accessibilityLabel("搜索资料")
                if !filter.query.isEmpty {
                    Button { filter.query = "" } label: { Image(systemName: "xmark.circle.fill").frame(minWidth: 44, minHeight: 44) }
                        .buttonStyle(.plain).foregroundStyle(.secondary).accessibilityLabel("清除搜索")
                }
            }.padding(.horizontal, 12).frame(minHeight: 44)
                .background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 10))
            HStack {
                Button { showing = true } label: {
                    Label(filterCount == 0 ? L("筛选") : filterCount == 1 ? L("1 项筛选") : LF("%d 项筛选", filterCount), systemImage: "line.3.horizontal.decrease")
                        .frame(minHeight: 44)
                }.buttonStyle(.plain)
                if filterCount > 0 {
                    Button("清除筛选") { clear() }.buttonStyle(.plain).foregroundStyle(.secondary).frame(minHeight: 44)
                }
                Spacer()
            }.font(.subheadline)
        }
        .sheet(isPresented: $showing) {
            NavigationStack {
                Form {
                    if categorized {
                        Picker("主题", selection: $filter.category) {
                            Text("全部分类").tag(""); Text("未分类").tag("uncategorized")
                            ForEach(facets.categories) { Text($0.displayName).tag($0.id) }
                        }
                    }
                    Picker("日期", selection: $filter.month) {
                        Text("全部月份").tag("")
                        ForEach(facets.months, id: \.self) { Text(verbatim: $0).tag($0) }
                    }
                    if categorized && !facets.tags.isEmpty {
                        Picker("标签", selection: $filter.tag) {
                            Text("全部标签").tag("")
                            ForEach(facets.tags, id: \.self) { Text(verbatim: $0).tag($0) }
                        }
                    }
                    if recording {
                        Picker("文字", selection: $filter.state) {
                            Text("全部文字状态").tag(""); Text("已有文字").tag("ready"); Text("等待文字").tag("awaitingText")
                        }
                        Toggle("显示已归档录音", isOn: $filter.archived)
                    }
                    if filterCount > 0 { Button("清除筛选") { clear() } }
                }.formStyle(.grouped).navigationTitle(L("筛选"))
                    #if os(iOS)
                    .navigationBarTitleDisplayMode(.inline)
                    #endif
                    .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { showing = false } } }
            }
            #if os(macOS)
            .frame(minWidth: 420, minHeight: 360)
            #endif
            .appLanguage()
        }
    }
    private func clear() {
        filter.category = ""; filter.month = ""; filter.tag = ""
        if recording { filter.state = ""; filter.archived = false }
    }
}

struct LibraryFooter<Item: Decodable & Identifiable & Sendable>: View {
    @ObservedObject var loader: LibraryLoader<Item>
    let more: () -> Void
    var body: some View {
        VStack(spacing: 18) {
            if let error = loader.problem { Text(error).font(.footnote).foregroundStyle(AppPalette.muted) }
            if loader.loading { ProgressView().padding() }
            else if loader.items.isEmpty {
                VStack(spacing: 14) {
                    Image(systemName: "tray").font(.system(size: 32, weight: .light))
                    Text(L(loader.problem == nil ? "这里还没有资料" : "暂时无法读取资料")).font(.headline)
                    Text(L(loader.problem == nil ? "换一个筛选条件，或等待新的记录到达。" : "原有资料保留，可以重新读取。"))
                        .font(.subheadline).foregroundStyle(AppPalette.muted)
                }.padding(.vertical, 42).frame(maxWidth: .infinity)
            }
            if loader.nextCursor != nil {
                Button("再看 40 条", action: more).buttonStyle(.bordered).disabled(loader.loading)
            }
            if !loader.items.isEmpty {
                Text("已显示 \(loader.items.count) / \(loader.total) 条").font(.caption).foregroundStyle(AppPalette.muted)
            }
        }.frame(maxWidth: .infinity).padding(.vertical, 16)
    }
}

struct OrganizationBadges: View {
    let organization: LibraryOrganization
    let categories: [LibraryCategory]
    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) { badges }
            VStack(alignment: .leading, spacing: 8) { badges }
        }
    }
    @ViewBuilder private var badges: some View {
        if let category = categories.first(where: { $0.id == organization.categoryID }) {
            Text(category.displayName)
                .font(.caption.weight(.medium)).padding(.horizontal, 10).padding(.vertical, 6)
                .background(AppPalette.line, in: Capsule())
        }
        ForEach(organization.tags.prefix(3), id: \.self) { tag in
            Text("#" + tag).font(.caption).foregroundStyle(AppPalette.muted).lineLimit(1)
        }
        if organization.archived { Text("已归档").font(.caption).foregroundStyle(AppPalette.muted) }
    }
}

struct OrganizationEditor: View {
    @ObservedObject var store: NodeWorkspaceStore
    let kind: String
    let id: UUID
    let initial: LibraryOrganization
    @Environment(\.dismiss) private var dismiss
    @State private var category = ""
    @State private var tags = ""
    @State private var archived = false
    @State private var revision = 0
    @State private var categories: [LibraryCategory] = []
    @State private var saving = false
    @State private var problem: String?
    @State private var managing = false
    @State private var initialized = false
    var body: some View {
        NavigationStack {
            Form {
                Section("主题分类") {
                    Picker("分类", selection: $category) {
                        Text("未分类").tag("")
                        ForEach(categories) { Text($0.displayName).tag($0.id) }
                    }
                    Button("新建或重命名分类") { managing = true }
                }
                Section("标签") {
                    TextField("用逗号分隔，例如 项目复盘, 产品", text: $tags)
                    Text("最多 12 个标签，可以跨主题查找。").font(.caption).foregroundStyle(AppPalette.muted)
                }
                Section {
                    Toggle("归档这条资料", isOn: $archived)
                } footer: { Text("归档后从日常列表收起，原音和来源保留，可以恢复。") }
                if let problem {
                    Text(problem).foregroundStyle(AppPalette.muted)
                    Button("重新读取已保存的分类") { Task { await reloadSaved() } }.disabled(saving)
                }
            }.formStyle(.grouped).navigationTitle("整理分类")
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() }.disabled(saving) }
                    ToolbarItem(placement: .confirmationAction) { Button(L(saving ? "保存中" : "保存"), action: save).disabled(saving) }
                }
        }.frame(idealWidth: 480, idealHeight: 460).tint(.primary).appLanguage()
            .onAppear {
                guard !initialized else { return }
                category = initial.categoryID; tags = initial.tags.joined(separator: ", "); archived = initial.archived
                revision = initial.revision; initialized = true
            }
            .task(id: store.libraryRevision) { await loadCategories() }
            .sheet(isPresented: $managing) { CategoryManager(store: store, kind: kind) }
    }
    private func loadCategories() async {
        do { categories = try await store.client?.read("v1/library/\(kind)/facets", as: LibraryFacets.self).categories ?? [] }
        catch { problem = error.localizedDescription }
    }
    private func save() {
        saving = true
        Task {
            defer { saving = false }
            guard let client = store.client else { problem = "请先连接资料库。"; return }
            do {
                let values = tags.components(separatedBy: CharacterSet(charactersIn: ",，"))
                    .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
                let body = LibraryOrganization(categoryID: category, tags: values, archived: archived, revision: revision)
                try await client.sendData("v1/library/\(kind)/\(id.uuidString)", method: "PUT", body: JSONEncoder().encode(body))
                await store.libraryChanged(); dismiss()
            } catch { problem = error.localizedDescription }
        }
    }
    private func reloadSaved() async {
        struct Envelope: Decodable, Sendable { let organization: LibraryOrganization }
        do {
            guard let value = try await store.client?.read("v1/library/\(kind)/\(id.uuidString)", as: Envelope.self).organization else { return }
            category = value.categoryID; tags = value.tags.joined(separator: ", "); archived = value.archived
            revision = value.revision; problem = nil
        } catch { problem = error.localizedDescription }
    }
}

struct CategoryManager: View {
    @ObservedObject var store: NodeWorkspaceStore
    let kind: String
    @Environment(\.dismiss) private var dismiss
    @State private var categories: [LibraryCategory] = []
    @State private var name = ""
    @State private var renaming: LibraryCategory?
    @State private var rename = ""
    @State private var busy = false
    @State private var problem: String?
    var body: some View {
        NavigationStack {
            Form {
                Section(kind == "memory" ? "记忆分类" : "录音分类") {
                    ForEach(categories) { category in
                        HStack {
                            Text(category.displayName)
                            Spacer()
                            Text("\(category.count)").foregroundStyle(AppPalette.muted)
                            Button("重命名") { renaming = category; rename = category.name }.buttonStyle(.borderless)
                                .accessibilityLabel("重命名\(category.name)")
                        }
                    }
                }
                Section("新建分类") {
                    TextField("分类名称", text: $name)
                    Button("添加分类") { change(nil) }.disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || busy)
                }
                if let problem { Text(problem).font(.footnote).foregroundStyle(AppPalette.muted) }
            }.formStyle(.grouped).navigationTitle("分类管理")
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
        }.frame(idealWidth: 460, idealHeight: 520).tint(.primary).appLanguage()
            .task { await load() }
            .alert("重命名分类", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
                TextField("名称", text: $rename)
                Button("取消", role: .cancel) { renaming = nil }
                Button("保存") { change(renaming); renaming = nil }
            }
    }
    private func load() async {
        do { categories = try await store.client?.read("v1/library/\(kind)/facets", as: LibraryFacets.self).categories ?? [] }
        catch { problem = error.localizedDescription }
    }
    private func change(_ category: LibraryCategory?) {
        busy = true
        let chosen = category == nil ? name : rename
        Task {
            defer { busy = false }
            guard let client = store.client else { problem = "资料库尚未连接。"; return }
            do {
                struct Body: Encodable { let name: String; let revision: Int? }
                let path = "v1/library/\(kind)/categories" + (category.map { "/\($0.id)" } ?? "")
                try await client.sendData(path, method: category == nil ? "POST" : "PUT", body: JSONEncoder().encode(Body(name: chosen, revision: category?.revision)))
                name = ""; problem = nil; await load(); await store.libraryChanged()
            } catch { problem = error.localizedDescription }
        }
    }
}

struct NodeRecordDestination: View {
    @ObservedObject var store: NodeWorkspaceStore
    let id: UUID
    @State private var record: NodeRecord?
    @State private var problem: String?
    var body: some View {
        Group {
            if let record { NodeRecordView(store: store, record: record) }
            else if let problem { ContentUnavailableView("无法读取来源", systemImage: "exclamationmark.circle", description: Text(problem)) }
            else { ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity) }
        }.task {
            do { record = try await store.client?.read("v1/library/recording/\(id.uuidString)", as: NodeRecord.self) }
            catch { problem = error.localizedDescription }
        }
    }
}
