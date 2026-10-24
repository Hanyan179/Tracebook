import AppKit
import SwiftUI
import UniformTypeIdentifiers

private struct ImportPreview: Decodable, Identifiable, Sendable {
    let id: String
    let alreadyImported: Bool
    let conversations: Int
    let notes: Int
    let messages: Int
    let otherBranchMessages: Int
    let nonTextMessages: Int
    let warningCount: Int
    let firstDay: String
    let lastDay: String
    var titles: [String]?
}
private struct ImportReport: Decodable, Sendable {
    let added: Int
    let updated: Int
    let skipped: Int
    let conflicts: Int
}

struct DataTransferSection: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @State private var busy = false
    @State private var preview: ImportPreview?
    @State private var result: String?
    var body: some View {
        Section("导入与导出") {
            HStack {
                Label("聊天记录", systemImage: "bubble.left.and.text.bubble.right")
                Spacer()
                Button("导入 ChatGPT 对话") { chooseImport() }.disabled(busy || !workspace.connected)
            }
            Text("导入你下载的对话文件，保留说话人、时间和来源。导入内容不会自动成为长期记忆。")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Label("自己的资料", systemImage: "square.and.arrow.up")
                Spacer()
                Button("导出资料包") { chooseExport() }.disabled(busy || !workspace.connected)
            }
            Text("包含转写、笔记、对话、每日记录与分析、记忆、提示词和聊天导入原文件；不包含原音、文档原件和应用密钥。")
                .font(.caption).foregroundStyle(.secondary)
            if busy { ProgressView().controlSize(.small) }
            if let result { Text(L(result)).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
        }.sheet(item: $preview) { item in
            NavigationStack {
                Form {
                    Section("导入预览") {
                        LabeledContent("对话", value: String(item.conversations))
                        LabeledContent("文字消息", value: String(item.messages))
                        LabeledContent("按日归档", value: LF("%d 篇笔记", item.notes))
                        LabeledContent("日期范围", value: item.firstDay + " — " + item.lastDay)
                    }
                    Section("导入规则") {
                        Text("保留当前选中的对话分支；原始文件单独留存，包含其他分支。图片、音频和附件不作文字导入。")
                        Text("重复导入会去重。已有人工修改不会覆盖，新版本另存供核对。")
                        if item.warningCount > 0 { Text(LF("有 %d 项格式或日期问题，相关消息将跳过。", item.warningCount)) }
                        if item.nonTextMessages > 0 { Text(LF("%d 条非文字消息未转换。", item.nonTextMessages)) }
                    }
                    if let titles = item.titles {
                        Section("内容预览") { ForEach(Array(titles.enumerated()), id: \.offset) { Text(verbatim: $0.element).lineLimit(2) } }
                    }
                    if item.alreadyImported { Text("这个文件已经导入，无需重复处理。") }
                    if let result { Text(L(result)).font(.callout).foregroundStyle(.secondary) }
                }.formStyle(.grouped).disabled(busy)
                    .navigationTitle(L("导入聊天记录"))
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) { Button("取消") { Task { await cancel(item) } }.disabled(busy) }
                        ToolbarItem(placement: .confirmationAction) { Button("确认导入") { Task { await commit(item) } }.disabled(busy || item.alreadyImported) }
                    }
            }.frame(width: 670, height: 650).interactiveDismissDisabled().appLanguage()
        }
    }
    private func chooseImport() {
        let panel = NSOpenPanel(); panel.allowedContentTypes = [.json, .zip]; panel.allowsMultipleSelection = false
        panel.message = L("选择 ChatGPT 导出的 ZIP 或 conversations.json。")
        guard panel.runModal() == .OK, let url = panel.url else { return }
        busy = true; result = nil
        Task {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() }; busy = false }
            do {
                guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
                let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                guard size > 0 else { throw NodeFailure(message: "文件是空的，请选择包含对话的文件。") }
                guard size <= 64 * 1024 * 1024 else { throw NodeFailure(message: "导入文件超过 64 MB，请分批导入。") }
                var request = client.request("v1/imports/preview", method: "POST")
                request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type"); request.timeoutInterval = 120
                let (data, response) = try await URLSession.shared.upload(for: request, fromFile: url)
                try NodeClient.validate(data, response)
                preview = try JSONDecoder().decode(ImportPreview.self, from: data)
            } catch { result = error.localizedDescription }
        }
    }
    private func cancel(_ item: ImportPreview) async {
        guard let client = workspace.client else { preview = nil; return }
        busy = true; defer { busy = false; preview = nil }
        do { try await client.send("v1/imports/\(item.id)/cancel", method: "POST", body: [:]) }
        catch { result = error.localizedDescription }
    }
    private func commit(_ item: ImportPreview) async {
        guard let client = workspace.client else { return }
        busy = true; result = nil; defer { busy = false }
        do {
            let data = try await client.send("v1/imports/\(item.id)/commit", method: "POST", body: [:])
            let report = try JSONDecoder().decode(ImportReport.self, from: data)
            result = LF("新增 %d 篇，更新 %d 篇，跳过 %d 篇，待核对 %d 篇。", report.added, report.updated, report.skipped, report.conflicts)
            preview = nil; await workspace.libraryChanged()
        } catch { result = error.localizedDescription }
    }
    private func chooseExport() {
        let panel = NSSavePanel(); panel.allowedContentTypes = [.zip]; panel.nameFieldStringValue = "personal-library.zip"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        busy = true; result = nil
        Task {
            defer { busy = false }
            do {
                guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
                let (temp, response) = try await URLSession.shared.download(for: client.request("v1/export"))
                guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw NodeFailure(message: "资料导出失败，原有内容保留。") }
                defer { try? FileManager.default.removeItem(at: temp) }
                let scoped = url.startAccessingSecurityScopedResource(); defer { if scoped { url.stopAccessingSecurityScopedResource() } }
                let data = try Data(contentsOf: temp, options: .mappedIfSafe)
                try data.write(to: url, options: .atomic)
                result = "资料包已保存。"; NSWorkspace.shared.activateFileViewerSelecting([url])
            } catch { result = error.localizedDescription }
        }
    }
}
