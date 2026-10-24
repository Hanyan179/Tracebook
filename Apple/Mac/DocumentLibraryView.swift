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
    let pdfs: [PDFCoverage]?
    let docxDocuments: [DOCXCoverage]?
    let sourceRoles: [SourceRole]?
    struct SourceRole: Decodable, Sendable { let name: String; let contentRole: String }
    struct PDFCoverage: Decodable, Sendable {
        let name: String
        let pages: Int
        let textPages: Int
        let emptyPages: [Int]
        let failedPages: [Int]
        let parserWarnings: Bool
    }
    struct DOCXCoverage: Decodable, Sendable {
        let name: String
        let paragraphs: Int
        let tables: Int
        let parts: [DocumentPart]
        let blocks: Int
        let warnings: [DocumentStructureWarning]
    }
    struct Warning: Decodable, Sendable {
        let name: String
        let reason: String
        var message: String {
            switch reason {
            case "unsupported": "暂不支持这个文件格式"
            case "too_large": "单份资料最多 2 MB"
            case "encrypted": "PDF 已加密，请先导出未加密副本"
            case "invalid_pdf": "PDF 损坏或结构不受支持"
            case "invalid_docx": "DOCX 损坏或结构不受支持"
            case "encrypted_docx": "DOCX 已加密，请先导出未加密副本"
            case "unsupported_docx": "请导出为标准 DOCX 文档，不支持启用宏的格式"
            case "page_limit": "PDF 最多 200 页，请拆分后导入"
            case "text_limit": "提取文字过大，请拆分文件后导入"
            case "no_text": "未提取到文字，可能为空白页或扫描件"
            case "parser_timeout": "解析超时，请拆分文件后重试"
            case "parser_limit": "解析超过资源限制，请拆分文件后重试"
            case "parser_unavailable": "资料解析组件不可用，请更新电脑应用"
            case "parser_busy": "其他资料正在解析，请稍后重试"
            case "not_processed", "batch_limit": "本批未处理，请单独选择后重试"
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
    var data: String?
    var error: String?
    var contentRole: String?
}
private struct DocumentPreparation: Decodable, Sendable {
    let id: String
    let state: String
    let completed: Int
    let total: Int
    let currentFile: String?
    let problem: String?
    let preview: DocumentPreview?
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
    @State private var preparingID: String?
    @State private var preparation: DocumentPreparation?
    @State private var reread = 0
    @State private var cancelling = false
    @State private var contentRole = DocumentContentRole.unknown
    private var savedPreparation: String { "documents.preparation." + workspace.endpoint }

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(LF("已导入 %d 份文字资料", count)).font(.headline)
                        Text("支持 DOCX、PDF、Markdown 和 TXT。先预览，再保存提取文字。").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("选择文件", systemImage: "plus") { choose() }.buttonStyle(FilledButtonStyle()).disabled(busy || preparingID != nil)
                }
                Text("原文件保持原位。同一份资料不会重复导入；文件变化会另存版本，保留已有内容和人工修改。资料不会自动发送给模型或成为本人记忆。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                VStack(alignment: .leading, spacing: 6) {
                    Picker("这批资料的性质", selection: $contentRole) {
                        ForEach(DocumentContentRole.allCases, id: \.self) { role in Text(L(role.title)).tag(role) }
                    }.pickerStyle(.menu).disabled(busy || preparingID != nil)
                    Text("选择适用于本批所有文件的性质；混合资料请分批导入，无法确定时保留未确认。更正性质会另存版本。")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                if let problem { Text(L(problem)).foregroundStyle(.secondary).textSelection(.enabled) }
                if let result { Text(verbatim: result).font(.callout) }
                if busy { ProgressView().controlSize(.small) }
                if let preparingID {
                    if let preparation {
                        Text(LF("已检查 %d / %d 份文件", preparation.completed, preparation.total)).font(.callout)
                        if let name = preparation.currentFile { Text(verbatim: name).font(.caption).lineLimit(2) }
                    }
                    HStack {
                        Button("停止解析") { Task { await stopPreparation(preparingID) } }.disabled(cancelling)
                        if !busy { Button("重新读取解析状态") { reread += 1 } }
                    }
                    Text("解析只在这台电脑进行，尚未导入资料。停止后原文件保持原位。").font(.caption).foregroundStyle(.secondary)
                }
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
                                        Text(L(item.documentRole.title)).font(.caption).foregroundStyle(.secondary)
                                        DocumentRevisionNotice(source: item)
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
        .frame(minWidth: 580, idealWidth: 760, minHeight: 540, idealHeight: 650)
        .interactiveDismissDisabled(busy || preview != nil)
        .task(id: workspace.endpoint) {
            await load()
            if preview == nil, preparingID == nil { preparingID = UserDefaults.standard.string(forKey: savedPreparation) }
        }
        .task(id: (preparingID ?? "") + workspace.endpoint + String(reread)) {
            if let preparingID { await monitor(preparingID) }
        }
        .onChange(of: workspace.endpoint) { _, _ in
            preparingID = nil;preparation = nil;preview = nil;busy = false
            problem = "资料库连接已变化，请重新选择文件。"
        }
        .sheet(item: $selected) { KnowledgeSourceReader(workspace: workspace, source: $0).appLanguage() }
        .sheet(item: $preview) { value in
            NavigationStack {
                Form {
                    Section("导入预览") {
                        LabeledContent("已选择文件", value: String(value.selected))
                        LabeledContent("可新增资料", value: String(value.added))
                        LabeledContent("重复内容", value: String(value.duplicates))
                        if value.redacted > 0 { Text(LF("%d 份资料中的常见凭证已隐藏。", value.redacted)) }
                        if let roles = value.sourceRoles {
                            ForEach(Array(roles.enumerated()), id: \.offset) { _, item in
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(verbatim: item.name)
                                    Text(L((DocumentContentRole(rawValue: item.contentRole) ?? .unknown).title)).foregroundStyle(.secondary)
                                }.font(.caption)
                            }
                        } else {
                            ForEach(Array(value.titles.enumerated()), id: \.offset) { _, title in Text(verbatim: title).font(.caption) }
                        }
                    }
                    if !value.warnings.isEmpty {
                        Section("未导入的文件") {
                            ForEach(Array(value.warnings.enumerated()), id: \.offset) { _, warning in
                                Text(verbatim: warning.name + " · " + L(warning.message)).font(.caption)
                            }
                        }
                    }
                    if let pdfs = value.pdfs, !pdfs.isEmpty {
                        Section("PDF 提取范围") {
                            ForEach(Array(pdfs.enumerated()), id: \.offset) { _, pdf in
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(verbatim: pdf.name).font(.headline)
                                    Text(LF("共 %d 页，其中 %d 页提取到文字。", pdf.pages, pdf.textPages))
                                    if !pdf.emptyPages.isEmpty { Text(LF("未提取到文字的页：%@", pdf.emptyPages.map(String.init).joined(separator: ", "))) }
                                    if !pdf.failedPages.isEmpty { Text(LF("解析失败的页：%@", pdf.failedPages.map(String.init).joined(separator: ", "))) }
                                    if pdf.parserWarnings { Text("解析器报告了格式问题，请对照原文件核查。") }
                                }.font(.callout).fixedSize(horizontal: false, vertical: true)
                            }
                            Text("保留文件页序，不识别图片中的文字。阅读顺序与表格可能需要校对；原文件留在原位，资料夹只保存提取文字。")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                    if let documents = value.docxDocuments, !documents.isEmpty {
                        Section("DOCX 提取范围") {
                            ForEach(Array(documents.enumerated()), id: \.offset) { _, document in
                                VStack(alignment: .leading, spacing: 8) {
                                    Text(verbatim: document.name).font(.headline)
                                    DocumentStructureCoverage(parts: document.parts, blockCount: document.blocks, warnings: document.warnings)
                                }.font(.callout)
                            }
                            Text("原文件保持原位，资料夹仅保存提取文字。").font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                    if !value.samples.isEmpty { Section("内容预览") {
                        ForEach(Array(value.samples.enumerated()), id: \.offset) { _, sample in
                            VStack(alignment: .leading, spacing: 6) {
                                Text(verbatim: sample.name).font(.headline)
                                Text(verbatim: sample.text).font(.caption).textSelection(.enabled)
                            }
                        }
                    } }
                    Text("文件日期只用于归档，不代表内容发生时间；文件中的陈述不自动视为本人事实。")
                        .font(.caption).foregroundStyle(.secondary)
                    if let problem { Text(L(problem)).foregroundStyle(.secondary) }
                }.formStyle(.grouped).disabled(busy)
                    .navigationTitle(L("导入文字资料"))
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) { Button("取消") { Task { await cancel(value) } }.disabled(busy) }
                        ToolbarItem(placement: .confirmationAction) { Button("确认导入") { Task { await commit(value) } }.disabled(busy || value.accepted == 0) }
                    }
            }.frame(minWidth: 580, idealWidth: 700, minHeight: 540, idealHeight: 650).interactiveDismissDisabled().appLanguage()
        }
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.allowedContentTypes = [.pdf, .plainText, UTType(filenameExtension: "docx") ?? .data, UTType(filenameExtension: "md") ?? .plainText, UTType(filenameExtension: "markdown") ?? .plainText]
        panel.message = L("选择 DOCX、PDF、Markdown 或 TXT，每份最多 2 MB；PDF 最多 200 页。不识别图片中的文字。")
        guard panel.runModal() == .OK else { return }
        let urls = panel.urls
        guard urls.count <= 100 else { problem = "每次请选择 1 至 100 份文字资料。";return }
        busy = true;problem = nil;result = nil
        let target = workspace.endpoint
        let selectedRole = contentRole.rawValue
        Task {
            defer { if workspace.endpoint == target, preparingID == nil { busy = false } }
            do {
                guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
                var files = await Task.detached(priority: .userInitiated) { Self.readFiles(urls) }.value
                for index in files.indices { files[index].contentRole = selectedRole }
                guard client.endpoint == workspace.client?.endpoint else { throw NodeFailure(message: "资料库连接已变化，请重新选择文件。") }
                let id = UUID().uuidString
                struct Request: Encodable { let requestID: String; let files: [DocumentPayload] }
                let body = try JSONEncoder().encode(Request(requestID: id, files: files))
                guard body.count <= 16 * 1024 * 1024 else { throw NodeFailure(message: "本批资料超过 12 MB，请分批导入。") }
                UserDefaults.standard.set(id, forKey: savedPreparation)
                do { _ = try await client.sendData("v1/documents/prepare", method: "POST", body: body) }
                catch {
                    guard client.endpoint == workspace.client?.endpoint else { return }
                    preparingID = id;throw error
                }
                guard client.endpoint == workspace.client?.endpoint else { return }
                preparingID = id
            } catch { if workspace.endpoint == target { problem = error.localizedDescription } }
        }
    }
    nonisolated private static func readFiles(_ urls: [URL]) -> [DocumentPayload] {
        var bytes = 0
        return urls.map { url in
            guard bytes < 10 * 1024 * 1024 else {
                return DocumentPayload(name: url.lastPathComponent, sourcePath: url.path, error: "batch_limit")
            }
            var value = readFile(url)
            bytes += (value.text?.utf8.count ?? 0) + (value.data?.utf8.count ?? 0) * 3 / 4
            if bytes > 10 * 1024 * 1024 { value.text = nil;value.data = nil;value.error = "batch_limit" }
            return value
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
            if ["pdf", "docx"].contains(url.pathExtension.lowercased()) { item.data = data.base64EncodedString() }
            else { item.text = String(data: data, encoding: .utf8) }
            if let date = info.contentModificationDate { item.modifiedAt = ISO8601DateFormatter().string(from: date) }
            if item.text == nil && item.data == nil { item.error = "unreadable" }
        } catch { item.error = "unreadable" }
        return item
    }
    private func monitor(_ id: String) async {
        guard let client = workspace.client else { return }
        busy = true;problem = nil
        defer {
            if client.endpoint == workspace.client?.endpoint, preparingID == id || preparingID == nil { busy = false }
        }
        do {
            repeat {
                let state = try await client.read("v1/documents/" + id + "/preparation", as: DocumentPreparation.self)
                try Task.checkCancellation()
                guard preparingID == id, client.endpoint == workspace.client?.endpoint else { return }
                preparation = state
                if state.state == "ready" {
                    guard !cancelling else { return }
                    preview = state.preview;preparingID = nil;preparation = nil;return
                }
                guard state.state == "preparing" else {
                    problem = state.problem ?? (state.state == "interrupted"
                        ? "上次资料解析被中断，原文件未改变，请重新选择文件。"
                        : "这次资料解析已结束，请重新选择文件。")
                    UserDefaults.standard.removeObject(forKey: savedPreparation)
                    preparingID = nil;preparation = nil;return
                }
                try await Task.sleep(for: .seconds(1))
            } while !Task.isCancelled
        } catch is CancellationError { return }
        catch { preparation = nil;problem = "无法读取资料解析状态，请检查连接后重新读取或停止解析。" }
    }
    private func stopPreparation(_ id: String) async {
        guard let client = workspace.client else { return }
        let key = savedPreparation
        cancelling = true;defer { cancelling = false }
        do {
            _ = try await client.sendData("v1/documents/" + id + "/cancel", method: "POST", body: Data("{}".utf8))
            UserDefaults.standard.removeObject(forKey: key)
            guard client.endpoint == workspace.client?.endpoint else { return }
            if preview?.id == id { preview = nil }
            preparingID = nil;preparation = nil;busy = false;problem = nil;result = L("解析已停止，未导入资料。")
        } catch { problem = error.localizedDescription }
    }
    private func load(offset: Int = 0) async {
        guard let client = workspace.client else { return }
        do {
            let page = try await client.read("v1/documents", query: ["offset": String(offset)], as: DocumentPage.self)
            guard client.endpoint == workspace.client?.endpoint else { return }
            items = offset == 0 ? page.items : items + page.items
            count = page.count;nextOffset = page.nextOffset
        } catch { problem = error.localizedDescription }
    }
    private func commit(_ value: DocumentPreview) async {
        guard let client = workspace.client else { return }
        let key = savedPreparation
        busy = true;problem = nil;defer { busy = false }
        do {
            let data = try await client.sendData("v1/documents/"+value.id+"/commit", method: "POST", body: Data("{}".utf8))
            let report = try JSONDecoder().decode(DocumentImportReport.self, from: data)
            UserDefaults.standard.removeObject(forKey: key)
            guard client.endpoint == workspace.client?.endpoint else { return }
            result = LF("新增 %d 份资料，跳过 %d 份重复内容，其中 %d 份为更新版本。", report.added, report.skipped, report.versions)
            preview = nil
            await load();await workspace.libraryChanged()
        } catch { problem = error.localizedDescription }
    }
    private func cancel(_ value: DocumentPreview) async {
        guard let client = workspace.client else { return }
        let key = savedPreparation
        busy = true;defer { busy = false }
        do {
            _ = try await client.sendData("v1/documents/"+value.id+"/cancel", method: "POST", body: Data("{}".utf8))
            UserDefaults.standard.removeObject(forKey: key)
            guard client.endpoint == workspace.client?.endpoint else { return }
            preview = nil;problem = nil
        } catch { problem = error.localizedDescription }
    }
}
