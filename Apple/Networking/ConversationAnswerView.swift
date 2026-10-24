import SwiftUI

struct GroundedAnswer: Codable, Sendable {
    struct Statement: Codable, Sendable {
        let text: String
        let citations: [KnowledgeSource]
    }
    let id: String
    let model: String
    let endpoint: String
    let createdAt: String
    let insufficient: Bool
    let statements: [Statement]
    let selectedSources: [KnowledgeSource]
}

struct AnswerJob: Codable, Sendable {
    let id: String
    let status: String
    let problem: String?
    let model: String
    let endpoint: String
    let sources: [String]
    let attempts: Int
    var running: Bool { status == "running" }
}

private struct AnswerRuntime: Decodable, Sendable {
    let job: AnswerJob?
    let answer: GroundedAnswer?
    let configured: Bool
}

struct ConversationAnswerSection: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let conversationID: String
    let turn: ConversationTurn
    @State private var runtime: AnswerRuntime?
    @State private var choosing = false
    @State private var citation: KnowledgeSource?
    @State private var source: KnowledgeSource?
    @State private var problem: String?
    @State private var refresh = 0
    private var answer: GroundedAnswer? { runtime?.answer ?? turn.response.answer }
    private var job: AnswerJob? { runtime?.job ?? turn.answerJob }
    private var path: String { "v1/conversations/" + conversationID + "/turns/" + turn.id + "/answer" }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let answer {
                Label("模型回答", systemImage: "sparkles").font(.headline)
                if answer.insufficient {
                    Text("所选片段不足以回答这个问题。可以补充资料后重新查询。").foregroundStyle(.secondary)
                } else {
                    ForEach(Array(answer.statements.enumerated()), id: \.offset) { _, statement in
                        VStack(alignment: .leading, spacing: 10) {
                            Text(verbatim: statement.text).textSelection(.enabled).lineSpacing(5)
                            ForEach(Array(statement.citations.enumerated()), id: \.offset) { index, value in
                                Button { citation = value } label: {
                                    VStack(alignment: .leading, spacing: 6) {
                                        Label(LF("引用 %d · %@", index + 1, value.title), systemImage: "quote.opening")
                                            .font(.callout.weight(.medium))
                                        Text(verbatim: value.excerpt ?? "").font(.callout).lineLimit(2).foregroundStyle(.secondary)
                                    }.frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                                }.buttonStyle(.plain)
                            }
                        }
                    }
                }
                Text("模型生成，请核对结论与原文。回答不会自动成为记忆。").font(.footnote).foregroundStyle(.secondary)
                DisclosureGroup("本次回答的范围") {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(verbatim: answer.model).font(.callout)
                        Text(verbatim: answer.endpoint).font(.caption).textSelection(.enabled)
                        ForEach(answer.selectedSources, id: \.identity) { value in
                            Button { source = value } label: { Label(value.title, systemImage: value.symbol).frame(minHeight: 44) }
                        }
                        Text("这次发送了当前问题与所选片段。历史对话未自动发送。")
                            .font(.footnote).foregroundStyle(.secondary)
                    }.padding(.top, 8)
                }.font(.callout)
            } else if job?.running == true {
                if problem == nil {
                    ProgressView("正在根据所选片段生成…")
                    Text("可以离开此页，稍后从这段对话查看回答。").font(.footnote).foregroundStyle(.secondary)
                }
            } else if problem == nil && !turn.response.sources.isEmpty {
                if let issue = job?.problem { Text(L(issue)).font(.callout).foregroundStyle(.secondary) }
                Button(job == nil ? "选择片段并回答" : "核对范围并重试") { choosing = true }
                    .buttonStyle(.bordered).disabled(!workspace.connected)
            }
            if let problem {
                Text(L(problem)).font(.callout).foregroundStyle(.secondary)
                Button("重新读取回答") { refresh += 1 }
            }
        }
        .sheet(isPresented: $choosing, onDismiss: { refresh += 1 }) {
            AnswerScopeSheet(workspace: workspace, conversationID: conversationID, turn: turn, previous: job).appLanguage()
        }
        .sheet(item: $citation) { value in
            SourcePassageReader(workspace: workspace, source: value, quote: value.excerpt ?? "",
                quoteStart: value.excerptStart, origin: .answer).appLanguage()
        }
        .sheet(item: $source) { value in
            SourcePassageReader(workspace: workspace, source: value, quote: value.excerpt ?? "",
                quoteStart: value.excerptStart, origin: .search).appLanguage()
        }
        .task(id: workspace.endpoint + "/" + String(refresh)) {
            guard refresh > 0 || turn.answerJob?.running == true else { return }
            await load()
        }
    }
    @MainActor private func load() async {
        guard let client = workspace.client else { return }
        repeat {
            do {
                let state = try await client.read(path, as: AnswerRuntime.self)
                try Task.checkCancellation()
                guard client.endpoint == workspace.client?.endpoint else { return }
                runtime = state; problem = nil
                guard state.job?.running == true else { return }
                try await Task.sleep(for: .seconds(2))
            } catch is CancellationError { return }
            catch { problem = "暂时无法读取回答状态。请检查资料库连接后重新读取。"; return }
        } while !Task.isCancelled
    }
}

