import SwiftUI

private struct RelatedMemories: Decodable {
    struct Item: Decodable, Identifiable {
        let id: UUID
        let text: String
        let reasons: [String]
        let rank: [Double]?
        let scanIndex: Int?
        var reason: String {
            reasons.map { L($0 == "sharedSource" ? "引用同一份资料" : $0 == "sameWording" ? "文字基本相同" : "文字相近") }.joined(separator: " · ")
        }
    }
    var items: [Item]
    let checkedCount: Int
    let totalCount: Int
    let truncated: Bool
    let scannedCount: Int?
    let nextCursor: String?
    let snapshot: String?
    var checkedTotal: Int { scannedCount ?? checkedCount }

    mutating func includeEarlierPage(_ previous: RelatedMemories) {
        var seen = Set<UUID>()
        items = Array((previous.items + items).filter { seen.insert($0.id).inserted }.sorted {
            let left = $0.rank ?? [], right = $1.rank ?? []
            if left != right { return right.lexicographicallyPrecedes(left) }
            return ($0.scanIndex ?? 0) < ($1.scanIndex ?? 0)
        }.prefix(5))
    }
}

struct MemoryRelation: Codable, Identifiable, Sendable {
    let id: UUID
    let kind: String
    let outgoing: Bool
    let otherID: UUID
    let otherRevision: Int
    let otherText: String
    let createdAt: String
    var title: String {
        switch (kind, outgoing) {
        case ("duplicate", true): "与这条记忆重复，已忽略新建议"
        case ("duplicate", false): "已忽略的重复建议"
        case ("supplement", true): "补充了这条记忆"
        case ("supplement", false): "这条记忆的补充"
        case ("replace", true): "替代了这条旧记忆"
        default: "已由这条新记忆替代"
        }
    }
}

