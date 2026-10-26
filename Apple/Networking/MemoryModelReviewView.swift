import SwiftUI

struct MemoryReviewPair: Codable, Sendable {
    let incomingID: UUID
    let targetID: UUID
    let incomingRevision: Int
    let targetRevision: Int
}

struct MemoryReviewReference: Codable, Identifiable, Sendable {
    let id: UUID
    let model: String
    let createdAt: String
    let completedAt: String?
    let relation: String
    var title: String { memoryRelationTitle(relation) }
    var date: Date? { ISO8601DateFormatter().date(from: completedAt ?? createdAt) }
}

private func memoryRelationTitle(_ relation: String) -> String {
    switch relation {
    case "duplicate": "可能重复"
    case "supplement": "可能形成补充"
    case "conflict": "可能存在冲突"
    case "unrelated": "可能属于不同事项"
    default: "依据不足，无法判断关系"
    }
}

private struct MemoryReviewState: Decodable, Sendable {
    struct Side: Decodable, Identifiable, Sendable {
        let side: String
        let id: UUID
        let memoryRevision: Int
        let text: String
        let evidence: [MemoryEvidence]
        var title: String { side == "incoming" ? "新建议" : "已有记忆" }
    }
    struct Result: Decodable, Sendable {
        struct Citation: Decodable, Sendable {
            let side: String
            let evidence: MemoryEvidence
        }
        let relation: String
        let reason: String
        let citations: [Citation]
    }
    let id: UUID
    let status: String
    let result: Result?
    let sides: [Side]
    let characters: Int
    let limit: Int
    let configured: Bool
    let model: String
    let endpoint: String
    let current: Bool
    let problem: String?
    let createdAt: String?
    let completedAt: String?
    let previewToken: String?
    let canSend: Bool
}

