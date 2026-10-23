import SwiftUI
import CryptoKit

struct WorkspaceHomeView: View {
    @ObservedObject var store: NodeWorkspaceStore
    @State private var section = 0
    @State private var settings = false
    var body: some View {
        Group {
            if store.configured {
                VStack(spacing: 0) {
                    Picker("资料分类", selection: $section) {
                        Text("记录").tag(0); Text("每日草稿").tag(1); Text("记忆").tag(2)
                    }.pickerStyle(.segmented).padding()
                    if section == 0 { NodeRecordsView(store: store) }
                    else if section == 1 { NodeDailyView(store: store) }
                    else { NodeMemoryView(store: store) }
                }
            } else {
                ContentUnavailableView {
                    Label("让记录汇到一起", systemImage: "externaldrive.connected.to.line.below")
                } description: {
                    Text("连接电脑或自部署节点，集中查看原音、文字和记忆。")
                } actions: {
                    Button("连接处理节点") { settings = true }.buttonStyle(FilledButtonStyle())
                }
            }
        }.navigationTitle("电脑资料库")
            .toolbar { ToolbarItem { Button { settings = true } label: { Image(systemName: "network") }.accessibilityLabel("电脑连接设置") } }
            .sheet(isPresented: $settings) {
                NavigationStack {
                    NodeConnectionView(store: store)
                        .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { settings = false } } }
                }
            }
            .task { await store.refresh() }
    }
}

struct WorkspaceStatusRow: View {
    @ObservedObject var store: NodeWorkspaceStore
    var body: some View {
        Section {
            HStack {
                Circle().fill(store.connected ? Color.primary : Color.secondary).frame(width: 6, height: 6)
                Text(L(store.connected ? "资料库已连接" : "等待连接")).font(.subheadline)
                Spacer()
                Button { Task { await store.refresh() } } label: {
                    if store.loading { ProgressView().controlSize(.small) }
                    else { Image(systemName: "arrow.clockwise") }
                }.disabled(store.loading).accessibilityLabel("刷新资料")
            }
            if let error = store.problem { Text(error).font(.footnote).foregroundStyle(.secondary) }
            if let conflicts = store.status?.conflicts, !conflicts.isEmpty {
                Label("\(conflicts.count) 个归档文件有人工修改，新版本已另存。", systemImage: "doc.badge.clock")
                    .font(.footnote).foregroundStyle(.orange)
            }
        }
    }
}

struct NodeRecordView: View {
    @ObservedObject var store: NodeWorkspaceStore
    let record: NodeRecord
    @State private var latest: NodeRecord?
    @State private var categories: [LibraryCategory] = []
    @State private var organizing = false
    private var currentRecord: NodeRecord { latest ?? record }
    @State private var suggestionSource: KnowledgeSource?
    @State private var proposalSaved = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack {
                    Text(currentRecord.title).font(.title2.weight(.semibold)).frame(maxWidth: .infinity, alignment: .leading)
                    Menu { Button("整理分类", systemImage: "folder") { organizing = true } } label: {
                        Image(systemName: "ellipsis").frame(minWidth: 44, minHeight: 44)
                    }.accessibilityLabel("录音操作")
                }
                Text(currentRecord.date, format: .dateTime.year().month().day().hour().minute()).foregroundStyle(.secondary)
                OrganizationBadges(organization: currentRecord.organization ?? .init(), categories: categories)
                NodeAudioControls(store: store, record: currentRecord, playback: store.playback)
                TranscriptionView(store: store, recordingID: record.id)
                NavigationLink {
                    NodeDocumentEditor(store: store, path: "v1/recordings/\(record.id.uuidString)/transcript", title: "文字记录",
                                       hint: "结合原音校对文字，人工修改会保留。")
                } label: {
                    Label(L(currentRecord.textState == "ready" ? "查看与编辑文字" : "添加文字记录"), systemImage: "text.alignleft")
                        .frame(maxWidth: .infinity, minHeight: 40)
                }.buttonStyle(FilledButtonStyle())
                Button("提出记忆建议", systemImage: "brain") {
                    Task {
                        do {
                            guard let client = store.client else { throw NodeFailure(message: "请先连接资料库。") }
                            suggestionSource = try await client.read("v1/sources/recording/" + record.id.uuidString, as: KnowledgeSource.self)
                        } catch { store.problem = error.localizedDescription }
                    }
                }.buttonStyle(.bordered).disabled(currentRecord.textState != "ready")
                Text(L(currentRecord.textState == "ready" ? "建议保留原文依据，确认后才成为记忆。" : "添加文字后，可以从原文提出记忆建议。"))
                    .font(.footnote).foregroundStyle(.secondary)
                if proposalSaved { Text("已加入待确认记忆。").font(.caption).foregroundStyle(.secondary) }
            }.padding(20).frame(maxWidth: 760, alignment: .leading).frame(maxWidth: .infinity)
        }.background(AppPalette.canvas).navigationTitle(L("录音资料")).appLanguage()
            .sheet(isPresented: $organizing) {
                OrganizationEditor(store: store, kind: "recording", id: record.id, initial: currentRecord.organization ?? .init())
            }
            .task(id: store.libraryRevision) {
                guard let client = store.client else { return }
                do {
                    async let value = client.read("v1/library/recording/\(record.id.uuidString)", as: NodeRecord.self)
                    async let facets = client.read("v1/library/recording/facets", as: LibraryFacets.self)
                    let result = try await (value, facets); latest = result.0; categories = result.1.categories
                } catch { store.problem = error.localizedDescription }
            }
            .sheet(item: $suggestionSource) { source in
                MemoryProposalSheet(workspace: store, source: source, sourceText: source.text ?? "") { proposalSaved = true }.appLanguage()
            }
            .onDisappear { store.playback.stop() }
    }
}