private struct AnswerPlan: Decodable, Sendable {
    struct Item: Decodable, Sendable, Identifiable {
        let identity: String
        let source: KnowledgeSource?
        let title: String?
        let problem: String?
        var id: String { identity }
    }
    let question: String
    let items: [Item]
    let model: String
    let endpoint: String
    let snapshot: String
    let configured: Bool
    let characters: Int
    var valid: Bool { configured && !items.isEmpty && items.allSatisfy { $0.source != nil && $0.problem == nil } }
}

private struct AnswerScopeSheet: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let conversationID: String
    let turn: ConversationTurn
    let previous: AnswerJob?
    @Environment(\.dismiss) private var dismiss
    @State private var selection = Set<String>()
    @State private var plan: AnswerPlan?
    @State private var planEndpoint: URL?
    @State private var busy = false
    @State private var problem: String?
    @State private var inspecting: KnowledgeSource?
    @State private var initialized = false
    @State private var previewRevision = 0
    #if os(macOS)
    @Environment(\.openSettings) private var openSettings
    #endif
    private var path: String { "v1/conversations/" + conversationID + "/turns/" + turn.id + "/" }
    var body: some View {
        NavigationStack {
            ScrollViewReader { scroller in
            Form {
                Section("这次回答的问题") {
                    Text(verbatim: turn.question).textSelection(.enabled)
                    Text("选择要发送的片段，最多 8 个；每个片段最多 1400 个字符。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("所选片段") {
                    Text(LF("已选择 %d / 8 个片段", selection.count))
                    ForEach(turn.response.sources, id: \.identity) { value in
                        passageRow(value)
                    }
                }
                if let plan {
                    Section("发送预览") {
                        Text(LF("将发送当前问题及 %d 个片段，共 %d 个来源字符。", plan.items.count, plan.characters))
                        Text("仅发送勾选片段的文字；模型使用可能产生费用。回答会保留引用，仍需核对。")
                            .font(.footnote).foregroundStyle(.secondary)
                        if plan.configured {
                            Text(verbatim: plan.model).font(.headline)
                            Text(verbatim: plan.endpoint).font(.caption).textSelection(.enabled)
                        } else {
                            Text("请先在电脑设置中单独配置回答模型。")
                            #if os(macOS)
                            Button("配置回答模型") {
                                UserDefaults.standard.set("模型", forKey: "settings.section")
                                UserDefaults.standard.set("answer", forKey: "model.focusRole")
                                openSettings()
                            }
                            #endif
                        }
                        ForEach(plan.items) { item in
                            if let source = item.source {
                                VStack(alignment: .leading, spacing: 4) {
                                    Label(source.title, systemImage: source.symbol).font(.callout)
                                    if source.kind == "document" {
                                        Text(source.documentArchiveLabel).font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                            } else if let issue = item.problem {
                                Text(verbatim: (item.title ?? item.source?.title ?? "") + "\n" + L(issue))
                                    .font(.callout).foregroundStyle(.secondary)
                            }
                        }
                        if previous != nil {
                            Text("服务未返回结果的请求，重试可能再次计费。").font(.footnote).foregroundStyle(.secondary)
                        }
                        Button("发送问题与所选片段") { start(plan) }
                            .buttonStyle(FilledButtonStyle()).disabled(busy || !plan.valid)
                    }.id("answer-preview")
                }
                Section {
                    if let problem { Text(L(problem)).font(.callout).foregroundStyle(.secondary) }
                    Button(busy ? "正在读取" : "核对回答发送范围") { preview() }
                        .disabled(busy || selection.isEmpty)
                }.id("answer-actions")
            }.formStyle(.grouped).navigationTitle(L("回答范围"))
                #if os(iOS)
                .navigationBarTitleDisplayMode(.inline)
                #endif
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() }.disabled(busy) } }
                .onAppear {
                    guard !initialized else { return }
                    initialized = true
                    let valid = turn.response.sources.filter(usable).map(\.identity)
                    selection = Set((previous?.sources ?? Array(valid.prefix(8))).filter { valid.contains($0) })
                }
                .onChange(of: workspace.status?.answerConfigured) { _, _ in plan = nil }
                .onChange(of: workspace.endpoint) { _, _ in
                    plan = nil; planEndpoint = nil; problem = "资料库连接已变化，请重新打开这轮对话。"
                }
                .onChange(of: previewRevision) { _, _ in scroller.scrollTo("answer-preview", anchor: .top) }
                .onChange(of: problem) { _, value in
                    if value != nil { scroller.scrollTo("answer-actions", anchor: .top) }
                }
            }
        }
        .sheet(item: $inspecting) { value in
            SourcePassageReader(workspace: workspace, source: value, quote: value.excerpt ?? "",
                quoteStart: value.excerptStart, origin: .search).appLanguage()
        }
        #if os(macOS)
        .frame(minWidth: 580, idealWidth: 720, minHeight: 600, idealHeight: 720)
        #endif
    }
    private func selectionLabel(_ source: KnowledgeSource) -> String {
        var label = LF("选择片段：%@", source.title)
        if source.kind == "document" {
            label += ", " + source.documentArchiveLabel
            if let revision = source.documentRevisionLabel(snapshot: true) { label += ", " + revision }
        }
        return label
    }
    private func passageRow(_ value: KnowledgeSource) -> some View {
        let selected = selection.contains(value.identity)
        return VStack(alignment: .leading, spacing: 8) {
            Button { toggle(value.identity) } label: {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                    Text(verbatim: value.title).fixedSize(horizontal: false, vertical: true)
                }.frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            }.buttonStyle(.plain)
                .disabled(busy || !usable(value) || (selection.count >= 8 && !selected))
                .accessibilityLabel(selectionLabel(value))
                .accessibilityValue(L(selected ? "已选择" : "未选择"))
                .accessibilityIdentifier("answer.source." + value.identity)
            Text(L(value.kindTitle) + " · " + value.date).font(.caption).foregroundStyle(.secondary)
            DocumentRevisionNotice(source: value, snapshot: true)
            if let notice = value.analysisNotice { Text(L(notice)).font(.footnote).foregroundStyle(.secondary) }
            if usable(value) {
                Text(verbatim: value.excerpt ?? "").font(.callout).foregroundStyle(.secondary).lineLimit(2)
                DisclosureGroup("查看本次片段全文") {
                    Text(verbatim: value.excerpt ?? "").textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }.font(.callout)
                Button("核对原文位置") { inspecting = value }.disabled(busy)
            } else {
                Text("这轮检索缺少有效片段位置，请重新查询这个问题。")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }.padding(.vertical, 4)
    }
    private func usable(_ source: KnowledgeSource) -> Bool {
        source.textAvailable && source.excerptStart != nil && !(source.excerpt ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
    private func toggle(_ id: String) {
        if selection.contains(id) { selection.remove(id) }
        else if selection.count < 8 { selection.insert(id) }
        plan = nil; problem = nil
    }
    private func preview() {
        guard let client = workspace.client else { return }
        busy = true; problem = nil
        struct Body: Encodable { let sources: [String] }
        Task {
            defer { busy = false }
            do {
                let data = try await client.sendData(path + "answer-preview", method: "POST",
                    body: JSONEncoder().encode(Body(sources: selection.sorted())))
                guard client.endpoint == workspace.client?.endpoint else { throw NodeFailure(message: "资料库连接已变化，请重新打开这轮对话。") }
                plan = try JSONDecoder().decode(AnswerPlan.self, from: data); planEndpoint = client.endpoint
                previewRevision += 1
            } catch { plan = nil; problem = error.localizedDescription }
        }
    }
    private func start(_ plan: AnswerPlan) {
        guard let client = workspace.client, client.endpoint == planEndpoint else {
            self.plan = nil; problem = "资料库连接已变化，请重新打开这轮对话。"; return
        }
        busy = true; problem = nil
        struct Body: Encodable { let sources: [String]; let snapshot: String; let sendToModel: Bool }
        Task {
            defer { busy = false }
            do {
                _ = try await client.sendData(path + "answer", method: "POST", body: JSONEncoder().encode(
                    Body(sources: plan.items.map(\.identity), snapshot: plan.snapshot, sendToModel: true)))
                dismiss()
            } catch {
                problem = error.localizedDescription
                if (error as? NodeFailure)?.statusCode == 409 { self.plan = nil }
            }
        }
    }
}
