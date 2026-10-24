import SwiftUI

struct ConversationView: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @StateObject private var chat = ConversationStore()
    @State private var draft = ""
    @State private var scope = ""
    @State private var source: KnowledgeSource?
    @FocusState private var composing: Bool
    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var typeSize
    var body: some View {
        VStack(spacing: 0) {
            headerLayout {
                VStack(alignment: .leading, spacing: 4) {
                    Text(chat.conversations.first { $0.id == chat.current }?.title.nonEmpty ?? L("新对话"))
                        .font(.headline).lineLimit(typeSize.isAccessibilitySize ? 2 : 1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if !typeSize.isAccessibilitySize {
                        Text("从自己的资料开始。").font(.caption).foregroundStyle(.secondary)
                    }
                }
                HStack(spacing: 16) {
                Menu {
                    if chat.conversations.isEmpty { Text("还没有历史对话") }
                    ForEach(chat.conversations) { item in
                        Button(item.title.isEmpty ? L("新对话") : item.title) { Task { await chat.select(item.id, client: workspace.client) } }
                    }
                    if chat.moreHistory {
                        Button("更早的历史对话") { Task { await chat.earlierConversations(client: workspace.client) } }
                    }
                } label: { Image(systemName: "clock.arrow.circlepath").frame(minWidth: 44, minHeight: 44) }
                    .help("历史对话").accessibilityLabel("历史对话")
                Button { chat.newConversation(); draft = ""; composing = true } label: { Image(systemName: "square.and.pencil").frame(minWidth: 44, minHeight: 44) }
                    .help("新对话").accessibilityLabel("新对话")
                }
            }.buttonStyle(.plain).padding(.horizontal, 26).padding(.vertical, 20).disabled(chat.sending)
            Divider().opacity(0.5)
            ScrollViewReader { proxy in
                GeometryReader { viewport in
                ScrollView {
                    VStack(alignment: .leading, spacing: 32) {
                        if chat.nextBefore != nil {
                            Button("更早的对话") { Task { await chat.earlier(client: workspace.client) } }.disabled(chat.loading)
                        }
                        if chat.turns.isEmpty && !chat.loading { welcome.frame(minHeight: max(0, viewport.size.height - 56)) }
                        ForEach(chat.turns) { turn in turnView(turn).id(turn.id) }
                        if chat.loading { ProgressView().frame(maxWidth: .infinity) }
                    }.padding(28).frame(maxWidth: 900, alignment: .leading).frame(maxWidth: .infinity)
                }.scrollDismissesKeyboard(.interactively).id(chat.current).onChange(of: chat.turns.last?.id) { _, _ in if let id = chat.turns.last?.id { proxy.scrollTo(id, anchor: .top) } }
                }
            }
            composer
        }.background(AppPalette.canvas)
            .task(id: workspace.endpoint + String(workspace.connected)) { if workspace.connected { await chat.load(client: workspace.client) } }
            .sheet(item: $source) { source in
                if let excerpt = source.excerpt, !excerpt.isEmpty, let start = source.excerptStart {
                    SourcePassageReader(workspace: workspace, source: source, quote: excerpt,
                        quoteStart: start, origin: .search).appLanguage()
                } else {
                    KnowledgeSourceReader(workspace: workspace, source: source).appLanguage()
                }
            }
    }
    private var headerLayout: AnyLayout {
        typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 12))
            : AnyLayout(HStackLayout(spacing: 16))
    }
    private var welcome: some View {
        VStack(alignment: .leading, spacing: 24) {
            BrandMark(size: 52)
            Text("把零散的日常，\n连接起来。").font(.system(size: 32, weight: .medium)).tracking(-0.8).lineSpacing(5)
            Text("查找笔记、回看每天的记录，或找回已经确认的记忆。")
                .font(.body).foregroundStyle(.secondary).lineSpacing(5)
            VStack(alignment: .leading, spacing: 10) {
                suggestion("今天记录了什么？", symbol: "calendar")
                suggestion("查找项目相关的资料", symbol: "folder")
                suggestion("有哪些已经确认的长期记忆？", symbol: "bookmark", scope: "confirmedMemory")
            }.padding(.top, 8)
        }.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 24)
    }
    private func suggestion(_ text: String, symbol: String, scope: String = "") -> some View {
        Button { draft = L(text); self.scope = scope; composing = true } label: {
            HStack { Image(systemName: symbol).frame(width: 22); Text(LocalizedStringKey(text)); Spacer(); Image(systemName: "arrow.up.left").font(.caption) }
                .font(.subheadline).foregroundStyle(.primary).padding(15)
                .background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 12))
        }.buttonStyle(QuietPressStyle())
    }
    private func turnView(_ turn: ConversationTurn) -> some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack {
                Spacer(minLength: 40)
                Text(verbatim: turn.question).font(.body).lineSpacing(4).textSelection(.enabled)
                    .padding(16).background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 16))
            }
            HStack(spacing: 9) { BrandMark(size: 22); Text("Lifto").font(.subheadline.weight(.semibold)) }
            if let conversationID = chat.current, workspace.status?.answerConfigured != nil || turn.response.answer != nil {
                ConversationAnswerSection(workspace: workspace, conversationID: conversationID, turn: turn)
            }
            if turn.response.sources.isEmpty {
                Text("没有找到匹配资料。试试更具体的关键词，或调整查询范围。")
                    .font(.body).foregroundStyle(.secondary).lineSpacing(5)
            } else {
                Text(LF("找到 %d 条相关来源", turn.response.sources.count)).font(.headline)
                ForEach(turn.response.sources, id: \.identity) { item in
                    Button { source = item } label: {
                        VStack(alignment: .leading, spacing: 12) {
                            HStack(alignment: .firstTextBaseline) {
                                Label(item.title, systemImage: item.symbol).font(.subheadline.weight(.semibold)).lineLimit(2)
                                Spacer(); Image(systemName: "arrow.up.right").font(.caption)
                            }
                            if !item.textAvailable || readableExcerpt(item.excerpt ?? "").trimmingCharacters(in: .whitespacesAndNewlines) != item.title {
                                Text(verbatim: item.textAvailable ? readableExcerpt(item.excerpt ?? "") : L("这条记录还没有文字。"))
                                    .font(.body).lineSpacing(5).lineLimit(7).foregroundStyle(.secondary)
                            }
                            if let notice = item.analysisNotice {
                                Label(L(notice), systemImage: "info.circle").font(.caption).foregroundStyle(.secondary)
                            }
                            HStack {
                                Text(L(item.kindTitle)); Text(verbatim: item.date); Spacer(); Text("查看来源")
                            }.font(.caption).foregroundStyle(.secondary)
                        }.padding(18).frame(maxWidth: .infinity, alignment: .leading)
                            .background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 14)).foregroundStyle(.primary)
                    }.buttonStyle(QuietPressStyle())
                }
                if turn.response.coverage.hasMore { Text("还有更多匹配资料，可以缩小查询范围。").font(.caption).foregroundStyle(.secondary) }
            }
        }
    }
    private func readableExcerpt(_ value: String) -> String {
        value.replacingOccurrences(of: #"\[[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}\]\([^)]+\)"#, with: L("查看来源"), options: .regularExpression)
            .replacingOccurrences(of: #"(?m)^#{1,6}\s+"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: #"\[([^\]]+)\]\([^)]+\)"#, with: "$1", options: .regularExpression)
    }
    private var composer: some View {
        VStack(alignment: .leading, spacing: 10) {
            if draft.count > 500 { Text("问题最多 500 个字符。").font(.caption).foregroundStyle(.secondary) }
            if let error = chat.error { Text(L(error)).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
            if !workspace.connected {
                HStack {
                    if !typeSize.isAccessibilitySize { Text("连接资料库后即可查询。"); Spacer() }
                    Button("重新连接") { Task { await workspace.refresh() } }
                        .frame(minHeight: 44).accessibilityHint("连接资料库后即可查询。")
                }
                    .font(.caption).foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 12) {
                TextField("问问自己的资料…", text: $draft, axis: .vertical)
                    .font(.body).lineLimit(1...5).textFieldStyle(.plain).focused($composing)
                    .accessibilityHint("先在本机检索；选择片段后可请求模型回答。")
                    .onSubmit { submit() }.disabled(chat.sending)
                HStack {
                    Picker("查询范围", selection: $scope) {
                        Text("全部资料").tag("")
                        Text("笔记").tag("notes")
                        Text("原音文字").tag("transcripts")
                        Text("本地输入").tag("localInputs")
                        Text("导入资料").tag("documents")
                        Text("每日记录").tag("daily")
                        Text("AI 每日分析").tag("analyses")
                        Text("已确认的记忆").tag("confirmedMemory")
                    }.labelsHidden().fixedSize().controlSize(.small).disabled(chat.sending)
                    Spacer()
                    if chat.sending { ProgressView().controlSize(.small) }
                    Button(action: submit) { Image(systemName: "arrow.up").font(.body.weight(.semibold)).frame(width: 32, height: 32) }
                        .buttonStyle(.plain).foregroundStyle(AppPalette.actionText(scheme)).background(AppPalette.actionFill(scheme), in: Circle())
                        .accessibilityLabel("发送问题").disabled(!canSend)
                        .opacity(canSend ? 1 : 0.35)
                }
            }.padding(16).background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 19))
                .overlay(RoundedRectangle(cornerRadius: 19).stroke(AppPalette.line, lineWidth: 1))
            if !typeSize.isAccessibilitySize {
                Text("先在本机检索；选择片段后可请求模型回答。")
                    .font(.caption2).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }.padding(.horizontal, 26).padding(.bottom, 20).padding(.top, 12)
            .frame(maxWidth: 950).frame(maxWidth: .infinity)
    }
    private var canSend: Bool { workspace.connected && !chat.sending && !chat.loading && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && draft.count <= 500 }
    private func submit() {
        guard canSend else { return }
        let value = draft
        #if os(iOS)
        composing = false
        #endif
        Task { if await chat.send(value, scope: scope, client: workspace.client), draft == value { draft = "" } }
    }
}

struct KnowledgeSourceReader: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let source: KnowledgeSource
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var version: String?
    @State private var nextOffset: Int?
    @State private var loaded = false
    @State private var loading = false
    @State private var error: String?
    @State private var sourceChanged = false
    @State private var currentSource: KnowledgeSource?
    @State private var proposing = false
    @State private var proposalSaved = false
    @State private var memory: NodeCandidate?
    private var documentParts: (header: String?, body: String) {
        guard source.kind == "document", text.hasPrefix("# "),
              let divider = text.range(of: "\n\n---\n\n") else { return (nil, text) }
        let header = String(text[..<divider.lowerBound])
        guard header.contains("\n来源：导入资料\n"), header.contains("\n作者：未确认。") else { return (nil, text) }
        return (header, String(text[divider.upperBound...]))
    }
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    Text(verbatim: source.title).font(.title2.weight(.semibold))
                    Label(L(source.kindTitle) + " · " + source.date, systemImage: source.symbol).font(.caption).foregroundStyle(.secondary)
                    if sourceChanged {
                        Label("来源已更新，以下为最新内容。", systemImage: "arrow.clockwise").font(.caption).foregroundStyle(.secondary)
                    }
                    if let notice = (currentSource ?? source).analysisNotice {
                        Label(L(notice), systemImage: "info.circle").font(.callout).foregroundStyle(.secondary)
                    }
                    if loaded && text.isEmpty { Text("这条记录还没有文字。").foregroundStyle(.secondary) }
                    if let metadata = documentParts.header {
                        Text("作者尚未确认，请核对资料中陈述的归属。").font(.caption).foregroundStyle(.secondary)
                        MarkdownReader(text: documentParts.body)
                        DisclosureGroup("资料信息") { Text(verbatim: metadata).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
                    } else {
                        MarkdownReader(text: text)
                    }
                    if let error { Text(L(error)).font(.caption).foregroundStyle(.secondary); Button("重新读取") { Task { await read(reset: true) } } }
                    if loading { ProgressView() }
                    if nextOffset != nil, error == nil { Button("继续读取") { Task { await read(reset: false) } }.disabled(loading) }
                    if ["note", "recording", "input", "document"].contains(source.kind), loaded, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        Divider()
                        Button("提出记忆建议", systemImage: "bookmark.badge.plus") { proposing = true }.disabled(loading || error != nil)
                        if proposalSaved { Text("已加入待确认记忆。").font(.caption).foregroundStyle(.secondary) }
                    }
                    if source.kind == "memory", loaded {
                        Button("查看记忆依据", systemImage: "link") {
                            Task {
                                do {
                                    guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
                                    memory = try await client.read("v1/candidates/" + source.id, as: NodeCandidate.self)
                                } catch { self.error = error.localizedDescription }
                            }
                        }
                    }
                }.padding(24).frame(maxWidth: 850, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
            }.background(AppPalette.canvas).navigationTitle(L("查看来源"))
                #if os(iOS)
                .navigationBarTitleDisplayMode(.inline)
                #endif
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
        }.task { await read(reset: true) }.sourceLinks(workspace: workspace)
            .sheet(isPresented: $proposing) {
                MemoryProposalSheet(workspace: workspace, source: currentSource ?? source, sourceText: text) { proposalSaved = true }.appLanguage()
            }
            .sheet(item: $memory) { value in MemoryDetail(store: workspace, candidate: value) }
        #if os(macOS)
        .frame(minWidth: 680, idealWidth: 860, minHeight: 600, idealHeight: 760)
        #endif
    }
    private func read(reset: Bool) async {
        guard let client = workspace.client else { error = "请先连接资料库。"; return }
        loading = true; error = nil; defer { loading = false }
        do {
            var query = ["offset": String(reset ? 0 : (nextOffset ?? 0)), "limit": "16000"]
            if !reset, let version { query["version"] = version }
            let value = try await client.read("v1/sources/" + source.identity, query: query, as: KnowledgeSource.self)
            currentSource = value
            sourceChanged = value.contentVersion != source.contentVersion
            text = reset ? (value.text ?? "") : text + (value.text ?? "")
            version = value.contentVersion; nextOffset = value.nextOffset; loaded = true
        } catch { self.error = error.localizedDescription }
    }
}

private struct SourceLinkNavigation: ViewModifier {
    let workspace: NodeWorkspaceStore
    @State private var selected: KnowledgeSource?
    @State private var problem: String?
    func body(content: Content) -> some View {
        content
            .environment(\.openURL, OpenURLAction { url in
                guard let reference = LibraryReference.identity(url) else {
                    return url.scheme == "https" || url.scheme == "http" ? .systemAction : .discarded
                }
                Task {
                    do {
                        guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
                        selected = try await client.read("v1/sources/\(reference.kind)/\(reference.id.uuidString)", as: KnowledgeSource.self)
                    } catch { problem = error.localizedDescription }
                }
                return .handled
            })
            .sheet(item: $selected) { value in KnowledgeSourceReader(workspace: workspace, source: value).appLanguage() }
            .alert("无法打开来源", isPresented: Binding(get: { problem != nil }, set: { if !$0 { problem = nil } })) {
                Button("完成") { problem = nil }
            } message: { Text(L(problem ?? "")) }
    }
}

extension View {
    func sourceLinks(workspace: NodeWorkspaceStore) -> some View { modifier(SourceLinkNavigation(workspace: workspace)) }
}

private extension String { var nonEmpty: String? { isEmpty ? nil : self } }
