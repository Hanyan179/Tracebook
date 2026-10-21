import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct DocumentPage: Decodable, Sendable {
    let items: [KnowledgeSource]
    let count: Int
    let nextOffset: Int?
}

private struct DocumentPreview: Decodable, Identifiable, Sendable {
    let id: String
    let selected: Int
    let accepted: Int
    let added: Int
    let duplicates: Int
    let redacted: Int
    let titles: [String]
    let warnings: [Warning]
    let samples: [Sample]
    struct Warning: Decodable, Sendable {
        let name: String
        let reason: String
        var message: String {
            switch reason {
            case "unsupported": "暂不支持这个文件格式"
            case "too_large": "单份资料最多 2 MB"
            default: "文件为空、无法读取或不是 UTF-8 文字"
            }
        }
    }
    struct Sample: Decodable, Sendable { let name: String; let text: String }
}
private struct DocumentImportReport: Decodable, Sendable {
    let added: Int
    let skipped: Int
    let versions: Int
}
private struct DocumentPayload: Encodable, Sendable {
    let name: String
    let sourcePath: String
    var modifiedAt: String?
    var text: String?
    var error: String?
}

struct DocumentLibraryView: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @Environment(\.dismiss) private var dismiss
    @State private var items: [KnowledgeSource] = []
    @State private var count = 0
    @State private var nextOffset: Int?
    @State private var preview: DocumentPreview?
    @State private var selected: KnowledgeSource?
    @State private var busy = false
    @State private var problem: String?
    @State private var result: String?

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(LF("已导入 %d 份文字资料", count)).font(.headline)
                        Text("支持 Markdown 和 TXT。先预览，再保存到个人资料夹。").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("选择文件", systemImage: "plus") { choose() }.buttonStyle(FilledButtonStyle()).disabled(busy)
                }
                Text("原文件保持原位。相同文字不会重复导入；文件变化会另存版本，保留已有内容和人工修改。资料不会自动发送给模型或成为本人记忆。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                if let problem { Text(L(problem)).foregroundStyle(.secondary).textSelection(.enabled) }
                if let result { Text(verbatim: result).font(.callout) }
                if busy { ProgressView().controlSize(.small) }
                if items.isEmpty && !busy {
                    ContentUnavailableView("还没有导入资料", systemImage: "doc.text",
                        description: Text("选择已有的笔记、日记或其他文字文件。"))
                } else {
                    List {
                        ForEach(items) { item in
                            Button { selected = item } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 6) {
                                        Text(verbatim: item.title).font(.body)
                                        Text(verbatim: item.date).font(.caption).foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Image(systemName: "chevron.right").foregroundStyle(.secondary)
                                }.padding(.vertical, 6).contentShape(Rectangle())
                            }.buttonStyle(.plain)
                        }
                        if let offset = nextOffset {
                            Button("载入更多") { Task { await load(offset: offset) } }.disabled(busy)
                        }
                    }.listStyle(.inset)
                }
            }.padding(24).navigationTitle(L("文字资料"))
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() }.disabled(busy || preview != nil) } }
        }
        .frame(width: 760, height: 650)
        .interactiveDismissDisabled(busy || preview != nil)
        .task { await load() }
        .sheet(item: $selected) { KnowledgeSourceReader(workspace: workspace, source: $0).appLanguage() }
        .sheet(item: $preview) { value in
            NavigationStack {
                Form {
                    Section("导入预览") {
                        LabeledContent("已选择文件", value: String(value.selected))
                        LabeledContent("可新增资料", value: String(value.added))
                        LabeledContent("重复内容", value: String(value.duplicates))
                        if value.redacted > 0 { Text(LF("%d 份资料中的常见凭证已隐藏。", value.redacted)) }
                        ForEach(Array(value.titles.enumerated()), id: \.offset) { _, title in Text(verbatim: title).font(.caption) }
                    }
                    if !value.warnings.isEmpty {
                        Section("未导入的文件") {
                            ForEach(Array(value.warnings.enumerated()), id: \.offset) { _, warning in
                                Text(verbatim: warning.name + " · " + L(warning.message)).font(.caption)
                            }
                        }
                    }
                    Section("内容预览") {
                        ForEach(Array(value.samples.enumerated()), id: \.offset) { _, sample in
                            VStack(alignment: .leading, spacing: 6) {
                                Text(verbatim: sample.name).font(.headline)
                                Text(verbatim: sample.text).font(.caption).textSelection(.enabled)
                            }
                        }
                    }
                    Text("文件日期只用于归档，不代表内容发生时间；文件中的陈述不自动视为本人事实。")
                        .font(.caption).foregroundStyle(.secondary)
                    if let problem { Text(L(problem)).foregroundStyle(.secondary) }
                }.formStyle(.grouped).disabled(busy)
                    .navigationTitle(L("导入文字资料"))
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) { Button("取消") { Task { await cancel(value) } }.disabled(busy) }
                        ToolbarItem(placement: .confirmationAction) { Button("确认导入") { Task { await commit(value) } }.disabled(busy || value.accepted == 0) }
                    }
            }.frame(width: 700, height: 620).interactiveDismissDisabled().appLanguage()
        }
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.allowedContentTypes = [.plainText, UTType(filenameExtension: "md") ?? .plainText, UTType(filenameExtension: "markdown") ?? .plainText]
        panel.message = L("选择 Markdown 或 TXT 文件，每份最多 2 MB，每批最多 100 份。")
        guard panel.runModal() == .OK else { return }
        let urls = panel.urls
        guard urls.count <= 100 else { problem = "每次请选择 1 至 100 份文字资料。";return }
        busy = true;problem = nil;result = nil
        Task {
            defer { busy = false }
            do {
                guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
                let files = await Task.detached(priority: .userInitiated) { urls.map(Self.readFile) }.value
                struct Request: Encodable { let files: [DocumentPayload] }
                let data = try await client.sendData("v1/documents/preview", method: "POST", body: JSONEncoder().encode(Request(files: files)))
                preview = try JSONDecoder().decode(DocumentPreview.self, from: data)
            } catch { problem = error.localizedDescription }
        }
    }
    nonisolated private static func readFile(_ url: URL) -> DocumentPayload {
        var item = DocumentPayload(name: url.lastPathComponent, sourcePath: url.path)
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            let info = try url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey, .contentModificationDateKey])
            guard info.isRegularFile == true, info.isSymbolicLink != true else { item.error = "unreadable";return item }
            guard (info.fileSize ?? 0) <= 2 * 1024 * 1024 else { item.error = "too_large";return item }
            let handle = try FileHandle(forReadingFrom: url);defer { try? handle.close() }
            let data = try handle.read(upToCount: 2 * 1024 * 1024 + 1) ?? Data()
            guard data.count <= 2 * 1024 * 1024 else { item.error = "too_large";return item }
            item.text = String(data: data, encoding: .utf8)
            if let date = info.contentModificationDate { item.modifiedAt = ISO8601DateFormatter().string(from: date) }
            if item.text == nil { item.error = "unreadable" }
        } catch { item.error = "unreadable" }
        return item
    }
    private func load(offset: Int = 0) async {
        guard let client = workspace.client else { return }
        do {
            let page = try await client.read("v1/documents", query: ["offset": String(offset)], as: DocumentPage.self)
            items = offset == 0 ? page.items : items + page.items
            count = page.count;nextOffset = page.nextOffset
        } catch { problem = error.localizedDescription }
    }
    private func commit(_ value: DocumentPreview) async {
        guard let client = workspace.client else { return }
        busy = true;problem = nil;defer { busy = false }
        do {
            let data = try await client.sendData("v1/documents/"+value.id+"/commit", method: "POST", body: Data("{}".utf8))
            let report = try JSONDecoder().decode(DocumentImportReport.self, from: data)
            result = LF("新增 %d 份资料，跳过 %d 份重复内容，其中 %d 份为更新版本。", report.added, report.skipped, report.versions)
            preview = nil
            await load();await workspace.libraryChanged()
        } catch { problem = error.localizedDescription }
    }
    private func cancel(_ value: DocumentPreview) async {
        guard let client = workspace.client else { return }
        busy = true;defer { busy = false }
        do {
            _ = try await client.sendData("v1/documents/"+value.id+"/cancel", method: "POST", body: Data("{}".utf8))
            preview = nil;problem = nil
        } catch { problem = error.localizedDescription }
    }
}
