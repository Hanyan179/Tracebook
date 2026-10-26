import SwiftUI
#if os(macOS)
import AppKit
import CryptoKit
import UniformTypeIdentifiers
#endif

struct DocumentOriginalNotice: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let source: KnowledgeSource
    @State private var busy = false
    @State private var problem: String?
    @State private var saved = false
    @State private var refreshedCopy: DocumentOriginalCopy?
    private var copy: DocumentOriginalCopy? { refreshedCopy ?? source.documentInfo?.originalCopy }
    private var available: Bool { copy?.state == "available" }

    var body: some View {
        if source.kind == "document", let format = source.documentInfo?.format, ["pdf", "docx", "png", "jpg", "jpeg", "heic", "heif"].contains(format) {
            VStack(alignment: .leading, spacing: 8) {
                Label(L(available ? "原件副本已保存" : (copy?.state == "unavailable" ? "原件副本需要核对" : "未保留原文件副本")), systemImage: "paperclip")
                    .font(.callout)
                if let issue = copy?.problem { Text(L(issue)).font(.footnote).foregroundStyle(.secondary) }
                #if os(macOS)
                if available, let host = workspace.client?.endpoint.host,
                   ["localhost", "127.0.0.1", "::1", "[::1]"].contains(host) {
                    Button(busy ? "正在读取原件…" : "另存原文件…") { Task { await saveOriginal(format: format) } }.disabled(busy)
                }
                #endif
                if copy?.state == "unavailable" {
                    Button("重新核对原件") { Task { await refreshOriginal() } }.disabled(busy)
                }
                if let problem { Text(L(problem)).font(.footnote).foregroundStyle(.secondary) }
                if saved { Text("原文件已保存到所选位置。").font(.footnote).foregroundStyle(.secondary) }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func refreshOriginal() async {
        guard let client = workspace.client else { return }
        busy = true;problem = nil;saved = false
        defer { busy = false }
        do {
            let value = try await client.read("v1/sources/" + source.identity, query: ["limit": "1"], as: KnowledgeSource.self)
            guard client.endpoint == workspace.client?.endpoint else { return }
            refreshedCopy = value.documentInfo?.originalCopy
        } catch { problem = error.localizedDescription }
    }

    #if os(macOS)
    private struct Download: Decodable, Sendable {
        let name: String
        let format: String
        let byteCount: Int
        let sha256: String
        let data: String
    }
    private func saveOriginal(format: String) async {
        guard let client = workspace.client else { return }
        busy = true;problem = nil;saved = false
        defer { busy = false }
        do {
            let value = try await client.read("v1/documents/" + source.id + "/original", as: Download.self)
            guard client.endpoint == workspace.client?.endpoint, value.format == format,
                  let raw = Data(base64Encoded: value.data), raw.count > 0, raw.count <= (["pdf", "docx"].contains(format) ? 2 : 8) * 1024 * 1024,
                  raw.count == value.byteCount, value.sha256 == copy?.sha256,
                  SHA256.hash(data: raw).map({ String(format: "%02x", $0) }).joined() == value.sha256 else {
                throw NodeFailure(message: "原件内容与预览不一致，请重新选择文件。")
            }
            let panel = NSSavePanel()
            panel.nameFieldStringValue = URL(fileURLWithPath: value.name).lastPathComponent
            panel.allowedContentTypes = [UTType(filenameExtension: format) ?? .data]
            panel.canCreateDirectories = true
            guard panel.runModal() == .OK, let url = panel.url else { return }
            let access = url.startAccessingSecurityScopedResource()
            defer { if access { url.stopAccessingSecurityScopedResource() } }
            try raw.write(to: url, options: .atomic)
            saved = true
        } catch {
            if (error as? NodeFailure)?.statusCode == 409 {
                refreshedCopy = DocumentOriginalCopy(state: "unavailable", problem: error.localizedDescription, byteCount: nil, sha256: nil)
            } else { problem = error.localizedDescription }
        }
    }
    #endif
}
