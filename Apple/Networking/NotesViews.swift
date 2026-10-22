import SwiftUI

struct NotesLibrary: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    var initialSelection: UUID? = nil
    @StateObject private var loader = LibraryLoader<PersonalNote>()
    @State private var filter = LibraryFilter()
    @State private var selection: UUID?
    @State private var creating = false
    @State private var managing = false
    @FocusState private var searching: Bool
    private var emptyTitle: String { filter.hasSearchFilters || filter.archived ? "没有匹配的笔记" : "还没有笔记" }
    var body: some View {
        Group {
            #if os(macOS)
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
            }
            #else
            list.navigationDestination(for: UUID.self) { id in
                if let item = loader.items.first(where: { $0.id == id }) { NoteReader(workspace: workspace, note: item) }
            }
            #endif
        }.background(AppPalette.canvas)
            .task(id: filter.signature + workspace.endpoint + String(workspace.connected) + workspace.libraryRevision.uuidString) { await reload() }
            .onChange(of: initialSelection) { _, id in if let id { selection = id } }
            .sheet(isPresented: $creating, onDismiss: { Task { await reload(); await workspace.refresh() } }) {
                NoteEditor(workspace: workspace, noteID: nil).appLanguage()
            }
            .sheet(isPresented: $managing, onDismiss: { Task { await reload() } }) { CategoryManager(store: workspace, kind: "note").appLanguage() }
            .background(Button("") { searching = true }.keyboardShortcut("f", modifiers: .command).hidden())
    }
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
            List(selection: $selection) {
                ForEach(loader.items) { item in
                    #if os(macOS)
                    row(item).tag(item.id).listRowSeparator(.hidden)
                    #else
                    NavigationLink(value: item.id) { row(item) }
                    #endif
                }
            }
            #if os(macOS)
            .listStyle(.inset)
            #else
            .listStyle(.plain)
            #endif
            .scrollContentBackground(.hidden).tint(.gray)
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
            HStack {
                Text(verbatim: item.day ?? "")
                Spacer(minLength: 8)
                if let organization = item.organization, !organization.categoryID.isEmpty || !organization.tags.isEmpty {
                    OrganizationBadges(organization: organization, categories: loader.facets.categories)
                }
            }
                .font(.caption).foregroundStyle(.secondary)
        }.padding(.vertical, 8).padding(.horizontal, 4)
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
    @State private var error: String?
    @State private var proposing: KnowledgeSource?
    @State private var proposalSaved = false
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Label("笔记", systemImage: "note.text").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("整理分类") { organizing = true }
                Button("编辑") { editing = true }
            }.buttonStyle(.borderless).padding(22)
            Divider().opacity(0.5)
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    Text(verbatim: document?.title ?? note.title).font(.largeTitle.weight(.medium)).textSelection(.enabled)
                    Text(verbatim: note.day ?? "").font(.caption).foregroundStyle(.secondary)
                    if let document { MarkdownReader(text: document.text ?? "") }
                    else if error == nil { ProgressView() }
                    if let document, !(document.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
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
                }.padding(32).frame(maxWidth: 850, alignment: .leading).frame(maxWidth: .infinity)
            }
        }.task { await load() }.sourceLinks(workspace: workspace)
            .sheet(isPresented: $editing, onDismiss: { Task { await load(); await workspace.refresh() } }) { NoteEditor(workspace: workspace, noteID: note.id).appLanguage() }
            .sheet(isPresented: $organizing) { OrganizationEditor(store: workspace, kind: "note", id: note.id, initial: note.organization ?? .init()).appLanguage() }
            .sheet(item: $proposing) { source in
                MemoryProposalSheet(workspace: workspace, source: source, sourceText: source.text ?? "") { proposalSaved = true }.appLanguage()
            }
    }
    private func load() async {
        do {
            guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
            document = try await client.read("v1/notes/" + note.id.uuidString, as: PersonalNote.self); error = nil
        } catch { self.error = error.localizedDescription }
    }
}

struct NoteEditor: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let noteID: UUID?
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
                Text("保存为文字文件，保留每次修改的历史版本。").font(.caption).foregroundStyle(.secondary)
            }.padding(24).background(AppPalette.canvas).disabled(!loaded || saving)
                .navigationTitle(L(noteID == nil ? "新建笔记" : "编辑笔记"))
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
            await workspace.refresh(); dismiss()
        } catch { self.error = error.localizedDescription }
    }
}
