import SwiftUI

/// Read-only evidence inspection. Never relocate a historic quote into a changed source.
struct MemoryEvidenceReader: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let evidence: MemoryEvidence
    var origin: SourcePassageReader.Origin = .memory
    var body: some View {
        SourcePassageReader(workspace: workspace, source: evidence.source, quote: evidence.quote,
            quoteStart: evidence.snapshotState == "captured" ? evidence.quoteStart : nil, origin: origin)
    }
}

struct SourcePassageReader: View {
    enum Origin { case memory, search, answer, review }
    @ObservedObject var workspace: NodeWorkspaceStore
    let source: KnowledgeSource
    let quote: String
    let quoteStart: Int?
    let origin: Origin
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var context: SourceQuoteContext?
    @State private var currentSource: KnowledgeSource?
    @State private var notice: String?
    @State private var error: String?
    @State private var loading = true
    @State private var fullSource = false
    @State private var historicalFullSource = false
    @State private var versionRead: SourceVersionRead?
    @State private var historyNodeID: String?
    @State private var boundNodeID: String?
    @State private var readID = UUID()
    private var pageTitle: String {
        switch origin { case .memory: "引用上下文"; case .search: "检索片段上下文"; case .answer: "回答引用上下文"; case .review: "核对引用上下文" }
    }
    private var quoteTitle: String {
        switch origin { case .memory: "提出时的引用"; case .search: "检索时保存的片段"; case .answer: "生成回答时引用的原文"; case .review: "核对时引用的原文" }
    }
    private var locatedTitle: String {
        switch origin { case .memory: "已定位到提出时的引用位置"; case .search: "已定位到检索片段"; case .answer: "已定位到回答引用的原文"; case .review: "已定位到核对引用的原文位置" }
    }
    private var changedNotice: String {
        switch origin {
        case .memory: "原文已变化，无法还原当时的前后文。下方保留原引用。"
        case .search: "原文已变化，无法还原检索时的前后文。下方保留当时的片段。"
        case .answer: "原文已变化，无法还原生成回答时的前后文。下方保留当时的引用。"
        case .review: "原文已变化，无法还原核对时的前后文。下方保留当时的引用。"
        }
    }

