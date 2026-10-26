import SwiftUI

private struct ReviewBatchItem: Decodable, Identifiable {
    struct Selection: Decodable { let sides: [MemoryReviewState.Side]; let characters: Int }
    let pair: MemoryReviewPair
    let selection: Selection?
    let reviewID: UUID?
    let reusable: Bool
    let problem: String?
    let status: String?
    let relation: String?
    var id: String { pair.incomingID.uuidString + pair.targetID.uuidString }
}
private struct ReviewBatchPlan: Decodable {
    let items: [ReviewBatchItem]; let model: String; let endpoint: String
    let snapshot: String; let configured: Bool; let newRequests: Int; let characters: Int
    var canSend: Bool { !items.isEmpty && !items.contains { $0.problem != nil } && (configured || newRequests == 0) }
}
private struct ReviewBatchState: Decodable, Identifiable {
    let id: UUID; let status: String; let problem: String?; let items: [ReviewBatchItem]
    let model: String; let endpoint: String; let createdAt: String; let finishedCount: Int; let pairCount: Int
    var running: Bool { status == "running" || status == "pausing" }
}
private struct ReviewChoice: Identifiable {
    let pair: MemoryReviewPair; let incoming: String; let existing: String
    var id: String { pair.incomingID.uuidString + pair.targetID.uuidString }
}
private func reviewBatchTitle(_ state: String) -> String {
    switch state {
    case "ready": "已完成"; case "running": "正在核对记忆关系…"
    case "pausing": "完成当前配对后暂停"; case "paused": "已暂停"
    case "partial", "failed": "部分配对需要处理"; default: "等待处理"
    }
}

/// Local pairing, then one explicit approval for the immutable batch scope.
struct MemoryReviewBatchView: View {
    @ObservedObject var store: NodeWorkspaceStore
    #if os(macOS)
    @Environment(\.openSettings) private var openSettings
    #endif
    @State private var choices: [ReviewChoice] = []
    @State private var history: [ReviewBatchState] = []
    @State private var state: ReviewBatchState?
    @State private var plan: ReviewBatchPlan?
    @State private var picking = false
    @State private var comparing: NodeCandidate?
    @State private var comparisonTarget: UUID?
    @State private var review: MemoryReviewReference?
    @State private var evidence: MemoryEvidence?
    @State private var busy = false
    @State private var problem: String?
    @State private var requestID = UUID()
    @State private var polling = UUID()
    private var connectionScope: String { store.endpoint + (store.status?.nodeID ?? "") + store.connectionRevision.uuidString }

