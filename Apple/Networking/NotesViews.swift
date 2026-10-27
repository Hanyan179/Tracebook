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
    @State private var draft = PersonalNoteDraft()
    @State private var context: EditorDraftContext?
    @State private var loaded = false
    @State private var saving = false
    @State private var error: String?
    @State private var discard = false
    @State private var reloading = false
    private var dirty: Bool { draft.needsRecovery }
    var body: some View {
        NavigationStack {
            GeometryReader { geometry in
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        TextField("标题", text: $draft.title, axis: .vertical).font(.title2.weight(.medium)).textFieldStyle(.plain)
                            .disabled(!loaded || saving)
                        Divider()
                        TextEditor(text: $draft.text).font(.body).scrollContentBackground(.hidden)
                            .accessibilityLabel("笔记正文").disabled(!loaded || saving)
                            .frame(minHeight: max(240, geometry.size.height - 230))
                        if let error { Text(L(error)).font(.caption).foregroundStyle(.secondary).textSelection(.enabled).fixedSize(horizontal: false, vertical: true) }
                        if !loaded {
                            if error != nil { Button("重新读取") { Task { await load() } } }
                            else { ProgressView() }
                        }
                        if dirty {
                            Text("未提交的修改会保留为本机草稿").font(.caption).foregroundStyle(.secondary)
                            if !draft.etag.isEmpty {
                                Button("放弃本机草稿并重新读取") { reloading = true }.font(.caption).disabled(saving)
                            }
                        } else { Text("正文更新会保留历史。").font(.caption).foregroundStyle(.secondary) }
                    }.frame(maxWidth: .infinity, alignment: .leading).padding(24)
                }
            }.background(AppPalette.canvas)
                .navigationTitle(L(noteID == nil && draft.etag.isEmpty ? "新建笔记" : "编辑笔记"))
                #if os(iOS)
                .navigationBarTitleDisplayMode(.inline)
                #endif
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("取消") { if dirty { discard = true } else { dismiss() } }.disabled(saving)
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        Button(saving ? L("正在保存") : L("保存")) { Task { await save() } }
                            .keyboardShortcut("s", modifiers: .command)
                            .disabled(!loaded || saving || draft.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || draft.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !dirty)
                    }
                }
                .confirmationDialog("关闭未保存的笔记？", isPresented: $discard, titleVisibility: .visible) {
                    Button("保留草稿并关闭") { if persistDraft() { dismiss() } }
                    Button("放弃修改", role: .destructive) { if removeDraft() { loaded = false; dismiss() } }
                    Button("取消", role: .cancel) { }
                }
                .confirmationDialog("放弃尚未提交的本机修改？", isPresented: $reloading, titleVisibility: .visible) {
                    Button("重新读取", role: .destructive) {
                        let savedID = draft.etag.isEmpty ? nil : draft.id
                        if removeDraft() { loaded = false; Task { await load(reloadID: savedID) } }
                    }
                    Button("取消", role: .cancel) { }
                }
        }.interactiveDismissDisabled(dirty || saving).task { await load() }
            .onChange(of: draft) { _, _ in if loaded { persistDraft() } }
            .onDisappear { if loaded { persistDraft() } }
            .protectsUnsavedEdits(in: workspace, editing: dirty || saving)
        #if os(macOS)
        .frame(minWidth: 640, idealWidth: 800, minHeight: 540, idealHeight: 700)
        #endif
    }
    private func checkConnection(_ revision: UUID, _ expected: EditorDraftContext) throws {
        guard !Task.isCancelled, revision == workspace.connectionRevision, workspace.endpoint == expected.endpoint else {
            throw NodeFailure(message: "资料库已变化，草稿仍保留。请回到原资料库核对。")
        }
    }
    private func readNote(_ client: NodeClient, id: UUID, expected: EditorDraftContext) async throws -> PersonalNote {
        let note = try await client.read("v1/notes/" + id.uuidString, as: PersonalNote.self)
        guard note.nodeID == expected.nodeID else {
            throw NodeFailure(message: "资料库已变化，草稿仍保留。请回到原资料库核对。")
        }
        return note
    }
    private func load(reloadID: UUID? = nil) async {
        let requestedID = reloadID ?? noteID
        let path = "note-drafts/" + (requestedID?.uuidString ?? "new")
        let endpoint = workspace.endpoint
        let revision = workspace.connectionRevision
        loaded = false; error = nil; context = nil
        do {
            guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
            let identity = try await client.read("v1/status", as: NodeStatus.self)
            var next = EditorDraftContext(endpoint: endpoint, nodeID: identity.nodeID, path: path)
            try checkConnection(revision, next)
            guard identity.noteIdentitySupported == true else { throw NodeFailure(message: "请更新电脑端后继续编辑笔记，已有草稿仍保留。") }
            context = next
            var saved = try PersonalNoteDraft.read(from: next.url)
            // A creation may already appear in the list while its reply was lost.
            if saved == nil, let requestedID {
                let pending = EditorDraftContext(endpoint: endpoint, nodeID: identity.nodeID, path: "note-drafts/new")
                if let candidate = try? PersonalNoteDraft.read(from: pending.url), candidate.id == requestedID {
                    next = pending; context = pending; saved = candidate
                }
            }
            var value = saved ?? PersonalNoteDraft(id: requestedID ?? UUID())
            if let requestedID, value.id != requestedID { throw CocoaError(.fileReadCorruptFile) }
            if requestedID != nil || !value.etag.isEmpty || value.submittedText != nil {
                do {
                    let note = try await readNote(client, id: value.id, expected: next)
                    try checkConnection(revision, next)
                    if saved == nil { value = PersonalNoteDraft(id: note.id, title: note.title, text: note.text ?? "", etag: note.etag ?? "") }
                    else if !value.acknowledge(title: note.title, text: note.text ?? "", etag: note.etag ?? ""), value.etag != note.etag {
                        error = "文件已更新，你的草稿仍保留。请先核对版本。"
                    }
                } catch let failure as NodeFailure where failure.statusCode == 404 && requestedID == nil && value.etag.isEmpty { }
            }
            try checkConnection(revision, next)
            draft = value
            if saved != nil && error == nil { error = "已恢复本机未提交草稿。" }
            try next.rememberIdentity()
            loaded = true; persistDraft()
        } catch {
            guard !Task.isCancelled, revision == workspace.connectionRevision else { return }
            self.error = error.localizedDescription
            // An offline fallback uses only the last verified identity; never another library's draft.
            if context == nil { context = EditorDraftContext.cached(endpoint: endpoint, path: path) }
            if let context {
                do {
                    if let saved = try PersonalNoteDraft.read(from: context.url), requestedID == nil || saved.id == requestedID {
                        draft = saved; loaded = true
                    } else if requestedID == nil {
                        draft = PersonalNoteDraft(); loaded = true
                    }
                } catch { self.error = L("本机草稿文件暂时无法读取，原件仍保留。") }
            }
        }
    }
    private func save() async {
        guard let context, let client = workspace.client, persistDraft() else { return }
        let revision = workspace.connectionRevision
        saving = true; defer { saving = false }
        do {
            try checkConnection(revision, context)
            let identity = try await client.read("v1/status", as: NodeStatus.self)
            try checkConnection(revision, context)
            guard context.nodeID == identity.nodeID else { throw NodeFailure(message: "资料库已变化，草稿仍保留。请回到原资料库核对。") }
            guard identity.noteIdentitySupported == true else { throw NodeFailure(message: "请更新电脑端后继续编辑笔记，已有草稿仍保留。") }
            if draft.submittedText != nil {
                do {
                    let note = try await readNote(client, id: draft.id, expected: context)
                    try checkConnection(revision, context)
                    if !draft.acknowledge(title: note.title, text: note.text ?? "", etag: note.etag ?? ""), note.etag == draft.etag {
                        draft.submittedTitle = nil; draft.submittedText = nil
                    }
                } catch let failure as NodeFailure where failure.statusCode == 404 && draft.etag.isEmpty {
                    draft.submittedTitle = nil; draft.submittedText = nil
                }
            }
            if draft.hasChanges {
                draft.prepareSubmission()
                guard persistDraft() else { return }
                let result = try await client.send(draft.etag.isEmpty ? "v1/notes" : "v1/notes/" + draft.id.uuidString,
                    method: draft.etag.isEmpty ? "POST" : "PUT",
                    body: ["id": draft.id.uuidString, "title": draft.title, "text": draft.text, "etag": draft.etag, "nodeID": context.nodeID])
                try checkConnection(revision, context)
                let note = try JSONDecoder().decode(PersonalNote.self, from: result)
                guard note.nodeID == context.nodeID, note.id == draft.id,
                      draft.acknowledge(title: note.title, text: note.text ?? "", etag: note.etag ?? "") else {
                    throw NodeFailure(message: "保存结果尚未核对，草稿仍保留。请重试核对。")
                }
            }
            guard !draft.needsRecovery else { throw NodeFailure(message: "保存结果尚未核对，草稿仍保留。请重试核对。") }
            guard removeDraft() else { return }
            loaded = false
            await workspace.refresh(); onSaved?(draft.id); dismiss()
        } catch { self.error = error.localizedDescription; persistDraft() }
    }
    @discardableResult
    private func persistDraft() -> Bool {
        guard loaded, let context else { return false }
        guard dirty else { return removeDraft() }
        do { try draft.write(to: context.url); return true }
        catch { self.error = "草稿无法保存在本机。请保存或复制内容后再离开。"; return false }
    }
    @discardableResult
    private func removeDraft() -> Bool {
        guard let context else { return true }
        do {
            if FileManager.default.fileExists(atPath: context.url.path) { try FileManager.default.removeItem(at: context.url) }
            return true
        } catch { self.error = "本机草稿暂时无法移除，请重试。"; return false }
    }
}