    var body: some View {
        NavigationStack {
            ScrollViewReader { position in
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        Text(verbatim: source.title).font(.title2.weight(.semibold))
                        Label(L(source.kindTitle) + " · " + source.date, systemImage: source.symbol)
                            .font(.caption).foregroundStyle(.secondary)
                        if origin != .memory, let analysisNotice = (currentSource ?? source).analysisNotice {
                            Label(L(analysisNotice), systemImage: "info.circle").font(.callout).foregroundStyle(.secondary)
                        }
                        if let notice {
                            Label(L(notice), systemImage: "clock.arrow.circlepath")
                                .font(.callout).foregroundStyle(.secondary)
                        }
                        if let context {
                            Label(versionRead?.state == "historical" ? L("已核对当时版本与引用位置") : L(locatedTitle), systemImage: "checkmark.circle").font(.caption).foregroundStyle(.secondary)
                            if context.hasEarlierText { Text("前文已省略").font(.caption).foregroundStyle(.secondary) }
                            if !context.before.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { Text(verbatim: context.before).textSelection(.enabled).lineSpacing(4) }
                        }
                        VStack(alignment: .leading, spacing: 10) {
                            if versionRead?.state == "historical" {
                                Label("历史版本", systemImage: "clock.arrow.circlepath").font(.caption.weight(.medium))
                            }
                            Label(L(quoteTitle), systemImage: "quote.opening").font(.caption.weight(.medium))
                            Text(verbatim: quote).textSelection(.enabled).lineSpacing(5)
                            if source.kind == "document" {
                                DisclosureGroup {
                                    VStack(alignment: .leading, spacing: 10) {
                                        DocumentRevisionNotice(source: currentSource ?? source, snapshot: currentSource == nil)
                                        DocumentContentNotice(role: (currentSource ?? source).documentRole)
                                        if let info = currentSource?.documentInfo {
                                            DocumentSourceNotice(info: info, quoteStart: context == nil ? nil : quoteStart,
                                                quoteLength: quote.unicodeScalars.count)
                                        }
                                    }.frame(maxWidth: .infinity, alignment: .leading)
                                } label: {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text("来源详情")
                                        Text(L((currentSource ?? source).documentRole.title)).font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }.padding(16).frame(maxWidth: .infinity, alignment: .leading)
                            .background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 12))
                            .overlay(alignment: .leading) { RoundedRectangle(cornerRadius: 2).fill(.secondary.opacity(0.6)).frame(width: 3).padding(.vertical, 12) }
                            .id("saved-quote")
                        if let context {
                            if !context.after.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { Text(verbatim: context.after).textSelection(.enabled).lineSpacing(4) }
                            if context.hasLaterText { Text("后文已省略").font(.caption).foregroundStyle(.secondary) }
                        }
                        if loading { ProgressView("正在核对原文…") }
                        if versionRead?.canContinue == true {
                            Button("继续查找历史版本") { Task { await read(continuing: true) } }.disabled(loading)
                        }
                        if ["missing", "incomplete"].contains(versionRead?.state ?? ""), error == nil {
                            Button("重新读取") { Task { await read() } }.disabled(loading)
                        }
                        if let error {
                            Text(L(error)).font(.callout).foregroundStyle(.secondary)
                            Button("重新读取") { Task { await read() } }.disabled(loading)
                        }
                        Divider()
                        if versionRead?.state == "historical", context != nil {
                            Button("查看当时完整原文", systemImage: "clock.arrow.circlepath") { historicalFullSource = true }
                        }
                        Button("查看当前完整原文", systemImage: "doc.text") { fullSource = true }
                            .disabled(boundNodeID != nil && workspace.status?.nodeID != boundNodeID)
                    }.padding(24).frame(maxWidth: 720, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
                }.background(AppPalette.canvas).navigationTitle(L(pageTitle))
                    #if os(iOS)
                    .navigationBarTitleDisplayMode(.inline)
                    #endif
                    .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
                    .onChange(of: context) { _, value in
                        if value != nil, origin != .memory || typeSize.isAccessibilitySize || currentSource?.documentInfo != nil {
                            position.scrollTo("saved-quote", anchor: .top)
                        }
                    }
            }
        }.task(id: "\(workspace.connectionRevision)-\(workspace.status?.nodeID ?? "")") { await read() }
            .sheet(isPresented: $historicalFullSource) {
                if let value = versionRead, let nodeID = historyNodeID {
                    HistoricalSourceReader(workspace: workspace, source: source, versionRead: value, nodeID: nodeID).appLanguage()
                }
            }
            .sheet(isPresented: $fullSource, onDismiss: { Task { await read() } }) {
                KnowledgeSourceReader(workspace: workspace, source: currentSource ?? source).appLanguage()
            }
        #if os(macOS)
        .frame(minWidth: 520, idealWidth: 620, minHeight: 500, idealHeight: 680)
        #endif
    }

    private func read(continuing: Bool = false) async {
        let requestID = UUID(); readID = requestID
        let previous = continuing ? versionRead : nil
        context = nil; notice = nil; error = nil; currentSource = nil; loading = true
        if !continuing { versionRead = nil; historyNodeID = nil }
        defer { if readID == requestID { loading = false } }
        guard let quoteStart, !source.contentVersion.isEmpty,
              let window = SourceQuoteContext.window(quote: quote, quoteStart: quoteStart) else {
            notice = "未保存可核对的引用位置，请查看完整原文。"; return
        }
        let connection = workspace.connectionRevision
        let expectedNode = boundNodeID ?? workspace.status?.nodeID
        do {
            guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
            let status = try await client.read("v1/status", as: NodeStatus.self)
            try Task.checkCancellation()
            guard readID == requestID, workspace.connectionRevision == connection else { return }
            guard expectedNode == nil || expectedNode == status.nodeID else {
                throw NodeFailure(message: "资料库已变化，请重新连接后读取。")
            }
            boundNodeID = status.nodeID
            let value: KnowledgeSource
            if status.sourceHistorySupported == true {
                var query = ["offset": String(window.offset), "limit": String(window.limit), "nodeID": status.nodeID]
                if let cursor = previous?.nextCursor { query["cursor"] = cursor }
                if let revision = previous?.beforeRevision { query["beforeRevision"] = String(revision) }
                let result = try await client.read("v1/sources/" + source.identity + "/versions/" + source.contentVersion,
                    query: query, as: SourceVersionRead.self)
                try Task.checkCancellation()
                guard readID == requestID, workspace.connectionRevision == connection,
                      workspace.status?.nodeID == nil || workspace.status?.nodeID == status.nodeID else { return }
                versionRead = result; historyNodeID = status.nodeID
                switch result.state {
                case "searching": notice = "历史较多，尚未查找完。可继续查找，原引用保留。"; return
                case "missing": notice = "未找到这个版本的历史全文。下方保留原引用。"; return
                case "incomplete": notice = "部分历史文件无法核对，尚未找到这个版本。下方保留原引用。"; return
                case "historical": notice = "以下为当时保存的历史版本。当前原文可能已修订。"
                case "current": break
                default: throw NodeFailure(message: "未能核对引用位置，请查看完整原文。")
                }
                guard let saved = result.source else { throw NodeFailure(message: "未能核对引用位置，请查看完整原文。") }
                value = saved
            } else {
                value = try await client.read("v1/sources/" + source.identity,
                    query: ["offset": String(window.offset), "limit": String(window.limit)], as: KnowledgeSource.self)
                try Task.checkCancellation()
                guard readID == requestID, workspace.connectionRevision == connection else { return }
            }
            currentSource = value
            guard value.contentVersion == source.contentVersion else { notice = changedNotice; return }
            guard let offset = value.offset, let total = value.totalCharacters,
                  let matched = SourceQuoteContext(text: value.text ?? "", offset: offset, totalCharacters: total,
                      quote: quote, quoteStart: quoteStart) else {
                notice = "未能核对引用位置，请查看完整原文。"; return
            }
            context = matched
        } catch is CancellationError {
            return
        } catch {
            if readID == requestID, workspace.connectionRevision == connection { self.error = error.localizedDescription }
        }
    }
}

