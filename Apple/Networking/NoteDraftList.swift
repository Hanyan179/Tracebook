import SwiftUI

struct NoteDraftList: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let scope: EditorDraftContext
    @Environment(\.dismiss) private var dismiss
    @State private var items: [LocalNoteDraft] = []
    @State private var next: NoteDraftCursor?
    @State private var unreadable = 0
    @State private var loading = false
    @State private var error: String?
    @State private var selection: LocalNoteDraft?
    @State private var creation: NoteDraftCreation?
    var body: some View {
        NavigationStack {
            ScrollView {
                // Draft previews are bounded; an eager stack avoids the macOS lazy-layout
                // accessibility trap when jumping to the end of this sheet.
                VStack(alignment: .leading, spacing: 18) {
                    Text("仅存本机；连接后保存到原资料库。")
                        .font(.caption).foregroundStyle(.secondary)
                    if let error { Text(L(error)).foregroundStyle(.secondary) }
                    if unreadable > 0 { Text("部分本机草稿文件无法读取，原件仍保留。").foregroundStyle(.secondary) }
                    ForEach(items) { item in
                        Button { selection = item } label: {
                            VStack(alignment: .leading, spacing: 8) {
                                Text(verbatim: item.draft.title.isEmpty ? L("未命名笔记") : item.draft.title).font(.headline).lineLimit(2)
                                if item.draft.isConflictCopy { Label("并发修改副本", systemImage: "doc.on.doc").font(.caption) }
                                Text(verbatim: String(item.draft.text.prefix(200))).lineLimit(2).foregroundStyle(.secondary)
                                Text(item.modifiedAt, style: .date).font(.caption).foregroundStyle(.secondary)
                            }.frame(maxWidth: .infinity, alignment: .leading).padding(16)
                                .background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 12))
                        }.buttonStyle(.plain)
                    }
                    if loading { ProgressView("正在读取资料") }
                    else if next != nil { Button("继续读取本机草稿") { Task { await load(more: true) } } }
                    else if items.isEmpty && error == nil && unreadable == 0 { Text("这份资料库没有本机草稿。").foregroundStyle(.secondary) }
                    if error != nil || unreadable > 0 { Button("重新读取") { Task { await load() } } }
                }.frame(maxWidth: .infinity, alignment: .leading).padding(24)
            }.background(AppPalette.canvas).navigationTitle(L("本机草稿"))
                #if os(iOS)
                .navigationBarTitleDisplayMode(.inline)
                #endif
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } }
                    ToolbarItem(placement: .primaryAction) {
                        Button("新建笔记", systemImage: "square.and.pencil") {
                            let id = UUID(); creation = NoteDraftCreation(id: id, context: scope.noteContext(id: id))
                        }
                    }
                }
        }.task { await load() }
            .sheet(item: $selection, onDismiss: { Task { await load() } }) { item in
                let context = item.url == scope.url ? scope : scope.noteContext(id: item.draft.storageID)
                NoteEditor(workspace: workspace, noteID: nil, initialContext: context, initialDraftID: item.draft.id).appLanguage()
            }
            .sheet(item: $creation, onDismiss: { Task { await load() } }) { creation in
                NoteEditor(workspace: workspace, noteID: nil, initialContext: creation.context, initialDraftID: creation.id).appLanguage()
            }
        #if os(macOS)
        .frame(minWidth: 520, idealWidth: 640, minHeight: 480, idealHeight: 640)
        #endif
    }
    private func load(more: Bool = false) async {
        guard !loading else { return }
        loading = true; defer { loading = false }
        if !more { items = []; next = nil; unreadable = 0 }
        let directory = EditorDraftContext.noteDraftDirectory
        let endpoint = scope.endpoint, nodeID = scope.nodeID, cursor = more ? next : nil
        do {
            let page = try await Task.detached {
                try PersonalNoteDraftCatalog.read(directory: directory, endpoint: endpoint, nodeID: nodeID, after: cursor)
            }.value
            guard !Task.isCancelled else { return }
            items.append(contentsOf: page.items.filter { candidate in !items.contains(where: { $0.id == candidate.id }) })
            items.sort { $0.modifiedAt > $1.modifiedAt }
            next = page.next; unreadable += page.unreadable; error = nil
        } catch { self.error = "草稿目录暂时无法完整读取，请重新读取。原件仍保留。"; next = nil }
    }
}
