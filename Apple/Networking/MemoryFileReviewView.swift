import SwiftUI
import CryptoKit
#if os(macOS)
import AppKit
#else
import UIKit
#endif

struct MemoryFileReviewReceipt: Decodable, Sendable {
    let requestID: UUID
    let path: String
    let action: String
    let backupPath: String?
    let generatedPath: String
    let contentVersion: String
    let matchesCurrent: Bool?
    var title: String {
        switch action {
        case "keep": "已保留你的文件"
        case "merge": "已保存合并文件"
        default: "已采用新生成的文件"
        }
    }
}
struct MemoryFileReviewPending: Decodable, Sendable { let requestID: UUID; let action: String }
private struct MemoryFileReviewPair: Decodable, Sendable {
    let nodeID: String
    let path: String
    let snapshot: String
    let original: NodeDocument
    let generated: NodeDocument
    let maxBytes: Int
    let pendingOperation: MemoryFileReviewPending?
    let resolved: Bool
    let lastDecision: MemoryFileReviewReceipt?
    let deliveryPending: Bool?
}
private struct MemoryFileReviewDecision: Encodable {
    let nodeID: String
    let path: String
    let requestID: UUID
    let snapshot: String
    let action: String
    let text: String?
}

struct MemoryFileReviewSheet: View {
    @ObservedObject var store: NodeWorkspaceStore
    let path: String
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var pair: MemoryFileReviewPair?
    @State private var receipt: MemoryFileReviewReceipt?
    @State private var busy = false
    @State private var problem: String?
    @State private var confirming: String?
    @State private var retryDecision: MemoryFileReviewDecision?
    @State private var merging = false
    @State private var draft = ""
    @State private var baseline = ""
    @State private var draftSnapshot = ""
    @State private var context: EditorDraftContext?
    @State private var discardDraft = false
    @State private var mustReload = false
    @State private var showingVersions = false
    private var route: String { "v1/memory-files" }
    private var dirty: Bool { draft != baseline }
    private var savingUnresolved: Bool { retryDecision != nil || pair?.pendingOperation != nil }
    private var needsRecheck: Bool { dirty && pair != nil && draftSnapshot != pair?.snapshot }
    private var completed: MemoryFileReviewReceipt? { pair?.lastDecision ?? receipt }
    private var resolved: Bool { receipt != nil || pair?.resolved == true }