/// A version-pinned reader with no edit, proposal or model actions.
private struct HistoricalSourceReader: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let source: KnowledgeSource
    let versionRead: SourceVersionRead
    let nodeID: String
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var nextOffset: Int?
    @State private var loaded = false
    @State private var loading = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text(verbatim: source.title).font(.title2.weight(.semibold))
                    Label("历史全文，只读核对。", systemImage: "clock.arrow.circlepath")
                        .font(.caption).foregroundStyle(.secondary)
                    let parts = documentArchiveParts(text, kind: source.kind)
                    MarkdownReader(text: parts.body)
                    Text(LF("版本 %@", String(source.contentVersion.prefix(12)))).font(.caption).foregroundStyle(.secondary)
                    if let metadata = parts.header {
                        DisclosureGroup("资料信息") { Text(verbatim: metadata).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
                    }
                    if loading { ProgressView("正在核对原文…") }
                    if let error {
                        Text(L(error)).foregroundStyle(.secondary)
                        Button("重新读取") { Task { await read(reset: true) } }.disabled(loading)
                    }
                    if loaded, nextOffset != nil, error == nil {
                        Button("继续读取") { Task { await read(reset: false) } }.disabled(loading)
                    }
                }.padding(24).frame(maxWidth: 720, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
            }.background(AppPalette.canvas).navigationTitle(L("当时的完整原文"))
                #if os(iOS)
                .navigationBarTitleDisplayMode(.inline)
                #endif
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
        }.task(id: workspace.connectionRevision) { await read(reset: true) }
        #if os(macOS)
        .frame(minWidth: 520, idealWidth: 680, minHeight: 500, idealHeight: 700)
        #endif
    }

    private func read(reset: Bool) async {
        let connection = workspace.connectionRevision
        loading = true; error = nil
        if reset { text = ""; nextOffset = nil; loaded = false }
        defer { loading = false }
        do {
            guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
            var query = ["nodeID": nodeID, "offset": String(reset ? 0 : (nextOffset ?? 0)), "limit": "16000"]
            if let locator = versionRead.locator { query["locator"] = locator }
            let result = try await client.read("v1/sources/" + source.identity + "/versions/" + source.contentVersion,
                query: query, as: SourceVersionRead.self)
            try Task.checkCancellation()
            guard workspace.connectionRevision == connection else { return }
            guard let value = result.source, value.contentVersion == source.contentVersion,
                  value.offset == (reset ? 0 : nextOffset), ["current", "historical"].contains(result.state) else {
                throw NodeFailure(message: "历史文件或资料夹已变化，请重新读取。")
            }
            text = reset ? (value.text ?? "") : text + (value.text ?? "")
            nextOffset = value.nextOffset; loaded = true
        } catch is CancellationError { return }
        catch { if workspace.connectionRevision == connection { self.error = error.localizedDescription } }
    }
}
