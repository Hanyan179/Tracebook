import SwiftUI

/// Read-only evidence inspection. Never relocate a historic quote into a changed source.
struct MemoryEvidenceReader: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let evidence: MemoryEvidence
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var context: SourceQuoteContext?
    @State private var currentSource: KnowledgeSource?
    @State private var notice: String?
    @State private var error: String?
    @State private var loading = true
    @State private var fullSource = false

    var body: some View {
        NavigationStack {
            ScrollViewReader { position in
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        Text(verbatim: evidence.source.title).font(.title2.weight(.semibold))
                        Label(L(evidence.source.kindTitle) + " · " + evidence.date, systemImage: evidence.source.symbol)
                            .font(.caption).foregroundStyle(.secondary)
                        if let notice {
                            Label(L(notice), systemImage: "clock.arrow.circlepath")
                                .font(.callout).foregroundStyle(.secondary)
                        }
                        if let context {
                            Label("已定位到提出时的引用位置", systemImage: "checkmark.circle").font(.caption).foregroundStyle(.secondary)
                            if context.hasEarlierText { Text("前文已省略").font(.caption).foregroundStyle(.secondary) }
                            if !context.before.isEmpty { Text(verbatim: context.before).textSelection(.enabled).lineSpacing(4) }
                        }
                        VStack(alignment: .leading, spacing: 10) {
                            Label("提出时的引用", systemImage: "quote.opening").font(.caption.weight(.medium))
                            Text(verbatim: evidence.quote).textSelection(.enabled).lineSpacing(5)
                        }.padding(16).frame(maxWidth: .infinity, alignment: .leading)
                            .background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 12))
                            .overlay(alignment: .leading) { RoundedRectangle(cornerRadius: 2).fill(.secondary.opacity(0.6)).frame(width: 3).padding(.vertical, 12) }
                            .id("saved-quote")
                        if let context {
                            if !context.after.isEmpty { Text(verbatim: context.after).textSelection(.enabled).lineSpacing(4) }
                            if context.hasLaterText { Text("后文已省略").font(.caption).foregroundStyle(.secondary) }
                        }
                        if loading { ProgressView("正在核对原文…") }
                        if let error {
                            Text(L(error)).font(.callout).foregroundStyle(.secondary)
                            Button("重新读取") { Task { await read() } }.disabled(loading)
                        }
                        Divider()
                        Button("查看当前完整原文", systemImage: "doc.text") { fullSource = true }
                    }.padding(24).frame(maxWidth: 720, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
                }.background(AppPalette.canvas).navigationTitle(L("引用上下文"))
                    #if os(iOS)
                    .navigationBarTitleDisplayMode(.inline)
                    #endif
                    .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
                    .onChange(of: context) { _, value in
                        if value != nil, typeSize.isAccessibilitySize { position.scrollTo("saved-quote", anchor: .top) }
                    }
            }
        }.task(id: workspace.client?.endpoint.absoluteString) { await read() }
            .sheet(isPresented: $fullSource) {
                KnowledgeSourceReader(workspace: workspace, source: currentSource ?? evidence.source).appLanguage()
            }
        #if os(macOS)
        .frame(minWidth: 520, idealWidth: 620, minHeight: 500, idealHeight: 680)
        #endif
    }

    private func read() async {
        context = nil; notice = nil; error = nil; currentSource = nil; loading = true
        defer { loading = false }
        guard evidence.snapshotState == "captured", !evidence.contentVersion.isEmpty,
              let window = SourceQuoteContext.window(quote: evidence.quote, quoteStart: evidence.quoteStart) else {
            notice = "未保存可核对的引用位置，请查看完整原文。"; return
        }
        do {
            guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
            let value = try await client.read("v1/sources/" + evidence.source.identity,
                query: ["offset": String(window.offset), "limit": String(window.limit)], as: KnowledgeSource.self)
            try Task.checkCancellation()
            currentSource = value
            guard value.contentVersion == evidence.contentVersion else {
                notice = "原文已变化，无法还原当时的前后文。下方保留原引用。"; return
            }
            guard let offset = value.offset, let total = value.totalCharacters,
                  let matched = SourceQuoteContext(text: value.text ?? "", offset: offset, totalCharacters: total,
                      quote: evidence.quote, quoteStart: evidence.quoteStart) else {
                notice = "未能核对引用位置，请查看完整原文。"; return
            }
            context = matched
        } catch is CancellationError {
            return
        } catch {
            self.error = error.localizedDescription
        }
    }
}
