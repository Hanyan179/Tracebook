import SwiftUI

struct MemoryModelProposal: Codable, Sendable {
    let origin: String
    let model: String
    let endpoint: String
    let taskID: String
    let createdAt: String
}

struct MemoryEvidence: Codable, Identifiable, Sendable {
    let kind: String
    let id: String
    let title: String
    let date: String
    let contentVersion: String
    let quote: String
    let quoteStart: Int
    let capturedAt: String
    let snapshotState: String
    var sourceStatus: String?
    var identity: String { kind + "/" + id + "/" + String(quoteStart) }
    var source: KnowledgeSource {
        KnowledgeSource(id: id, kind: kind, title: title.isEmpty ? id : title, date: date,
                        contentVersion: contentVersion, textAvailable: !quote.isEmpty)
    }
}

struct MemoryHistory: Decodable {
    let items: [MemoryVersion]
    let nextBefore: Int?
}
struct MemoryVersion: Decodable, Identifiable {
    let memoryRevision: Int
    let text: String
    let state: String
    let action: String
    let changedAt: String
    var id: Int { memoryRevision }
    var date: Date? { ISO8601DateFormatter().date(from: changedAt) }
    var actionTitle: String {
        switch action {
        case "confirmed": "确认"; case "rejected": "忽略"; case "disabled": "停用"
        case "restored": "恢复使用"; case "reopened": "重新审阅"; case "edited": "修订"
        case "migrated": "保留旧记忆"; default: "提出建议"
        }
    }
}