/// The preview is read-only. Only the explicit send action starts a model request.
struct MemoryModelReviewView: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    var pair: MemoryReviewPair? = nil
    var reviewID: UUID? = nil
    @Environment(\.dismiss) private var dismiss
    #if os(macOS)
    @Environment(\.openSettings) private var openSettings
    #endif
    @State private var state: MemoryReviewState?
    @State private var evidence: MemoryEvidence?
    @State private var reviewingModelCitation = false
    @State private var error: String?
    @State private var busy = false
    @State private var refreshID = UUID()
    private var offersPreview: Bool {
        pair != nil && state?.configured == true && state?.result == nil && state?.status != "running" && state?.previewToken == nil
    }

    var body: some View {
        NavigationStack {
            Form {
                if let error {
                    Section {
                        Text(L(error)).foregroundStyle(.secondary)
                        Button("重新读取") { refreshID = UUID() }.disabled(busy)
                    }
                }
                if let state {
                    if !state.current {
                        Section { Text("这是先前版本的模型核对；记忆或依据已变化，请重新核对当前内容。")
                            .foregroundStyle(.secondary) }
                    }
                    if let result = state.result { resultSections(result, state: state) }
                    else { requestSection(state) }
                    if state.result == nil {
                        ForEach(state.sides) { side in sourceSection(side) }
                    } else {
                        Section {
                            DisclosureGroup("当时发送的范围") {
                                ForEach(state.sides) { side in
                                    VStack(alignment: .leading, spacing: 12) {
                                        Text(L(side.title)).font(.headline)
                                        sourceContent(side)
                                    }.padding(.vertical, 8)
                                }
                            }
                        }
                    }
                    Section {
                        Text("模型关系建议不是事实判断。引用准确也不代表结论正确；请核对人物、时间与原文后自行处理。")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                } else if error == nil { ProgressView("正在读取") }
                if error == nil && !offersPreview && state?.status != "running" {
                    Section { Button("重新读取") { refreshID = UUID() }.disabled(busy) }
                }
            }.formStyle(.grouped)
                #if os(iOS)
                .navigationTitle(L("关系核对"))
                .navigationBarTitleDisplayMode(.inline)
                #else
                .navigationTitle(L("模型关系核对"))
                #endif
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() }.disabled(busy) } }
        }
        .task(id: workspace.endpoint + refreshID.uuidString) {
            repeat {
                await load()
                guard state?.status == "running", error == nil else { break }
                do { try await Task.sleep(for: .seconds(2)) } catch { break }
            } while !Task.isCancelled
        }
        .sheet(item: $evidence) { item in
            MemoryEvidenceReader(workspace: workspace, evidence: item, origin: reviewingModelCitation ? .review : .memory).appLanguage()
        }
        .interactiveDismissDisabled(busy)
        #if os(macOS)
        .frame(minWidth: 560, idealWidth: 680, minHeight: 580)
        #endif
    }

    @ViewBuilder private func requestSection(_ value: MemoryReviewState) -> some View {
        Section("发送范围") {
            VStack(alignment: .leading, spacing: 8) {
                Text(LF("所选文字共 %d 个字符，包含下方两条记忆及其依据。", value.characters)).font(.callout)
                Text("不发送整份来源、其他记忆或历史核对。模型不会替你确认或停用记忆。")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            if value.status == "running" {
                ProgressView("正在核对记忆关系…")
                Text("可以关闭窗口，稍后从这组记忆查看结果。")
                    .font(.footnote).foregroundStyle(.secondary)
            } else if pair != nil {
                if value.configured {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(verbatim: value.model).font(.headline)
                        Text(verbatim: value.endpoint).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                    }
                    if value.previewToken == nil {
                        Button("重新预览发送范围") { state = nil; refreshID = UUID() }.disabled(busy)
                    } else {
                        Button(value.status == "idle" ? "发送所选记忆并核对" : "重新预览后重试核对") { start() }
                            .buttonStyle(FilledButtonStyle()).disabled(busy || !value.canSend || !value.current)
                    }
                } else {
                    Text("请先在电脑设置中配置文字模型。").foregroundStyle(.secondary)
                    #if os(macOS)
                    Button("配置文字模型") {
                        UserDefaults.standard.set("模型", forKey: "settings.section")
                        UserDefaults.standard.set("summary", forKey: "model.focusRole")
                        openSettings()
                    }
                    #endif
                }
            }
            if let problem = value.problem { Text(L(problem)).foregroundStyle(.secondary) }
        }
    }

    @ViewBuilder private func resultSections(_ result: MemoryReviewState.Result, state: MemoryReviewState) -> some View {
        Section("模型关系建议") {
            VStack(alignment: .leading, spacing: 12) {
                Label(L(memoryRelationTitle(result.relation)), systemImage: "text.bubble").font(.headline)
                Text(verbatim: result.reason).textSelection(.enabled)
                VStack(alignment: .leading, spacing: 6) {
                    Text(LF("由模型提出：%@", state.model))
                    Text(verbatim: state.endpoint).textSelection(.enabled)
                    if let rawDate = state.completedAt ?? state.createdAt, let date = ISO8601DateFormatter().date(from: rawDate) {
                        Text(date, format: .dateTime.year().month().day().hour().minute())
                    }
                }.font(.caption).foregroundStyle(.secondary)
                Text("此版本的核对已保存；再次打开不会重复发送。请返回新旧对照页决定如何处理。")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        if !result.citations.isEmpty {
            Section("模型引用的依据") {
                ForEach(Array(result.citations.enumerated()), id: \.offset) { _, citation in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(L(citation.side == "incoming" ? "新建议" : "已有记忆")).font(.caption).foregroundStyle(.secondary)
                        Text(verbatim: citation.evidence.quote).textSelection(.enabled)
                        quoteButton(citation.evidence, fromModel: true)
                    }.padding(.vertical, 4)
                }
            }
        }
    }

    @ViewBuilder private func sourceSection(_ side: MemoryReviewState.Side) -> some View {
        Section(L(side.title)) { sourceContent(side) }
    }

    private func sourceContent(_ side: MemoryReviewState.Side) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(verbatim: side.text).textSelection(.enabled)
            Text(LF("记忆版本 %d", side.memoryRevision)).font(.caption).foregroundStyle(.secondary)
            ForEach(side.evidence, id: \.identity) { item in
                VStack(alignment: .leading, spacing: 8) {
                    Text(verbatim: item.title).font(.subheadline.weight(.medium))
                    Text(verbatim: item.quote).textSelection(.enabled)
                    quoteButton(item, fromModel: false)
                }.padding(.vertical, 4)
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private func quoteButton(_ item: MemoryEvidence, fromModel: Bool) -> some View {
        Button { reviewingModelCitation = fromModel; evidence = item } label: {
            Text("核对引用上下文").frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
        }.buttonStyle(.plain)
    }

    private func load() async {
        guard let client = workspace.client else { error = "请先连接资料库。"; return }
        do {
            let result: MemoryReviewState
            if let active = state, active.status == "running" || active.result != nil {
                result = try await client.read("v1/memory-reviews/" + active.id.uuidString, as: MemoryReviewState.self)
            } else if let pair {
                struct Request: Encodable { let pair: MemoryReviewPair }
                let data = try await client.sendData("v1/memory-reviews/preview", method: "POST", body: JSONEncoder().encode(Request(pair: pair)))
                result = try JSONDecoder().decode(MemoryReviewState.self, from: data)
            } else if let reviewID {
                result = try await client.read("v1/memory-reviews/" + reviewID.uuidString, as: MemoryReviewState.self)
            } else { return }
            guard !Task.isCancelled else { return }
            let becameReady = state?.status != "ready" && result.status == "ready"
            state = result; error = nil
            if becameReady { await workspace.libraryChanged() }
        } catch {
            guard !Task.isCancelled else { return }
            self.error = error.localizedDescription
        }
    }

    private func start() {
        guard let client = workspace.client, let pair, let token = state?.previewToken else { return }
        struct Request: Encodable { let pair: MemoryReviewPair; let previewToken: String; let sendToModel: Bool }
        busy = true; error = nil
        Task {
            defer { busy = false }
            do {
                let data = try await client.sendData("v1/memory-reviews", method: "POST", body: JSONEncoder().encode(Request(pair: pair, previewToken: token, sendToModel: true)))
                state = try JSONDecoder().decode(MemoryReviewState.self, from: data)
                refreshID = UUID()
            } catch { self.error = error.localizedDescription }
        }
    }
}
