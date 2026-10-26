import SwiftUI

struct NotesLibrary: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    var initialSelection: UUID? = nil
    @StateObject private var loader = LibraryLoader<PersonalNote>()
    @State private var filter = LibraryFilter()
    @State private var selection: UUID?
    @State private var creating = false
    @State private var managing = false
    @State private var openedNote: PersonalNote?
    @FocusState private var searching: Bool
    #if os(macOS)
    @FocusState private var listFocused: Bool
    #endif
    private var emptyTitle: String { filter.hasSearchFilters || filter.archived ? "没有匹配的笔记" : "还没有笔记" }
    var body: some View {
        Group {
            #if os(macOS)
            GeometryReader { geometry in
                HSplitView {
                    list.frame(minWidth: 290, idealWidth: 330, maxWidth: 410)
                    if let item = loader.items.first(where: { $0.id == selection }) {
                        NoteReader(workspace: workspace, note: item).id(item.id)
                            .frame(minWidth: 380, maxWidth: .infinity, maxHeight: .infinity)
                    } else {
                        ContentUnavailableView {
                            Label(L(loader.problem != nil ? "暂时无法读取笔记" : loader.loading ? "正在读取资料" : loader.total == 0 ? emptyTitle : "选择一篇笔记"),
                                  systemImage: "note.text")
                        } description: {
                            Text(L(loader.problem ?? "随手写下想法，之后随时找回。"))
                        } actions: {
                            if loader.problem != nil { Button("重新读取") { Task { await reload() } } }
                            else if !loader.loading && !filter.hasSearchFilters && !filter.archived {
                                Button("写下第一篇笔记") { creating = true }.disabled(!workspace.connected)
                            }
                        }
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                }.frame(width: geometry.size.width, height: geometry.size.height)
            }
            #else
            phoneList
                .navigationDestination(isPresented: Binding(get: { openedNote != nil }, set: { if !$0 { openedNote = nil } })) {
                    if let openedNote { NoteReader(workspace: workspace, note: openedNote) }
                }
                .navigationTitle(L("笔记"))
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .primaryAction) {
                        Button { creating = true } label: { Image(systemName: "square.and.pencil") }
                            .accessibilityLabel("新建笔记").disabled(!workspace.connected)
                    }
                    ToolbarItem(placement: .topBarTrailing) {
                        Menu {
                            Button("刷新资料", systemImage: "arrow.clockwise") { Task { await workspace.refresh(); await reload() } }
                            Picker("范围", selection: $filter.archived) {
                                Text("资料库").tag(false); Text("已归档").tag(true)
                            }
                            Button("分类管理", systemImage: "folder") { managing = true }
                        } label: { Image(systemName: "ellipsis") }
                            .accessibilityLabel("笔记列表操作")
                    }
                }
            #endif
        }.background(AppPalette.canvas)
            .task(id: filter.signature + workspace.endpoint + String(workspace.connected) + workspace.libraryRevision.uuidString) { await reload() }
            .onChange(of: initialSelection) { _, id in if let id { selection = id } }
            .sheet(isPresented: $creating, onDismiss: { Task { await reload(); await workspace.refresh() } }) {
                NoteEditor(workspace: workspace, noteID: nil) { id in
                    filter = LibraryFilter(); selection = id
                }.appLanguage()
            }
            .sheet(isPresented: $managing, onDismiss: { Task { await reload() } }) { CategoryManager(store: workspace, kind: "note").appLanguage() }
            #if os(macOS)
            .background(Button("") { searching = true }.keyboardShortcut("f", modifiers: .command).hidden())
            #endif
    }
    #if os(iOS)
    private var phoneList: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                LibraryFilters(filter: $filter, facets: loader.facets)
                if loader.problem == nil, !loader.loading {
                    Text(filter.archived
                        ? (loader.total == 1 ? L("1 条已归档笔记") : LF("%d 条已归档笔记", loader.total))
                        : (loader.total == 1 ? L("1 条笔记") : LF("%d 条笔记", loader.total)))
                        .font(.subheadline.weight(.medium)).foregroundStyle(.secondary)
                }
                if let error = loader.problem {
                    Label("暂时无法读取笔记", systemImage: "wifi.exclamationmark").font(.headline)
                    Text(L(error)).foregroundStyle(.secondary)
                    Button("重新读取") { Task { await workspace.refresh(); await reload() } }.frame(minHeight: 44)
                }
                ForEach(loader.items) { item in
                    // The selection outlives filtering or archiving this row.
                    Button { openedNote = item } label: {
                        HStack(alignment: .top, spacing: 12) {
                            row(item).frame(maxWidth: .infinity, alignment: .leading)
                            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.secondary).padding(.top, 12)
                        }.padding(.horizontal, 12).padding(.vertical, 4)
                            .background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 12))
                    }.buttonStyle(.plain)
                }
                if loader.loading { ProgressView("正在读取资料").frame(maxWidth: .infinity).padding(.vertical, 12) }
                if loader.problem == nil, !loader.loading, loader.items.isEmpty {
                    Text(L(filter.archived && !filter.hasSearchFilters ? "暂无已归档笔记" : emptyTitle))
                        .font(.headline).padding(.top, 12)
                    if filter.hasSearchFilters {
                        Text("尝试其他关键词，或调整上方筛选。").foregroundStyle(.secondary)
                    } else if filter.archived {
                        Button("返回笔记") { filter.archived = false }.frame(minHeight: 44)
                    } else {
                        Button("写下第一篇笔记") { creating = true }.frame(minHeight: 44).disabled(!workspace.connected)
                    }
                }
                if loader.nextCursor != nil, loader.problem == nil {
                    Button("再看 40 条") { Task { await loader.more(client: workspace.client, kind: "note", filter: filter) } }
                        .frame(minHeight: 44).disabled(loader.loading)
                }
            }.padding(18)
        }.refreshable { await workspace.refresh(); await reload() }
    }
    #endif
    private var list: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("笔记").font(.title3.weight(.semibold))
                    Text(loader.total, format: .number).font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button { creating = true } label: { Image(systemName: "square.and.pencil") }
                        .help("新建笔记").accessibilityLabel("新建笔记").disabled(!workspace.connected)
                        .keyboardShortcut("n", modifiers: [.command, .shift])
                }
                TextField("搜索笔记", text: $filter.query).textFieldStyle(.roundedBorder).focused($searching)
                Picker("范围", selection: $filter.archived) { Text("资料库").tag(false); Text("已归档").tag(true) }
                    .pickerStyle(.segmented).labelsHidden()
                HStack {
                    Picker("分类", selection: $filter.category) {
                        Text("全部分类").tag(""); Text("未分类").tag("uncategorized")
                        ForEach(loader.facets.categories) { Text($0.displayName).tag($0.id) }
                    }.labelsHidden()
                    Picker("月份", selection: $filter.month) {
                        Text("全部月份").tag("")
                        ForEach(loader.facets.months, id: \.self) { Text(verbatim: $0).tag($0) }
                    }.labelsHidden()
                }.controlSize(.small)
                if filter.hasSearchFilters {
                    Button("清除筛选") { filter.query = ""; filter.category = ""; filter.month = ""; filter.tag = "" }
                        .buttonStyle(.plain).font(.caption)
                }
            }.padding(18)
            Divider()
            ScrollViewReader { proxy in
                List(selection: $selection) {
                    ForEach(loader.items) { item in
                        #if os(macOS)
                        row(item).tag(item.id).id(item.id).listRowSeparator(.hidden)
                        #else
                        NavigationLink(value: item.id) { row(item) }
                        #endif
                    }
                }
                #if os(macOS)
                .listStyle(.inset)
                .focusable(interactions: .edit)
                .focused($listFocused)
                .focusEffectDisabled()
                .simultaneousGesture(TapGesture().onEnded { listFocused = true })
                .onMoveCommand { direction in
                    guard listFocused, !loader.items.isEmpty else { return }
                    let step: Int
                    switch direction {
                    case .up: step = -1
                    case .down: step = 1
                    default: return
                    }
                    let current = loader.items.firstIndex { $0.id == selection }
                        ?? (step > 0 ? -1 : loader.items.count)
                    let next = min(max(current + step, 0), loader.items.count - 1)
                    selection = loader.items[next].id
                    proxy.scrollTo(loader.items[next].id)
                }
                #else
                .listStyle(.plain)
                #endif
                .scrollContentBackground(.hidden).tint(.gray)
            }
            if loader.loading { ProgressView().controlSize(.small).padding(10) }
            if let error = loader.problem {
                VStack(spacing: 8) {
                    Text(L(error)).font(.caption).foregroundStyle(.secondary)
                    Button("重新读取") { Task { await reload() } }.font(.caption)
                }.padding(12)
            } else if !loader.loading && loader.items.isEmpty {
                Text(L(emptyTitle)).font(.callout).foregroundStyle(.secondary).padding(16)
            }
            if loader.nextCursor != nil { Button("再看 40 条") { Task { await loader.more(client: workspace.client, kind: "note", filter: filter) } }.padding(12).disabled(loader.loading) }
            HStack { Text(LF("%d 条资料", loader.total)); Spacer(); Button("分类管理") { managing = true }.buttonStyle(.plain) }
                .font(.caption).foregroundStyle(.secondary).padding(16)
        }.background(AppPalette.surface.opacity(0.5))
    }
    private func row(_ item: PersonalNote) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(verbatim: item.title).font(.subheadline.weight(.semibold)).lineLimit(2)
            Text(verbatim: item.preview).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
            #if os(macOS)
            HStack {
                Text(verbatim: item.day ?? "")
                Spacer(minLength: 8)
                if let organization = item.organization, !organization.categoryID.isEmpty || !organization.tags.isEmpty {
                    OrganizationBadges(organization: organization, categories: loader.facets.categories)
                }
            }.font(.caption).foregroundStyle(.secondary)
            #else
            ViewThatFits(in: .horizontal) {
                HStack { rowMetadata(item) }
                VStack(alignment: .leading, spacing: 6) { rowMetadata(item) }
            }.font(.caption).foregroundStyle(.secondary)
            #endif
        }.padding(.vertical, 8).padding(.horizontal, 4)
    }
    @ViewBuilder private func rowMetadata(_ item: PersonalNote) -> some View {
        Text(verbatim: item.day ?? "")
        if let organization = item.organization, !organization.categoryID.isEmpty || !organization.tags.isEmpty {
            OrganizationBadges(organization: organization, categories: loader.facets.categories)
        }
    }
    private func reload() async {
        await loader.reload(client: workspace.client, kind: "note", filter: filter)
        if selection == nil || !loader.items.contains(where: { $0.id == selection }) { selection = loader.items.first { $0.id == initialSelection }?.id ?? loader.items.first?.id }
    }
}