/// The same evidence and state controls are used on Mac and phone.
struct MemoryContentView: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let initial: NodeCandidate
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var current: NodeCandidate?
    @State private var history: [MemoryVersion] = []
    @State private var nextBefore: Int?
    @State private var editSnapshot: NodeCandidate?
    @State private var busy = false
    @State private var error: String?
    @State private var source: MemoryEvidence?
    private var value: NodeCandidate { current ?? initial }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                let heading = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: 12)) : AnyLayout(HStackLayout())
                heading {
                    Text(L(value.stateTitle)).font(.caption.weight(.medium))
                    if !typeSize.isAccessibilitySize { Spacer() }
                    Button("修订内容") { editSnapshot = value }.disabled(current == nil || busy)
                }
                Text(verbatim: value.text).font(.title3).lineSpacing(7).textSelection(.enabled)
                if let proposal = value.proposal {
                    Label(LF("由模型提出：%@", proposal.model), systemImage: "sparkles")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if value.state == "pending" {
                    Text("确认内容与归属后再保存。待确认建议不会因超时自动成为记忆。")
                        .font(.callout).foregroundStyle(.secondary)
                } else if value.state == "inactive" {
                    Text("已停用：新的检索不会使用这条记忆，原文和历史仍保留。")
                        .font(.callout).foregroundStyle(.secondary)
                }
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 14) { stateActions }
                    VStack(alignment: .leading, spacing: 14) { stateActions }
                }.disabled(current == nil || busy)
                if busy { ProgressView() }
                if let error {
                    Text(L(error)).font(.callout).foregroundStyle(.secondary)
                    Button("重新读取") { Task { await load() } }
                }
                Divider()
                Label("提出时的依据", systemImage: "link").font(.headline)
                ForEach(value.evidence ?? [], id: \.identity) { evidence in
                    VStack(alignment: .leading, spacing: 12) {
                        Label(evidence.title.isEmpty ? evidence.id : evidence.title, systemImage: evidence.source.symbol)
                            .font(.subheadline.weight(.semibold))
                        Text(L(evidence.source.kindTitle) + (evidence.date.isEmpty ? "" : " · " + evidence.date))
                            .font(.caption).foregroundStyle(.secondary)
                        if !evidence.quote.isEmpty {
                            Text(verbatim: evidence.quote).font(.body).lineSpacing(5).textSelection(.enabled)
                        } else if evidence.snapshotState == "legacy" {
                            Text("旧记忆未保存当时的文字版本，请重新核对来源。")
                                .font(.callout).foregroundStyle(.secondary)
                        } else {
                            Text("提出时尚无文字依据，请核对原音。")
                                .font(.callout).foregroundStyle(.secondary)
                        }
                        if evidence.sourceStatus == "changed" {
                            Label("原文已变化；上方保留提出时的片段。", systemImage: "clock.arrow.circlepath")
                                .font(.caption).foregroundStyle(.secondary)
                        } else if evidence.sourceStatus == "unavailable" {
                            Label("当前来源不可读取，已保存的依据仍保留。", systemImage: "exclamationmark.circle")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Button(evidence.quote.isEmpty ? "查看当前原文" : "核对引用上下文") { source = evidence }
                    }.padding(18).frame(maxWidth: .infinity, alignment: .leading)
                        .background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 14))
                }
                DisclosureGroup("修订历史") {
                    VStack(alignment: .leading, spacing: 18) {
                        ForEach(history) { item in
                            VStack(alignment: .leading, spacing: 6) {
                                HStack {
                                    Text(L(item.actionTitle))
                                    if let date = item.date { Text(date, format: .dateTime.year().month().day().hour().minute()) }
                                }.font(.caption).foregroundStyle(.secondary)
                                Text(verbatim: item.text).font(.callout).textSelection(.enabled)
                            }
                        }
                        if nextBefore != nil { Button("更早的修订") { Task { await loadHistory(more: true) } }.disabled(busy) }
                    }.padding(.top, 14).frame(maxWidth: .infinity, alignment: .leading)
                }
                Text("原文片段是核对依据，不代表其中每句话都是本人的事实。")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(24).frame(maxWidth: 900, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
        }.background(AppPalette.canvas)
            .task(id: workspace.endpoint + initial.id.uuidString + workspace.libraryRevision.uuidString) { await load() }
            .sheet(item: $source) { item in
                if item.quote.isEmpty {
                    KnowledgeSourceReader(workspace: workspace, source: item.source).appLanguage()
                } else {
                    MemoryEvidenceReader(workspace: workspace, evidence: item).appLanguage()
                }
            }
            .sheet(item: $editSnapshot) { snapshot in
                MemoryEditSheet(workspace: workspace, candidate: snapshot) { current = $0; Task { await loadHistory() } }.appLanguage()
            }
    }
    @ViewBuilder private var stateActions: some View {
        switch value.state {
        case "pending":
            Button("确认记忆") { change("confirmed") }.buttonStyle(FilledButtonStyle())
            Button("忽略") { change("rejected") }.buttonStyle(.bordered)
        case "confirmed": Button("停用记忆") { change("inactive") }.buttonStyle(.bordered)
        case "inactive": Button("恢复使用") { change("confirmed") }.buttonStyle(FilledButtonStyle())
        case "rejected": Button("重新审阅") { change("pending") }.buttonStyle(.bordered)
        default: EmptyView()
        }
    }
    private func load() async {
        guard let client = workspace.client else { error = "请先连接资料库。"; return }
        do {
            current = try await client.read("v1/candidates/" + initial.id.uuidString, as: NodeCandidate.self)
            error = nil; await loadHistory()
        } catch { self.error = error.localizedDescription }
    }
    private func loadHistory(more: Bool = false) async {
        guard let client = workspace.client else { return }
        do {
            let page = try await client.read("v1/candidates/" + initial.id.uuidString + "/history",
                                            query: more ? ["before": String(nextBefore ?? 0)] : [:], as: MemoryHistory.self)
            history = more ? history + page.items : page.items; nextBefore = page.nextBefore
        } catch { self.error = error.localizedDescription }
    }
    private func change(_ state: String) {
        busy = true; error = nil
        Task {
            defer { busy = false }
            do { current = try await workspace.updateMemory(value, state: state); await loadHistory() }
            catch { self.error = error.localizedDescription }
        }
    }
}

