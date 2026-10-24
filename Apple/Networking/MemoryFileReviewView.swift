import SwiftUI

private struct MemoryFilePair: Decodable, Sendable {
    let path: String
    let snapshot: String
    let original: NodeDocument
    let generated: NodeDocument
}
private struct MemoryFileDecision: Encodable {
    let path: String
    let snapshot: String
    let action: String
    let requestID: UUID
}
private struct MemoryFileReceipt: Decodable {
    let action: String
    let backupPath: String?
}

struct MemoryFileReviewSheet: View {
    @ObservedObject var store: NodeWorkspaceStore
    let path: String
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var pair: MemoryFilePair?
    @State private var receipt: MemoryFileReceipt?
    @State private var busy = false
    @State private var problem: String?
    @State private var confirming: String?
    @State private var retry: MemoryFileDecision?
    var body: some View {
        NavigationStack {
            GeometryReader { available in
                ScrollView {
                    VStack(alignment: .leading, spacing: 24) {
                        Text(verbatim: path).font(.headline).textSelection(.enabled)
                        if let receipt {
                            Label(L(receipt.action == "keep" ? "已保留你的文件" : "已采用新生成的文件"), systemImage: "checkmark.circle")
                            Text("资料库中的记忆条目和来源均未改变。")
                            if let backup = receipt.backupPath {
                                Text("原文件备份位置").font(.subheadline.weight(.medium))
                                Text(verbatim: backup).font(.callout).textSelection(.enabled)
                            }
                        } else {
                            Text("这里只处理导出文件，不会将文件中的修改自动写回记忆条目。采用新版本前会备份原文件。")
                                .font(.callout).foregroundStyle(.secondary)
                            if let pair {
                                let sideBySide = available.size.width >= 850 && !typeSize.isAccessibilitySize
                                (sideBySide ? AnyLayout(HStackLayout(alignment: .top, spacing: 24)) : AnyLayout(VStackLayout(alignment: .leading, spacing: 24))) {
                                    version("你的当前文件", document: pair.original)
                                    version("新生成的文件", document: pair.generated)
                                }
                            }
                            if busy { ProgressView("正在处理") }
                            if pair == nil { recovery }
                        }
                    }.padding(20).frame(maxWidth: .infinity, alignment: .leading)
                }.id(receipt?.action ?? pair?.snapshot ?? "loading")
            }
            .safeAreaInset(edge: .bottom) {
                if pair != nil && receipt == nil {
                    VStack(alignment: .leading, spacing: 12) {
                        if problem != nil { recovery }
                        else {
                            ViewThatFits(in: .horizontal) {
                                HStack(spacing: 16) { decisions }.fixedSize(horizontal: true, vertical: false)
                                VStack(alignment: .leading, spacing: 12) { decisions }
                            }.disabled(busy)
                        }
                    }.padding(16).frame(maxWidth: .infinity, alignment: .leading).background(.bar)
                }
            }
            #if os(iOS)
            .navigationTitle(L("核对文件"))
            .navigationBarTitleDisplayMode(.inline)
            #else
            .toolbar { ToolbarItem(placement: .principal) { Text("核对记忆文件").font(.headline) } }
            #endif
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() }.disabled(busy) } }
            .confirmationDialog("按所选方式处理文件？", isPresented: Binding(get: { confirming != nil }, set: { if !$0 { confirming = nil } }), titleVisibility: .visible) {
                if let action = confirming, let pair {
                    Button(L(action == "keep" ? "确认保留我的文件" : "备份原件并采用新版本")) {
                        decide(MemoryFileDecision(path: path, snapshot: pair.snapshot, action: action, requestID: UUID()))
                    }
                }
                Button("取消", role: .cancel) { confirming = nil }
            } message: { Text("预览后文件或记忆内容有变化时，本次操作会停止，要求重新核对。") }
        }.task { await load() }.interactiveDismissDisabled(busy)
            #if os(macOS)
            .frame(minWidth: 560, idealWidth: 940, minHeight: 600)
            #endif
    }
    private var decisions: some View {
        Group {
            Button("保留我的文件") { confirming = "keep" }.buttonStyle(.bordered).frame(minHeight: 44)
            Button("采用新生成的文件") { confirming = "generated" }.buttonStyle(FilledButtonStyle())
        }.fixedSize(horizontal: false, vertical: true)
    }
    private var recovery: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let problem {
                Text(L(problem)).font(.callout).foregroundStyle(.secondary)
                if let retry { Button("重试同一次处理") { decide(retry) }.disabled(busy) }
                Button("重新读取两个版本") { Task { await load() } }.disabled(busy)
            }
        }
    }
    private func version(_ title: String, document: NodeDocument) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(L(title)).font(.headline)
            Text(verbatim: document.text.isEmpty ? L("空文件") : document.text)
                .font(.body).lineSpacing(5).textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }.padding(16).background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 12))
    }
    @MainActor private func load() async {
        guard let client = store.client else { problem = "请先连接资料库。"; return }
        busy = true; problem = nil; pair = nil; retry = nil
        defer { busy = false }
        do { pair = try await client.read("v1/memory-files/preview", query: ["path": path], as: MemoryFilePair.self) }
        catch { problem = error.localizedDescription }
    }
    private func decide(_ decision: MemoryFileDecision) {
        guard let client = store.client else { return }
        busy = true; problem = nil; retry = decision
        Task {
            defer { busy = false }
            do {
                let data = try await client.sendData("v1/memory-files/resolve", method: "POST", body: JSONEncoder().encode(decision))
                receipt = try JSONDecoder().decode(MemoryFileReceipt.self, from: data)
                retry = nil; await store.refresh()
            } catch {
                problem = error.localizedDescription
                if let code = (error as? NodeFailure)?.statusCode, (400..<500).contains(code), code != 408, code != 429 { retry = nil }
                if let code = (error as? NodeFailure)?.statusCode, code >= 500 { await store.refresh() }
            }
        }
    }
}