struct NoteReader: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let note: PersonalNote
    @State private var document: PersonalNote?
    @State private var editing = false
    @State private var organizing = false
    @State private var organization: LibraryOrganization?
    @State private var error: String?
    @State private var proposing: KnowledgeSource?
    @State private var proposalSaved = false
    private var archived: Bool { (organization ?? note.organization)?.archived == true }
    var body: some View {
        VStack(spacing: 0) {
            #if os(macOS)
            HStack {
                Label("笔记", systemImage: "note.text").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("整理分类") { organizing = true }
                Button("编辑") { editing = true }
            }.buttonStyle(.borderless).padding(22)
            Divider().opacity(0.5)
            #endif
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    Text(verbatim: document?.title ?? note.title).font(.largeTitle.weight(.medium)).textSelection(.enabled)
                    Text(verbatim: note.day ?? "").font(.caption).foregroundStyle(.secondary)
                    if archived { Label("已归档", systemImage: "archivebox").font(.callout).foregroundStyle(.secondary) }
                    if let document { MarkdownReader(text: document.text ?? "") }
                    else if error == nil { ProgressView() }
                    if !archived, let document, !(document.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        Button("提出记忆建议", systemImage: "bookmark.badge.plus") {
                            Task {
                                do {
                                    guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
                                    proposing = try await client.read("v1/sources/note/" + note.id.uuidString, as: KnowledgeSource.self)
                                } catch { self.error = error.localizedDescription }
                            }
                        }
                        if proposalSaved { Text("已加入待确认记忆。").font(.caption).foregroundStyle(.secondary) }
                    }
                    if let error { Text(L(error)); Button("重新读取") { Task { await load() } } }
                }
                #if os(macOS)
                .padding(32)
                #else
                .padding(24)
                #endif
                .frame(maxWidth: 850, alignment: .leading).frame(maxWidth: .infinity)
            }
        }
        #if os(iOS)
        .navigationTitle(L("笔记")).navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button("编辑", systemImage: "pencil") { editing = true }
            }
            ToolbarItem(placement: .secondaryAction) {
                Button("整理分类", systemImage: "folder") { organizing = true }
            }
        }
        #endif
        .task { await load() }.sourceLinks(workspace: workspace)
            .sheet(isPresented: $editing, onDismiss: { Task { await load(); await workspace.refresh() } }) { NoteEditor(workspace: workspace, noteID: note.id).appLanguage() }
            .sheet(isPresented: $organizing, onDismiss: { Task { await load() } }) {
                OrganizationEditor(store: workspace, kind: "note", id: note.id, initial: organization ?? note.organization ?? .init()).appLanguage()
            }
            .sheet(item: $proposing) { source in
                MemoryProposalSheet(workspace: workspace, source: source, sourceText: source.text ?? "") { proposalSaved = true }.appLanguage()
            }
    }
    private func load() async {
        do {
            guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
            document = try await client.read("v1/notes/" + note.id.uuidString, as: PersonalNote.self)
            struct Metadata: Decodable, Sendable { let organization: LibraryOrganization }
            organization = try await client.read("v1/library/note/" + note.id.uuidString, as: Metadata.self).organization
            error = nil
        } catch { self.error = error.localizedDescription }
    }
}