private struct MemoryEditSheet: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let candidate: NodeCandidate
    let saved: (NodeCandidate) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var busy = false
    @State private var error: String?
    @State private var discard = false
    @State private var requestID = UUID()
    @State private var latest: NodeCandidate?
    private var dirty: Bool { text != candidate.text }
    var body: some View {
        NavigationStack {
            Form {
                Section("记忆内容") { TextEditor(text: $text).frame(minHeight: 180).disabled(busy) }
                Text("修改会保留历史版本和提出时的依据。")
                    .font(.footnote).foregroundStyle(.secondary)
                if let error {
                    Text(L(error)).foregroundStyle(.secondary)
                    Button("读取最新记忆，保留草稿") {
                        busy = true
                        Task {
                            defer { busy = false }
                            do {
                                guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
                                latest = try await client.read("v1/candidates/" + candidate.id.uuidString, as: NodeCandidate.self)
                                requestID = UUID(); self.error = nil
                            } catch { self.error = error.localizedDescription }
                        }
                    }.disabled(busy)
                }
                if let latest {
                    Section("当前保存的内容") {
                        Text(verbatim: latest.text).textSelection(.enabled)
                        Text("草稿已保留。核对差异后，再保存修订。").font(.footnote).foregroundStyle(.secondary)
                    }
                }
            }.formStyle(.grouped).navigationTitle(L("修订记忆"))
                #if os(iOS)
                .navigationBarTitleDisplayMode(.inline)
                #endif
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("取消") { if dirty { discard = true } else { dismiss() } }.disabled(busy)
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("保存修订") {
                            busy = true
                            Task {
                                defer { busy = false }
                                do { saved(try await workspace.updateMemory(latest ?? candidate, text: text, requestID: requestID)); dismiss() }
                                catch { self.error = error.localizedDescription }
                            }
                        }.disabled(busy || !dirty || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || text.count > 10000)
                    }
                }
                .confirmationDialog("放弃未保存的修改？", isPresented: $discard, titleVisibility: .visible) {
                    Button("放弃修改", role: .destructive) { dismiss() }
                }
        }.onAppear { text = candidate.text }.onChange(of: text) { _, _ in requestID = UUID() }
            .interactiveDismissDisabled(dirty || busy)
        #if os(macOS)
        .frame(minWidth: 560, idealWidth: 680, minHeight: 420)
        #endif
    }
}