    var body: some View {
        ScrollViewReader { proxy in
            Form {
                if let problem {
                    Section { Text(L(problem)).foregroundStyle(.secondary) }.id("reviewProblem")
                }
                if let state, plan == nil {
                    Section {
                        Text(L(reviewBatchTitle(state.status))).font(.headline)
                        Text(LF("已核对 %d / %d 组记忆", state.finishedCount, state.items.count))
                        Text(verbatim: state.model).font(.caption).foregroundStyle(.secondary)
                        Button("重新读取") { openBatch(state.id) }.disabled(busy)
                        if let message = state.problem { Text(L(message)).foregroundStyle(.secondary) }
                        if state.running {
                            ProgressView()
                            Button("暂停后续配对") { pause() }.disabled(busy || state.status == "pausing")
                            Text("暂停会完成当前请求，之后不再发送其他配对。可以关闭窗口，稍后从这里继续。")
                                .font(.footnote).foregroundStyle(.secondary)
                        } else if state.status != "ready" {
                            Button("预览剩余配对") { preview() }.disabled(busy)
                            Button("重新选择未完成配对") { reselect(state) }.disabled(busy)
                        }
                        if !state.running {
                            Button("返回批量核对列表") { self.state = nil; plan = nil; problem = nil; Task { await loadHistory() } }
                        }
                    }
                }
                if let plan { previewSection(plan) }
                if let state, plan == nil {
                    ForEach(state.items) { item in
                        Section {
                            itemContent(item)
                            Text(L(item.status == "failed" ? "未完成" : reviewBatchTitle(item.status ?? "queued"))).font(.caption)
                            if let relation = item.relation { Text(L(memoryRelationTitle(relation))).font(.headline) }
                            if let id = item.reviewID, item.status == "ready" {
                                Button("查看模型核对记录") {
                                    review = .init(id: id, model: state.model, createdAt: state.createdAt, completedAt: nil, relation: item.relation ?? "uncertain")
                                }
                                Button("核对后处理") { openComparison(item) }.disabled(busy || state.running)
                            }
                        }
                    }
                } else if plan == nil {
                    Section("选择配对") {
                        Text("每次最多 20 组。先在本机选择新建议与已有记忆，集中预览后才发送给模型。")
                            .font(.footnote).foregroundStyle(.secondary)
                        ForEach(choices) { choice in
                            VStack(alignment: .leading, spacing: 8) {
                                Text(verbatim: choice.incoming)
                                Text(verbatim: choice.existing).foregroundStyle(.secondary)
                                Button("移除配对") { choices.removeAll { $0.id == choice.id }; requestID = UUID() }.disabled(busy)
                            }.padding(.vertical, 6)
                        }
                        Button("添加记忆配对") { picking = true }.disabled(busy || choices.count >= 20)
                        Button("预览批量核对范围") { preview() }.disabled(busy || choices.isEmpty)
                    }
                    Section("最近的批量核对") {
                        if history.isEmpty { Text("还没有批量核对任务。").foregroundStyle(.secondary) }
                        ForEach(history) { batch in
                            Button { openBatch(batch.id) } label: {
                                VStack(alignment: .leading, spacing: 8) {
                                    Text(L(reviewBatchTitle(batch.status)))
                                    Text(LF("已核对 %d / %d 组记忆", batch.finishedCount, batch.pairCount)).font(.caption)
                                    if let date = ISO8601DateFormatter().date(from: batch.createdAt) {
                                        Text(date, format: .dateTime.month().day().hour().minute()).font(.caption)
                                    }
                                }.frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                            }.buttonStyle(.plain)
                        }
                        Text("这里显示最近 20 次任务。").font(.footnote).foregroundStyle(.secondary)
                        Button("重新读取") { Task { await loadHistory() } }.disabled(busy)
                    }
                }
                if busy { ProgressView("正在读取") }
                Section {
                    Text("模型关系建议不是事实判断。引用准确也不代表结论正确；请核对人物、时间与原文后自行处理。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }.formStyle(.grouped)
                .id((state?.id.uuidString ?? "new") + (plan?.snapshot ?? "state"))
                .onChange(of: problem) { _, value in if value != nil { proxy.scrollTo("reviewProblem", anchor: .top) } }

        }
        .navigationTitle(L("批量核对记忆关系"))
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .task(id: connectionScope) {
            busy = false; picking = false; comparing = nil; review = nil; evidence = nil
            choices = []; plan = nil; state = nil; requestID = UUID(); await loadHistory()
        }
        .task(id: polling) {
            guard let id = state?.id else { return }
            let scope = connectionScope
            repeat {
                do {
                    guard let client = store.client else { return }
                    let updated = try await client.read("v1/memory-review-batches/" + id.uuidString, as: ReviewBatchState.self)
                    guard !Task.isCancelled, state?.id == id, scope == connectionScope else { return }
                    state = updated
                    guard updated.running else { await store.libraryChanged(); break }
                    try await Task.sleep(for: .seconds(2))
                } catch { if scope == connectionScope && !Task.isCancelled { problem = error.localizedDescription }; break }
            } while !Task.isCancelled
        }
        .sheet(isPresented: $picking) { ReviewPendingPicker(store: store) { add($0, $1) }.appLanguage() }
        .sheet(item: $review) { value in MemoryModelReviewView(workspace: store, reviewID: value.id).appLanguage() }
        .sheet(item: $evidence) { value in MemoryEvidenceReader(workspace: store, evidence: value).appLanguage() }
        .sheet(item: $comparing) { candidate in
            MemoryComparisonSheet(workspace: store, candidate: candidate, saved: { _ in polling = UUID() }, initialTargetID: comparisonTarget).appLanguage()
        }
    }

    private func itemContent(_ item: ReviewBatchItem) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            if let selection = item.selection {
                ForEach(selection.sides) { side in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(L(side.title)).font(.caption).foregroundStyle(.secondary)
                        Text(verbatim: side.text).textSelection(.enabled)
                        Text(LF("记忆版本 %d", side.memoryRevision)).font(.caption).foregroundStyle(.secondary)
                        DisclosureGroup("原文依据") {
                            ForEach(side.evidence, id: \.identity) { value in
                                VStack(alignment: .leading, spacing: 8) {
                                    Text(verbatim: value.title).font(.subheadline)
                                    Text(verbatim: value.quote).textSelection(.enabled)
                                    Button("核对引用上下文") { evidence = value }
                                }.padding(.vertical, 6)
                            }
                        }
                    }
                }
            } else {
                Text(choices.first { $0.id == item.id }?.incoming ?? item.pair.incomingID.uuidString)
            }
            if let problem = item.problem { Text(L(problem)).foregroundStyle(.secondary) }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private func previewSection(_ plan: ReviewBatchPlan) -> some View {
        Section("发送范围") {
            Text(LF("共 %d 组记忆，预计新增 %d 次请求，所选文字 %d 个字符。", plan.items.count, plan.newRequests, plan.characters))
            Text(verbatim: plan.model).font(.headline)
            Text(verbatim: plan.endpoint).font(.caption).textSelection(.enabled)
            Text("仅发送下方所选记忆与依据；已保存结果复用。中断后的未知结果可能需要重新请求并计费。")
                .font(.footnote).foregroundStyle(.secondary)
            if !plan.configured && plan.newRequests > 0 {
                Text("请先在电脑设置中配置文字模型。")
                #if os(macOS)
                Button("配置文字模型") {
                    UserDefaults.standard.set("模型", forKey: "settings.section")
                    UserDefaults.standard.set("summary", forKey: "model.focusRole"); openSettings()
                }
                #endif
            }
            Button(plan.newRequests == 0 ? "复用已保存核对" : "发送所选配对并核对") { send(plan) }.disabled(busy || !plan.canSend)
            Button(state == nil ? "返回修改选择" : "返回任务状态") { self.plan = nil; problem = nil }.disabled(busy)
            Button("重新预览发送范围") { preview() }.disabled(busy)
        }
        ForEach(plan.items) { item in
            Section { itemContent(item); if item.reusable { Text("复用已保存核对").font(.caption) } }
        }
    }

    private func add(_ incoming: NodeCandidate, _ existing: NodeCandidate) {
        guard let left = incoming.memoryRevision, let right = existing.memoryRevision else { return }
        let pair = MemoryReviewPair(incomingID: incoming.id, targetID: existing.id, incomingRevision: left, targetRevision: right)
        let value = ReviewChoice(pair: pair, incoming: incoming.text, existing: existing.text)
        if !choices.contains(where: { $0.id == value.id }) && choices.count < 20 { choices.append(value); requestID = UUID() }
    }
    private func openBatch(_ id: UUID) {
        guard let client = store.client else { return }
        busy = true; problem = nil
        let scope = connectionScope
        Task {
            defer { if scope == connectionScope { busy = false } }
            do {
                let result = try await client.read("v1/memory-review-batches/" + id.uuidString, as: ReviewBatchState.self)
                guard scope == connectionScope, !Task.isCancelled else { return }
                state = result
                plan = nil; polling = UUID()
            } catch { if scope == connectionScope && !Task.isCancelled { problem = error.localizedDescription } }
        }
    }
    private func loadHistory() async {
        guard let client = store.client else { problem = "请先连接资料库。"; return }
        let scope = connectionScope
        do {
            struct Page: Decodable { let items: [ReviewBatchState] }
            let result = try await client.read("v1/memory-review-batches", as: Page.self)
            guard scope == connectionScope, !Task.isCancelled else { return }
            history = result.items; problem = nil
        } catch { if scope == connectionScope && !Task.isCancelled { problem = error.localizedDescription } }
    }
    private func preview() {
        guard let client = store.client else { problem = "请先连接资料库。"; return }
        busy = true; problem = nil
        let scope = connectionScope
        Task {
            defer { if scope == connectionScope { busy = false } }
            do {
                if let state {
                    let result = try await client.read("v1/memory-review-batches/" + state.id.uuidString + "/preview", as: ReviewBatchPlan.self)
                    guard scope == connectionScope, !Task.isCancelled else { return }
                    plan = result
                }
                else {
                    struct Request: Encodable { let pairs: [MemoryReviewPair] }
                    let data = try await client.sendData("v1/memory-review-batches/preview", method: "POST", body: JSONEncoder().encode(Request(pairs: choices.map(\.pair))))
                    guard scope == connectionScope, !Task.isCancelled else { return }
                    plan = try JSONDecoder().decode(ReviewBatchPlan.self, from: data)
                }
            } catch { if scope == connectionScope && !Task.isCancelled { problem = error.localizedDescription; plan = nil } }
        }
    }
    private func send(_ plan: ReviewBatchPlan) {
        guard let client = store.client else { return }
        struct Request: Encodable { let requestID: UUID; let pairs: [MemoryReviewPair]; let snapshot: String; let sendToModel = true }
        let path = "v1/memory-review-batches" + (state.map { "/" + $0.id.uuidString + "/resume" } ?? "")
        busy = true; problem = nil
        let scope = connectionScope
        Task {
            defer { if scope == connectionScope { busy = false } }
            do {
                let data = try await client.sendData(path, method: "POST", body: JSONEncoder().encode(Request(requestID: requestID, pairs: plan.items.map(\.pair), snapshot: plan.snapshot)))
                guard scope == connectionScope, !Task.isCancelled else { return }
                state = try JSONDecoder().decode(ReviewBatchState.self, from: data)
                self.plan = nil; choices = []; requestID = UUID(); polling = UUID()
            } catch { if scope == connectionScope && !Task.isCancelled { problem = error.localizedDescription } }
        }
    }
    private func pause() {
        guard let client = store.client, let state else { return }
        busy = true
        let scope = connectionScope
        Task {
            defer { if scope == connectionScope { busy = false } }
            do {
                let data = try await client.sendData("v1/memory-review-batches/" + state.id.uuidString + "/pause", method: "POST", body: Data("{}".utf8))
                guard scope == connectionScope, !Task.isCancelled else { return }
                self.state = try JSONDecoder().decode(ReviewBatchState.self, from: data); polling = UUID()
            } catch { if scope == connectionScope && !Task.isCancelled { problem = error.localizedDescription } }
        }
    }
    private func reselect(_ batch: ReviewBatchState) {
        choices = batch.items.filter { $0.status != "ready" }.map {
            ReviewChoice(pair: $0.pair, incoming: $0.selection?.sides.first?.text ?? $0.pair.incomingID.uuidString,
                         existing: $0.selection?.sides.last?.text ?? $0.pair.targetID.uuidString)
        }
        state = nil; plan = nil; problem = nil; requestID = UUID()
    }
    private func openComparison(_ item: ReviewBatchItem) {
        guard let client = store.client else { return }
        busy = true
        let scope = connectionScope
        Task {
            defer { if scope == connectionScope { busy = false } }
            do {
                let current = try await client.read("v1/candidates/" + item.pair.incomingID.uuidString, as: NodeCandidate.self)
                guard scope == connectionScope, !Task.isCancelled else { return }
                comparisonTarget = item.pair.targetID; comparing = current
            } catch { if scope == connectionScope && !Task.isCancelled { problem = error.localizedDescription } }
        }
    }
}

private struct ReviewPendingPicker: View {
    @ObservedObject var store: NodeWorkspaceStore
    let selected: (NodeCandidate, NodeCandidate) -> Void
    @Environment(\.dismiss) private var dismiss
    @StateObject private var loader = LibraryLoader<NodeCandidate>()
    @State private var query = ""
    @State private var candidate: NodeCandidate?
    private var filter: LibraryFilter { var value = LibraryFilter(); value.state = "pending"; value.query = query; return value }
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("搜索待确认建议", text: $query)
                    ForEach(loader.items) { item in
                        Button { candidate = item } label: {
                            Text(verbatim: item.text).frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                        }.buttonStyle(.plain)
                    }
                    if !loader.loading && loader.items.isEmpty { Text("没有待确认建议。") }
                    LibraryFooter(loader: loader) { Task { await loader.more(client: store.client, kind: "memory", filter: filter) } }
                }
            }.formStyle(.grouped).navigationTitle(L("选择新建议"))
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } } }
                .task(id: filter.signature) { await loader.reload(client: store.client, kind: "memory", filter: filter) }
                .sheet(item: $candidate) { item in
                    MemoryComparisonSheet(workspace: store, candidate: item, saved: { _ in }, selectedPair: { incoming, existing in
                        selected(incoming, existing); candidate = nil; dismiss()
                    }).appLanguage()
                }
        }
        #if os(macOS)
        .frame(minWidth: 560, idealWidth: 680, minHeight: 580)
        #endif
    }
}