struct NoteEditor: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let noteID: UUID?
    var onSaved: ((UUID) -> Void)? = nil
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var text = ""
    @State private var etag = ""
    @State private var createdID = UUID()
    @State private var loaded = false
    @State private var saving = false
    @State private var error: String?
    @State private var originalTitle = ""
    @State private var originalText = ""
    @State private var discard = false
    private var dirty: Bool { title != originalTitle || text != originalText }
    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 18) {
                TextField("标题", text: $title).font(.title2.weight(.medium)).textFieldStyle(.plain)
                Divider()
                TextEditor(text: $text).font(.body).scrollContentBackground(.hidden)
                    .accessibilityLabel("笔记正文")
                if let error { Text(L(error)).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
                Text("正文更新会保留历史。").font(.caption).foregroundStyle(.secondary)
            }.padding(24).background(AppPalette.canvas).disabled(!loaded || saving)
                .navigationTitle(L(noteID == nil ? "新建笔记" : "编辑笔记"))
                #if os(iOS)
                .navigationBarTitleDisplayMode(.inline)
                #endif
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("取消") { if dirty { discard = true } else { dismiss() } }.disabled(saving) }
                    ToolbarItem(placement: .confirmationAction) {
                        Button(saving ? L("正在保存") : L("保存")) { Task { await save() } }
                            .keyboardShortcut("s", modifiers: .command)
                            .disabled(!loaded || saving || title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !dirty)
                    }
                }
                .confirmationDialog("放弃未保存的修改？", isPresented: $discard, titleVisibility: .visible) { Button("放弃修改", role: .destructive) { dismiss() } }
        }.interactiveDismissDisabled(dirty).task { await load() }
            .protectsUnsavedEdits(in: workspace, editing: dirty || saving)
        #if os(macOS)
        .frame(minWidth: 640, idealWidth: 800, minHeight: 540, idealHeight: 700)
        #endif
    }
    private func load() async {
        guard let noteID else { loaded = true; return }
        do {
            guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
            let note = try await client.read("v1/notes/" + noteID.uuidString, as: PersonalNote.self)
            title = note.title; text = note.text ?? ""; etag = note.etag ?? ""
            originalTitle = title; originalText = text; loaded = true
        } catch { self.error = error.localizedDescription }
    }
    private func save() async {
        guard let client = workspace.client else { return }
        saving = true; defer { saving = false }
        do {
            try await client.send(noteID.map { "v1/notes/" + $0.uuidString } ?? "v1/notes", method: noteID == nil ? "POST" : "PUT",
                body: ["id": createdID.uuidString, "title": title, "text": text, "etag": etag])
            await workspace.refresh(); onSaved?(noteID ?? createdID); dismiss()
        } catch { self.error = error.localizedDescription }
    }
}
