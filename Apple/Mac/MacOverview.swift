import SwiftUI

struct MacOverview: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    var showNotes: (UUID?) -> Void
    var showMemory: () -> Void
    var showDaily: () -> Void
    @StateObject private var notes = LibraryLoader<PersonalNote>()
    @State private var creating = false
    var body: some View {
        GeometryReader { geometry in
            HStack(spacing: 0) {
                ConversationView(workspace: workspace).frame(maxWidth: .infinity, maxHeight: .infinity)
                if geometry.size.width >= 1020 {
                    Divider()
                    context.frame(width: min(420, max(290, geometry.size.width * 0.24)))
                }
            }
        }.sheet(isPresented: $creating, onDismiss: { Task { await reload() } }) { NoteEditor(workspace: workspace, noteID: nil).appLanguage() }
            .task(id: workspace.endpoint + String(workspace.connected) + workspace.libraryRevision.uuidString) { await reload() }
    }
    private var context: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 30) {
                HStack { Text("你的资料库").font(.headline); Spacer(); BrandMark(size: 28) }
                Text(Date.now, format: .dateTime.year().month().day().weekday())
                    .font(.caption).foregroundStyle(.secondary).padding(.top, -17)
                VStack(spacing: 16) {
                    contextRow("笔记", count: workspace.overview?.noteCount ?? 0, symbol: "note.text", action: { showNotes(nil) })
                    contextRow("每日记录", count: workspace.overview?.dayCount ?? 0, symbol: "calendar", action: showDaily)
                    contextRow("长期记忆", count: workspace.overview?.memoryCount ?? 0, symbol: "bookmark", action: showMemory)
                }
                Divider()
                HStack {
                    Text("最近的笔记").font(.subheadline.weight(.semibold))
                    Spacer()
                    Button { creating = true } label: { Image(systemName: "plus") }.help("新建笔记").accessibilityLabel("新建笔记").disabled(!workspace.connected)
                }
                if notes.items.isEmpty {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("一个想法，也值得留下。").font(.body)
                        Text("文字和声音，都可以成为日后的线索。").font(.subheadline).foregroundStyle(.secondary).lineSpacing(4)
                        Button("写下第一篇笔记") { creating = true }.buttonStyle(.bordered).disabled(!workspace.connected)
                    }.padding(18).frame(maxWidth: .infinity, alignment: .leading).background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 14))
                }
                ForEach(notes.items.prefix(4)) { item in
                    Button(action: { showNotes(item.id) }) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(verbatim: item.title).font(.subheadline.weight(.medium)).lineLimit(2)
                            Text(verbatim: item.preview).font(.caption).foregroundStyle(.secondary).lineLimit(3).lineSpacing(3)
                            Text(verbatim: item.day ?? "").font(.caption2).foregroundStyle(.secondary)
                        }.frame(maxWidth: .infinity, alignment: .leading).foregroundStyle(.primary)
                    }.buttonStyle(.plain)
                }
                Divider()
                VStack(alignment: .leading, spacing: 12) {
                    Label("长期记忆", systemImage: "bookmark").font(.subheadline.weight(.semibold))
                    Text("从记录中留下稳定、有用的事实。由你确认，才能成为记忆。")
                        .font(.subheadline).foregroundStyle(.secondary).lineSpacing(5)
                    Button("查看记忆", action: showMemory).buttonStyle(.borderless)
                }
            }.padding(26)
        }.background(AppPalette.surface.opacity(0.38))
    }
    private func contextRow(_ title: String, count: Int, symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack { Label(LocalizedStringKey(title), systemImage: symbol); Spacer(); Text(count, format: .number).monospacedDigit().foregroundStyle(.secondary); Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary) }
                .font(.subheadline).foregroundStyle(.primary)
        }.buttonStyle(.plain)
    }
    private func reload() async { await notes.reload(client: workspace.client, kind: "note", filter: .init()); await workspace.refresh() }
}