private struct NodeAudioControls: View {
    @ObservedObject var store: NodeWorkspaceStore
    let record: NodeRecord
    @ObservedObject var playback: AudioPlayback
    private var current: Bool { playback.recordingID == record.id }
    var body: some View {
        VStack(spacing: 18) {
            HStack {
                Text(Duration.seconds(current ? playback.position : record.duration).formatted(.time(pattern: .minuteSecond)))
                    .font(.system(size: 42, weight: .light, design: .rounded)).monospacedDigit()
                Spacer()
                Button { Task { await store.play(record) } } label: {
                    if store.downloadingID == record.id { ProgressView() }
                    else { Image(systemName: current && playback.isPlaying ? "pause.fill" : "play.fill").font(.title2) }
                }.buttonStyle(.bordered).disabled(store.downloadingID != nil)
                    .accessibilityLabel(current && playback.isPlaying ? "暂停回放" : "播放原音")
            }
            Slider(value: Binding(get: { current ? playback.position : 0 }, set: { playback.seek(to: $0) }),
                   in: 0...max(0.1, record.duration)).disabled(!current).accessibilityLabel("回放位置")
            if let error = playback.problem { Text(error).font(.footnote).foregroundStyle(.secondary) }
        }.padding(22).background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 22))
    }
}

struct NodeConnectionView: View {
    @ObservedObject var store: NodeWorkspaceStore
    var allowsConnectionEditing = true
    var showsLibrarySettings = true
    @State private var endpoint = ""
    @State private var token = ""
    @State private var error: String?
    @State private var busy = false
    var body: some View {
        Form {
            if allowsConnectionEditing {
              Section("连接电脑或服务器") {
                TextField("节点地址，例如 http://电脑名.local:8765", text: $endpoint)
                SecureField(store.configured ? "连接口令，留空保留现有口令" : "连接口令", text: $token)
                Button(L(busy ? "正在连接" : "保存并连接")) {
                    busy = true
                    Task {
                        do { try store.configure(endpoint: endpoint, token: token); token = ""; await store.refresh(); error = store.problem }
                        catch { self.error = error.localizedDescription }
                        busy = false
                    }
                }.disabled(busy || endpoint.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                if let error { Text(error).foregroundStyle(.red).font(.footnote) }
                Text("地址和连接口令由声记电脑端或自部署节点提供，与模型接口密钥无关。")
                    .font(.footnote).foregroundStyle(.secondary)
              }
            }
            if store.configured {
                Section("当前节点") {
                    LabeledContent("状态", value: L(store.connected ? "已连接" : "等待连接"))
                    if let status = store.status {
                        LabeledContent("时区", value: status.timeZone)
                        Text(status.outputDirectory).font(.caption).textSelection(.enabled)
                    }
                    if allowsConnectionEditing {
                        Button("断开节点", role: .destructive) { store.disconnect() }
                    }
                }
                if showsLibrarySettings {
                  Section("整理规则") {
                    NavigationLink("每日摘要提示词", value: LibraryRoute.document(path: "v1/prompts/daily-summary", title: "每日摘要规则", hint: "规则可先编辑，模型接入后使用。"))
                    NavigationLink("记忆提取提示词", value: LibraryRoute.document(path: "v1/prompts/memory-extraction", title: "记忆提取规则", hint: "规则可先编辑，模型接入后使用。"))
                  }
                }
                if let conflicts = store.status?.conflicts, !conflicts.isEmpty {
                    Section("归档需要处理") {
                        ForEach(conflicts) { conflict in
                            VStack(alignment: .leading, spacing: 7) {
                                Text(conflict.path).font(.headline)
                                Text("人工修改已保留，新生成内容在：").font(.footnote)
                                Text(conflict.generatedPath ?? "").font(.caption).textSelection(.enabled)
                            }
                        }
                    }
                }
            }
        }.formStyle(.grouped).navigationTitle(L("连接与归档"))
            .libraryDestinations(store: store)
            .onAppear { endpoint = store.endpoint; error = store.problem }
    }
}

private struct EditorDraft: Codable {
    let text: String
    let etag: String
    var savedText: String?
}

struct NodeDocumentEditor: View {
    @ObservedObject var store: NodeWorkspaceStore
    let path: String
    let title: String
    let hint: String
    @State private var text = ""
    @State private var savedText = ""
    @State private var etag = ""
    @State private var loaded = false
    @State private var saving = false
    @State private var error: String?
    @State private var discard = false
    private var dirty: Bool { text != savedText }
    private var draftURL: URL {
        let key = SHA256.hash(data: Data((store.endpoint + "/" + path).utf8)).map { String(format: "%02x", $0) }.joined()
        return FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("WorkspaceDrafts").appendingPathComponent(key + ".json")
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(LocalizedStringKey(hint)).font(.subheadline).foregroundStyle(.secondary)
            if let error { Text(L(error)).font(.footnote).foregroundStyle(.orange) }
            if loaded {
                TextEditor(text: $text).font(.body).frame(minHeight: 260)
                    .accessibilityLabel(L(title))
                    .disabled(saving)
                    .padding(10).background(.quaternary.opacity(0.2), in: RoundedRectangle(cornerRadius: 14))
                HStack {
                    Text(L(dirty ? "未提交的修改会保留为本机草稿" : "已保存"))
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button(L(saving ? "保存中" : "保存")) {
                        saving = true
                        Task {
                            do {
                                let doc = try await store.saveDocument(path, text: text, etag: etag)
                                etag = doc.etag; savedText = doc.text; error = nil
                                try? FileManager.default.removeItem(at: draftURL)
                            } catch { self.error = error.localizedDescription; persistDraft() }
                            saving = false
                        }
                    }.buttonStyle(FilledButtonStyle()).disabled(!dirty || saving)
                }
                Button("放弃本机草稿并重新读取") { discard = true }.font(.caption)
            } else if error != nil {
                Button("重新读取") { Task { await load() } }
            } else { ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity) }
        }.padding(24).navigationTitle(L(title))
            .task(id: path) { await load() }
            .onChange(of: text) { _, _ in if loaded { persistDraft() } }
            .onDisappear { persistDraft() }
            .confirmationDialog("放弃尚未提交的本机修改？", isPresented: $discard) {
                Button("重新读取", role: .destructive) {
                    try? FileManager.default.removeItem(at: draftURL)
                    loaded = false
                    Task { await load() }
                }
            }
    }
    private func load() async {
        do {
            let doc = try await store.document(path)
            savedText = doc.text; etag = doc.etag; text = doc.text
            if let data = try? Data(contentsOf: draftURL), let draft = try? JSONDecoder().decode(EditorDraft.self, from: data) {
                text = draft.text; etag = draft.etag
                error = draft.etag == doc.etag ? "已恢复本机未提交草稿。" : "文件已更新，你的草稿仍保留。请先核对版本。"
            } else { error = nil }
            loaded = true
        } catch {
            self.error = error.localizedDescription
            if let data = try? Data(contentsOf: draftURL),
               let draft = try? JSONDecoder().decode(EditorDraft.self, from: data) {
                text = draft.text; etag = draft.etag; savedText = draft.savedText ?? ""
                loaded = true
                self.error = "节点暂不可用，已恢复本机草稿。恢复连接后可以保存。"
            }
        }
    }
    private func persistDraft() {
        guard loaded else { return }
        guard dirty else { try? FileManager.default.removeItem(at: draftURL); return }
        try? FileManager.default.createDirectory(at: draftURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? JSONEncoder().encode(EditorDraft(text: text, etag: etag, savedText: savedText)).write(to: draftURL, options: .atomic)
    }
}