struct MemoryProposalSheet: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let source: KnowledgeSource
    let sourceText: String
    var saved: () -> Void = {}
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var text = ""
    @State private var quote = ""
    @State private var requestID = UUID()
    @State private var busy = false
    @State private var error: String?
    @State private var discard = false
    @State private var mode = "manual"
    @State private var matches = SourceQuoteMatches(text: "", quote: "")
    @State private var quoteStart: Int?
    private var dirty: Bool {
        !text.isEmpty || !quote.unicodeScalars.elementsEqual(SourceQuoteMatches.initialQuote(in: sourceText).unicodeScalars)
    }
    private var valid: Bool {
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && text.unicodeScalars.count <= 10000 &&
        quoteStart != nil && matches.items.contains { $0.start == quoteStart }
    }
    var body: some View {
        NavigationStack {
            Form {
                if typeSize.isAccessibilitySize {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("整理方式").font(.headline)
                        Menu {
                            Button("手动填写") { mode = "manual" }
                            Button("模型整理") { mode = "model" }
                        } label: {
                            HStack(alignment: .top, spacing: 12) {
                                Text(L(mode == "model" ? "模型整理" : "手动填写"))
                                    .fixedSize(horizontal: false, vertical: true)
                                Spacer(minLength: 0)
                                Image(systemName: "chevron.down").font(.caption)
                            }.frame(maxWidth: .infinity, minHeight: 44, alignment: .leading).padding(.vertical, 8)
                        }.buttonStyle(.plain).disabled(busy)
                    }.padding(.vertical, 8)
                } else {
                    modePicker.pickerStyle(.segmented).disabled(busy)
                }
                if mode == "model" {
                    MemoryExtractionSection(workspace: workspace, source: source, saved: saved)
                } else {
                    Section("希望长期记住什么") { TextEditor(text: $text).frame(minHeight: 100).disabled(busy) }
                    Section("原文依据") {
                        Label(source.title, systemImage: source.symbol)
                        TextEditor(text: $quote).frame(minHeight: 120).disabled(busy)
                        Text("保留一段连续原文，最多 4000 个字符；请核对内容与说话人。")
                            .font(.footnote).foregroundStyle(.secondary)
                        if quote.unicodeScalars.count > 4000 {
                            Text("引用超过 4000 个字符，请缩短后再保存。").font(.callout).foregroundStyle(.secondary)
                        } else if !quote.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && matches.items.isEmpty {
                            Text("当前已读原文中没有这段文字，请保留原文的字词与标点。").font(.callout).foregroundStyle(.secondary)
                        }
                    }
                    if matches.items.count > 1 {
                        Section("选择引用位置") {
                            Text("当前已读原文中有多处相同片段。请核对上下文，选择对应的说话人或内容。")
                                .font(.callout).foregroundStyle(.secondary)
                            ForEach(Array(matches.items.enumerated()), id: \.element.id) { index, match in
                                Button {
                                    quoteStart = match.start; requestID = UUID()
                                } label: {
                                    VStack(alignment: .leading, spacing: 8) {
                                        Label(LF("第 %d 处", index + 1), systemImage: quoteStart == match.start ? "checkmark.circle.fill" : "circle")
                                            .font(.headline)
                                        Text(verbatim: match.before).foregroundStyle(.secondary)
                                        Text(verbatim: quote.count > 160 ? String(quote.prefix(120)) + "…" + String(quote.suffix(40)) : quote)
                                            .fontWeight(.medium)
                                        Text(verbatim: match.after).foregroundStyle(.secondary)
                                    }.font(.callout).fixedSize(horizontal: false, vertical: true)
                                        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading).padding(.vertical, 8)
                                        .contentShape(Rectangle())
                                }.buttonStyle(.plain).disabled(busy)
                                    .accessibilityAddTraits(quoteStart == match.start ? .isSelected : [])
                            }
                            if matches.hasMore {
                                Text("相同片段较多，仅显示前 20 处。请补充前后文以缩小范围。")
                                    .font(.footnote).foregroundStyle(.secondary)
                            }
                        }
                    }
                    Text("保存后进入待确认，确认后才成为长期记忆。")
                        .font(.footnote).foregroundStyle(.secondary)
                    if let error { Text(L(error)).foregroundStyle(.secondary) }
                }
            }.formStyle(.grouped)
                #if os(iOS)
                .navigationTitle(L("建议"))
                .navigationBarTitleDisplayMode(.inline)
                #else
                .toolbar { ToolbarItem(placement: .principal) { Text("记忆建议").font(.headline) } }
                #endif
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button(mode == "model" ? "完成" : "取消") { if dirty { discard = true } else { dismiss() } }.disabled(busy)
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        if mode == "manual" {
                            Button("保存建议") { submit() }.accessibilityLabel("加入待确认").disabled(!valid || busy)
                        }
                    }
                }
                .confirmationDialog("放弃未保存的修改？", isPresented: $discard, titleVisibility: .visible) {
                    Button("放弃修改", role: .destructive) { dismiss() }
                }
        }.onAppear { quote = SourceQuoteMatches.initialQuote(in: sourceText); updateMatches() }
            .onChange(of: Array(text.unicodeScalars)) { _, _ in requestID = UUID() }
            .onChange(of: Array(quote.unicodeScalars)) { _, _ in requestID = UUID(); updateMatches() }
            .interactiveDismissDisabled(dirty || busy)
        #if os(macOS)
        .frame(minWidth: 580, idealWidth: 720, minHeight: 540)
        #endif
    }
    private var modePicker: some View {
        Picker("整理方式", selection: $mode) {
            Text("手动填写").tag("manual")
            Text("模型整理").tag("model")
        }
    }
    private func updateMatches() {
        matches = SourceQuoteMatches(text: sourceText, quote: quote)
        quoteStart = matches.items.count == 1 ? matches.items.first?.start : nil
    }
    private func submit() {
        guard let client = workspace.client else { error = "请先连接资料库。"; return }
        guard valid, let quoteStart else { return }
        busy = true; error = nil
        struct Reference: Encodable { let kind: String; let id: String; let contentVersion: String; let quote: String; let quoteStart: Int }
        struct Body: Encodable { let text: String; let evidence: [Reference]; let requestID: UUID }
        Task {
            defer { busy = false }
            do {
                let body = Body(text: text, evidence: [.init(kind: source.kind, id: source.id, contentVersion: source.contentVersion, quote: quote, quoteStart: quoteStart)], requestID: requestID)
                try await client.sendData("v1/candidates", method: "POST", body: JSONEncoder().encode(body))
                workspace.memoryNotice = "已加入待确认记忆。"
                await workspace.libraryChanged(); saved(); dismiss()
            } catch { self.error = error.localizedDescription }
        }
    }
}