/// The user chooses the relationship. No similarity score is treated as a factual decision.
struct MemoryComparisonSheet: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let candidate: NodeCandidate
    let saved: (NodeCandidate) -> Void
    @Environment(\.dismiss) private var dismiss
    @StateObject private var loader = LibraryLoader<NodeCandidate>()
    @State private var query = ""
    @State private var proposal: NodeCandidate?
    @State private var target: NodeCandidate?
    @State private var evidence: MemoryEvidence?
    @State private var busy = false
    @State private var problem: String?
    @State private var requestID = UUID()
    @State private var retryDecision: String?
    @State private var confirmation: String?
    @State private var suggestions: RelatedMemories?
    @State private var suggestionsLoading = false
    @State private var suggestionsProblem: String?
    @State private var suggestionRequest = UUID()
    @State private var browseAll = false
    private var incoming: NodeCandidate { proposal ?? candidate }
    private var filter: LibraryFilter { var value = LibraryFilter(); value.query = query; value.state = "confirmed"; return value }
    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                Form {
                    Section("新建议") { summary(incoming) }
                    if let target {
                        Section("已有记忆") {
                            summary(target)
                            Button("选择另一条记忆") { self.target = nil; problem = nil; retryDecision = nil; requestID = UUID() }
                                .disabled(busy || retryDecision != nil)
                        }
                        Section("核对后处理") {
                            Text("请核对两边的原文与说话人，再决定是否重复、补充或替代。")
                                .font(.callout).foregroundStyle(.secondary)
                            decisionButton("重复，保留已有记忆", kind: "duplicate")
                            decisionButton("作为补充，保留两条", kind: "supplement")
                            decisionButton("用新建议替代旧记忆", kind: "replace")
                        }
                    } else {
                        Section("可能相关的记忆") {
                            Text("按文字与来源在本机查找，未发送给模型。相近内容可能属于不同的人，请核对后再处理。")
                                .font(.footnote).foregroundStyle(.secondary)
                            if suggestionsLoading { ProgressView("正在读取") }
                            if let suggestions {
                                ForEach(suggestions.items) { item in
                                    Button { Task { await readPair(item.id) } } label: {
                                        VStack(alignment: .leading, spacing: 6) {
                                            Text(verbatim: item.text).lineLimit(3)
                                            Text(verbatim: item.reason).font(.caption).foregroundStyle(.secondary)
                                        }.frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                                    }.buttonStyle(.plain).disabled(busy)
                                }
                                if suggestions.items.isEmpty {
                                    Text(suggestions.truncated ? "已检查部分暂未找到相关项。" : "没有找到相关项，仍可在下方搜索已有记忆。")
                                }
                                Text(LF("已在本机检查 %d / %d 条已确认记忆，累计保留最多 5 条相关项。", suggestions.checkedTotal, suggestions.totalCount))
                                    .font(.footnote).foregroundStyle(.secondary)
                                if suggestions.nextCursor != nil {
                                    Button("继续检查更早记忆") { Task { await loadSuggestions(continuing: true) } }
                                        .disabled(busy || suggestionsLoading)
                                } else if suggestions.truncated {
                                    Text("更早的记忆尚未比较，请使用下方搜索继续核对。")
                                        .font(.footnote).foregroundStyle(.secondary)
                                }
                            }
                            if let suggestionsProblem { Text(L(suggestionsProblem)).foregroundStyle(.secondary) }
                            Button("重新查找相关记忆") { Task { await loadSuggestions() } }.disabled(busy || suggestionsLoading)
                        }
                        Section("选择已确认记忆") {
                            TextField("搜索已有记忆", text: $query).disabled(busy)
                            if query.isEmpty {
                                Button(browseAll ? "收起全部记忆" : "浏览全部已确认记忆") { browseAll.toggle() }.disabled(busy)
                            }
                            if !query.isEmpty || browseAll {
                                Text("选择需要对照的记忆；列表不代表系统已判断它们存在冲突。")
                                    .font(.footnote).foregroundStyle(.secondary)
                                ForEach(loader.items.filter { $0.id != candidate.id }) { item in
                                    Button { Task { await readPair(item.id) } } label: {
                                        Text(verbatim: item.text).fixedSize(horizontal: false, vertical: true)
                                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                                    }.buttonStyle(.plain).disabled(busy)
                                }
                                if loader.loading { ProgressView("正在读取") }
                                else if loader.items.isEmpty && loader.problem == nil { Text("没有找到可对照的已确认记忆。") }
                                if let failure = loader.problem {
                                    Text(L(failure)).foregroundStyle(.secondary)
                                    Button("重新读取") { Task { await loader.reload(client: workspace.client, kind: "memory", filter: filter) } }
                                }
                                if loader.nextCursor != nil {
                                    Button("继续读取") { Task { await loader.more(client: workspace.client, kind: "memory", filter: filter) } }
                                        .disabled(loader.loading || busy)
                                }
                            }
                        }
                    }
                    if busy { ProgressView("正在处理") }
                    if let problem {
                        Section {
                            Text(L(problem)).foregroundStyle(.secondary)
                            if let retryDecision {
                                Button("重试同一次处理") { resolve(retryDecision) }.disabled(busy)
                            }
                            if let target {
                                Button("重新读取新旧记忆") { Task { await readPair(target.id) } }.disabled(busy)
                            }
                        }.id("comparisonProblem")
                    }
                    Text("替代会确认新建议并停用旧记忆。补充会保留两条已确认记忆。重复只忽略新建议。原文与历史均保留。")
                        .font(.footnote).foregroundStyle(.secondary)
                }.formStyle(.grouped)
                    .onChange(of: problem) { _, value in
                        if value != nil { proxy.scrollTo("comparisonProblem", anchor: .top) }
                    }
            }
                #if os(iOS)
                .navigationTitle(L("对照"))
                .navigationBarTitleDisplayMode(.inline)
                #else
                .toolbar { ToolbarItem(placement: .principal) { Text("记忆对照").font(.headline) } }
                #endif
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() }.disabled(busy) } }
                .confirmationDialog("按所选方式处理这两条记忆？", isPresented: Binding(get: { confirmation != nil }, set: { if !$0 { confirmation = nil } }), titleVisibility: .visible) {
                    if let kind = confirmation {
                        Button(L(kind == "replace" ? "确认替代并保留历史" : kind == "supplement" ? "确认补充" : "确认重复")) { resolve(kind) }
                    }
                    Button("取消", role: .cancel) { confirmation = nil }
                } message: { Text("请核对上方新旧内容。两条记忆中任何一条被修改时，本次处理会停止。") }
        }
        .task(id: workspace.endpoint + filter.signature) { await loader.reload(client: workspace.client, kind: "memory", filter: filter) }
        .task(id: workspace.endpoint + candidate.id.uuidString) { await loadSuggestions() }
        .onAppear { workspace.memoryNotice = nil }
        .onDisappear { suggestionRequest = UUID(); suggestionsLoading = false }
        .interactiveDismissDisabled(busy)
        .sheet(item: $evidence) { item in
            if item.quote.isEmpty {
                KnowledgeSourceReader(workspace: workspace, source: item.source).appLanguage()
            } else {
                MemoryEvidenceReader(workspace: workspace, evidence: item).appLanguage()
            }
        }
        #if os(macOS)
        .frame(minWidth: 560, idealWidth: 680, minHeight: 580)
        #endif
    }
    private func summary(_ item: NodeCandidate) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(verbatim: item.text).font(.body).textSelection(.enabled)
            Text(L(item.stateTitle)).font(.caption).foregroundStyle(.secondary)
            if let proposal = item.proposal {
                Text(LF("由模型提出：%@", proposal.model)).font(.caption).foregroundStyle(.secondary)
            }
            ForEach(item.evidence ?? [], id: \.identity) { source in
                VStack(alignment: .leading, spacing: 6) {
                    Text(verbatim: source.title).font(.subheadline.weight(.medium))
                    Text(L(source.source.kindTitle) + (source.date.isEmpty ? "" : " · " + source.date))
                        .font(.caption).foregroundStyle(.secondary)
                    Text(verbatim: source.quote).font(.callout).textSelection(.enabled)
                    if source.sourceStatus == "changed" || source.sourceStatus == "unavailable" {
                        Text("当前来源已变化或不可读，此处保留提出时的依据。")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    Button("核对引用上下文") { evidence = source }.disabled(busy)
                }
            }
        }.padding(.vertical, 6)
    }
    private func decisionButton(_ title: String, kind: String) -> some View {
        Button(L(title)) { confirmation = kind }
            .frame(minHeight: 44)
            .disabled(busy || problem != nil || incoming.state != "pending" || target?.state != "confirmed")
    }
    private func readPair(_ id: UUID) async {
        guard let client = workspace.client else { problem = "请先连接资料库。"; return }
        busy = true
        defer { busy = false }
        do {
            let fresh = try await client.read("v1/candidates/" + candidate.id.uuidString, as: NodeCandidate.self)
            let previous = try await client.read("v1/candidates/" + id.uuidString, as: NodeCandidate.self)
            proposal = fresh; target = previous; requestID = UUID(); retryDecision = nil
            problem = fresh.state == "pending" && previous.state == "confirmed" ? nil : "新旧记忆已被修改，请重新读取并核对后再处理。"
        } catch { problem = error.localizedDescription }
    }
    private func loadSuggestions(continuing: Bool = false) async {
        let previous = continuing ? suggestions : nil
        if continuing && previous?.nextCursor == nil { return }
        let request = UUID(); suggestionRequest = request
        let endpoint = workspace.endpoint
        if !continuing { suggestions = nil }
        suggestionsProblem = nil
        guard let client = workspace.client else { suggestionsProblem = "请先连接资料库。"; return }
        suggestionsLoading = true
        defer { if suggestionRequest == request { suggestionsLoading = false } }
        do {
            let parameters = previous?.nextCursor.map { ["cursor": $0] } ?? [:]
            var result = try await client.read("v1/candidates/\(candidate.id.uuidString)/related", query: parameters, as: RelatedMemories.self)
            guard !Task.isCancelled, suggestionRequest == request, endpoint == workspace.endpoint else { return }
            if let previous {
                guard previous.snapshot == result.snapshot, result.checkedTotal > previous.checkedTotal else {
                    suggestions = nil; suggestionsProblem = "记忆已变化，请重新查找相关记忆。"; return
                }
                result.includeEarlierPage(previous)
            }
            suggestions = result
        } catch {
            guard !Task.isCancelled, suggestionRequest == request, endpoint == workspace.endpoint else { return }
            if let code = (error as? NodeFailure)?.statusCode, code == 400 || code == 409 { suggestions = nil }
            suggestionsProblem = error.localizedDescription
        }
    }
    private func resolve(_ kind: String) {
        guard let client = workspace.client, let target, let revision = incoming.memoryRevision,
              let targetRevision = target.memoryRevision else { return }
        busy = true; retryDecision = kind
        struct Body: Encodable {
            let targetID: UUID; let memoryRevision: Int; let targetRevision: Int
            let relation: String; let requestID: UUID
        }
        let body = Body(targetID: target.id, memoryRevision: revision, targetRevision: targetRevision, relation: kind, requestID: requestID)
        Task {
            defer { busy = false }
            do {
                let data = try await client.sendData("v1/candidates/\(candidate.id.uuidString)/resolve", method: "POST", body: JSONEncoder().encode(body))
                let updated = try JSONDecoder().decode(NodeCandidate.self, from: data)
                workspace.memoryNotice = "记忆对照已保存，原文与历史保留。"
                await workspace.libraryChanged(); saved(updated); dismiss()
            } catch {
                problem = error.localizedDescription
                if let code = (error as? NodeFailure)?.statusCode, code >= 500 { await workspace.refresh() }
                // A definite client rejection cannot be repaired by repeating an old decision.
                // For transport/server failures keep its ID to recover an unknown commit outcome.
                if let code = (error as? NodeFailure)?.statusCode, (400..<500).contains(code), code != 408, code != 429 {
                    retryDecision = nil
                }
            }
        }
    }
}