    var body: some View {
        NavigationStack {
            GeometryReader { geometry in
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        Text(verbatim: path).font(.headline).textSelection(.enabled)
                        if typeSize.isAccessibilitySize, let problem {
                            Text(L(problem)).font(.callout).foregroundStyle(.secondary)
                        }
                        if let completed {
                            Label(L(completed.title), systemImage: "checkmark.circle").font(.headline)
                            Text("资料库中的记忆条目、原文依据与修订历史均未改变。")
                                .font(.callout).foregroundStyle(.secondary)
                            if pair?.deliveryPending == true {
                                Text("这是已保存的文件核对记录。资料库中的后续更新需要单独交付；查看记录不会恢复写入。")
                                    .font(.callout).foregroundStyle(.secondary)
                            }
                            if completed.matchesCurrent == false {
                                Text("当前文件在这次核对后又有修改，下面显示当前内容。").font(.callout)
                            }
                            if let backup = completed.backupPath {
                                Text("原文件备份位置").font(.subheadline.weight(.medium))
                                Text(verbatim: backup).font(.caption).textSelection(.enabled)
                            }
                        } else if typeSize.isAccessibilitySize {
                            DisclosureGroup("保存规则") { explanation }
                        } else { explanation }
                        if let pair {
                            if merging || dirty {
                                DisclosureGroup("对照两个版本", isExpanded: $showingVersions) {
                                    versions(pair, width: geometry.size.width).padding(.top, 12)
                                }
                            } else { versions(pair, width: geometry.size.width) }
                            if pair.pendingOperation != nil && !resolved {
                                Text("上次文件核对尚未完成，继续会复用已保存的备份和合并内容。")
                                    .font(.callout)
                            }
                        }
                        if retryDecision != nil && pair?.pendingOperation == nil && !resolved {
                            Text("上次保存结果尚未确认，请重试或重新读取。当前草稿会保留，暂时不能修改。")
                                .font(.callout)
                        }
                        if merging || dirty {
                            VStack(alignment: .leading, spacing: 10) {
                                Text("合并草稿").font(.headline)
                                Text("草稿保存在本机，只有保存后才更新这个导出文件。").font(.caption).foregroundStyle(.secondary)
                                TextEditor(text: $draft).font(.body).frame(minHeight: 260)
                                    .accessibilityLabel(L("合并草稿"))
                                    .disabled(busy || savingUnresolved)
                                    .padding(8).background(.quaternary.opacity(0.2), in: RoundedRectangle(cornerRadius: 12))
                                if let draftIssue { Text(L(draftIssue)).font(.callout) }
                                if needsRecheck && !resolved {
                                    Text("两个版本已更新，草稿仍保留。请检查上方内容，再确认草稿适用于当前版本。")
                                }
                                (typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: 12)) : AnyLayout(HStackLayout())) {
                                    Button("复制合并草稿") { copyDraft() }
                                    Button("放弃合并草稿") { discardDraft = true }.disabled(busy || savingUnresolved)
                                }
                            }
                        }
                        if busy { ProgressView("正在处理") }
                    }.padding(20).frame(maxWidth: .infinity, alignment: .leading)
                }
                .id(receipt?.requestID.uuidString ?? pair?.snapshot ?? "loading")
                .safeAreaInset(edge: .bottom) {
                    VStack(alignment: .leading, spacing: 12) {
                        if typeSize.isAccessibilitySize && problem != nil {
                            Menu("恢复处理") { recoveryActions }.buttonStyle(.bordered).disabled(busy)
                        } else {
                            if let problem {
                                Text(L(problem)).font(.callout).foregroundStyle(.secondary)
                                recoveryActions
                            }
                            if let pair, !resolved, problem == nil {
                                reviewActions(pair, width: geometry.size.width)
                                    .disabled(busy || mustReload || retryDecision != nil)
                            }
                        }
                    }.fixedSize(horizontal: false, vertical: true)
                        .padding(problem != nil || (pair != nil && !resolved) ? 16 : 0)
                        .frame(maxWidth: .infinity, alignment: .leading).background(.bar)
                }
            }
            .navigationTitle(L("核对记忆文件"))
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { persistDraft(); dismiss() }.disabled(busy) } }
            .confirmationDialog("按所选方式处理文件？", isPresented: Binding(get: { confirming != nil }, set: { if !$0 { confirming = nil } }), titleVisibility: .visible) {
                if let action = confirming, let pair {
                    Button(L(action == "keep" ? "确认保留我的文件" : action == "merge" ? "备份并保存合并文件" : "备份原件并采用新版本")) {
                        decide(MemoryFileReviewDecision(nodeID: pair.nodeID, path: path, requestID: UUID(), snapshot: pair.snapshot,
                            action: action, text: action == "merge" ? draft : nil))
                    }
                }
                Button("取消", role: .cancel) { confirming = nil }
            } message: { Text("采用或合并前会备份原文件，生成版本也会保留。预览后文件或记忆有变化时会停止，并保留本机草稿。") }
            .confirmationDialog("放弃尚未提交的本机修改？", isPresented: $discardDraft) {
                Button("放弃合并草稿", role: .destructive) {
                    removeDraft(); draft = pair?.original.text ?? ""; baseline = draft
                    draftSnapshot = pair?.snapshot ?? ""; merging = false
                }
            }
        }.task { await load() }
            .onChange(of: draft) { _, _ in persistDraft() }
            .onDisappear { persistDraft() }
            .protectsUnsavedEdits(in: store, editing: busy || dirty || savingUnresolved)
            .interactiveDismissDisabled(busy)
            #if os(macOS)
            .frame(minWidth: 560, idealWidth: 940, minHeight: 600)
            #endif
    }

    private var draftIssue: String? {
        if draft.utf8.count > (pair?.maxBytes ?? 128 * 1024) || draft.contains("\0") { return "合并文件须为有效文字，最多 128 KB。" }
        if path.hasSuffix(".json"), (try? JSONSerialization.jsonObject(with: Data(draft.utf8))) as? [String: Any] == nil {
            return "历史文件的合并内容须为有效 JSON 对象；原文件和草稿仍保留。"
        }
        return nil
    }
    private var explanation: some View {
        Text("对照当前文件与生成版本，可保留、采用或手动合并。采用或合并前先备份；这里只修改导出文件，不会写回记忆条目。")
            .font(.callout).foregroundStyle(.secondary)
    }
    @ViewBuilder private var recoveryActions: some View {
        if let retryDecision { Button("重试同一次处理") { decide(retryDecision) }.disabled(busy) }
        else if let pending = pair?.pendingOperation { Button("继续保存上次文件核对") { resume(pending) }.disabled(busy) }
        Button("重新读取两个版本") { Task { await load() } }.disabled(busy)
    }
    @ViewBuilder private func reviewActions(_ pair: MemoryFileReviewPair, width: CGFloat) -> some View {
        if let pending = pair.pendingOperation {
            Button("继续保存上次文件核对") { resume(pending) }.buttonStyle(FilledButtonStyle())
        } else if merging || dirty {
            if needsRecheck {
                Button("已核对新版本") { draftSnapshot = pair.snapshot; persistDraft() }.buttonStyle(.bordered)
            } else {
                Button("保存合并版本") { confirming = "merge" }.buttonStyle(FilledButtonStyle())
                    .disabled(draftIssue != nil)
            }
        } else if typeSize.isAccessibilitySize || width < 650 {
            Menu("处理文件") {
                Button("保留我的文件") { confirming = "keep" }
                Button("采用新生成的文件") { confirming = "generated" }
                Button("手动合并") { merging = true; draftSnapshot = pair.snapshot }
            }.buttonStyle(FilledButtonStyle())
        } else {
            (width >= 850 ? AnyLayout(HStackLayout(spacing: 16)) : AnyLayout(VStackLayout(alignment: .leading, spacing: 12))) {
                Button("保留我的文件") { confirming = "keep" }.buttonStyle(.bordered).frame(minHeight: 44)
                Button("采用新生成的文件") { confirming = "generated" }.buttonStyle(.bordered).frame(minHeight: 44)
                Button("手动合并") { merging = true; draftSnapshot = pair.snapshot }.buttonStyle(FilledButtonStyle())
            }
        }
    }
    private func versions(_ pair: MemoryFileReviewPair, width: CGFloat) -> some View {
        let horizontal = width >= 850 && !typeSize.isAccessibilitySize
        return (horizontal ? AnyLayout(HStackLayout(alignment: .top, spacing: 20)) : AnyLayout(VStackLayout(alignment: .leading, spacing: 20))) {
            version("你的当前文件", pair.original)
            version("新生成的文件", pair.generated)
        }
    }
    private func version(_ title: String, _ document: NodeDocument) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L(title)).font(.headline)
            Text(verbatim: document.text.isEmpty ? L("空文件") : document.text)
                .font(.body).lineSpacing(5).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
        }.padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 12))
    }
    @MainActor private func load() async {
        guard let client = store.client else { problem = "请先连接资料库。"; return }
        busy = true; problem = nil; defer { busy = false }
        let revision = store.connectionRevision
        do {
            let identity = try await client.read("v1/status", as: NodeStatus.self)
            guard !Task.isCancelled, revision == store.connectionRevision else { return }
            if let context, dirty && (context.nodeID != identity.nodeID || context.endpoint != store.endpoint) {
                throw NodeFailure(message: "资料库已变化，草稿仍保留。请回到原资料库核对。")
            }
            let nextContext = EditorDraftContext(endpoint: store.endpoint, nodeID: identity.nodeID, path: route + "/" + path + "/merge-draft")
            let saved = try nextContext.readDraft(nextContext.url)
            context = nextContext
            let next = try await client.read(route + "/preview", query: ["path": path], as: MemoryFileReviewPair.self)
            guard !Task.isCancelled, revision == store.connectionRevision, next.nodeID == identity.nodeID, next.path == path else { return }
            if !dirty {
                baseline = next.original.text; draft = saved?.text ?? baseline
                draftSnapshot = saved?.etag ?? next.snapshot; merging = saved != nil
                if next.resolved, saved?.text == next.original.text { removeDraft(); merging = false }
            }
            if !next.resolved { receipt = nil }
            pair = next; retryDecision = nil; mustReload = false
        } catch {
            guard !Task.isCancelled, revision == store.connectionRevision else { return }
            if let context, !dirty, let saved = try? context.readDraft(context.url) {
                draft = saved.text; baseline = saved.savedText ?? ""; draftSnapshot = saved.etag; merging = true
            }
            problem = error.localizedDescription
        }
    }
    private func decide(_ decision: MemoryFileReviewDecision) {
        guard let client = store.client else { return }
        busy = true; problem = nil; retryDecision = decision
        let revision = store.connectionRevision
        Task { @MainActor in
            defer { busy = false }
            do {
                let data = try await client.sendData(route + "/resolve", method: "POST", body: JSONEncoder().encode(decision))
                guard revision == store.connectionRevision, !Task.isCancelled else { return }
                try await accept(data)
            } catch { if revision == store.connectionRevision && !Task.isCancelled { handle(error) } }
        }
    }
    private func resume(_ pending: MemoryFileReviewPending) {
        guard let client = store.client, let pair else { return }
        busy = true; problem = nil
        let revision = store.connectionRevision
        Task { @MainActor in
            defer { busy = false }
            do {
                let data = try await client.send(route + "/retry", method: "POST", body: ["nodeID": pair.nodeID, "path": path, "requestID": pending.requestID.uuidString])
                guard revision == store.connectionRevision, !Task.isCancelled else { return }
                try await accept(data)
            } catch { if revision == store.connectionRevision && !Task.isCancelled { handle(error) } }
        }
    }
    @MainActor private func accept(_ data: Data) async throws {
        let result = try JSONDecoder().decode(MemoryFileReviewReceipt.self, from: data)
        guard result.path == path else { throw NodeFailure(message: "这次文件处理的内容或资料夹已变化，请重新核对。") }
        receipt = result
        retryDecision = nil
        let hash = SHA256.hash(data: Data(draft.utf8)).map { String(format: "%02x", $0) }.joined()
        if !dirty || receipt?.contentVersion == hash { removeDraft(); baseline = draft; merging = false }
        await store.libraryChanged(); await load()
    }
    private func handle(_ error: Error) {
        problem = error.localizedDescription; persistDraft()
        if let code = (error as? NodeFailure)?.statusCode, (400..<500).contains(code), code != 408, code != 429 { retryDecision = nil; mustReload = true }
    }
    private func persistDraft() {
        guard let context else { return }
        guard dirty else { removeDraft(); return }
        do {
            try FileManager.default.createDirectory(at: context.url.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            try JSONEncoder().encode(EditorDraft(text: draft, etag: draftSnapshot, savedText: baseline)).write(to: context.url, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: context.url.path)
        } catch { problem = "草稿无法保存在本机。请保存或复制内容后再离开。" }
    }
    private func removeDraft() { if let context { try? FileManager.default.removeItem(at: context.url) } }
    private func copyDraft() {
        #if os(macOS)
        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(draft, forType: .string)
        #else
        UIPasteboard.general.string = draft
        #endif
    }
}
