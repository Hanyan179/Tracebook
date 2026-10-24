import SwiftUI

struct BatchReference: Codable, Sendable {
    let kind: String
    let id: String
    var segment: MemorySourceSegment? = nil
    var contentVersion: String? = nil
    var identity: String { kind + "/" + id + (segment.map { "/\($0.start)-\($0.end)" } ?? "") }
}
private struct BatchSource: Decodable, Sendable {
    let id: String; let kind: String; let title: String; let date: String; let textAvailable: Bool
    var contentRole: String?
    var identity: String { kind + "/" + id }
    var reference: BatchReference { .init(kind: kind, id: id) }
}
struct BatchItem: Decodable, Sendable {
    let id: String; let kind: String; let title: String; let date: String
    let contentVersion: String; let characters: Int; let reusable: Bool
    let problem: String?
    var status: String?
    var candidateIDs: [String]?
    var segment: MemorySourceSegment?
    var reference: BatchReference { .init(kind: kind, id: id, segment: segment, contentVersion: segment == nil ? nil : contentVersion) }
    var identity: String { reference.identity }
}
struct BatchPlan: Decodable, Sendable {
    let items: [BatchItem]; let model: String; let endpoint: String; let snapshot: String
    let configured: Bool; let newRequests: Int; let characters: Int; let sourceLimit: Int
    var segmented: Bool { items.contains { $0.segment != nil } }
    var canReuseAll: Bool { !items.isEmpty && items.allSatisfy { $0.problem == nil && $0.reusable } }
}
private struct BatchState: Decodable, Identifiable, Sendable {
    let id: String; let status: String; let problem: String?; let createdAt: String
    let items: [BatchItem]; let model: String; let endpoint: String; let configured: Bool
    let finishedCount: Int; let candidateCount: Int
    let remainingRequests: Int
    var segmented: Bool { items.contains { $0.segment != nil } }
    var running: Bool { status == "running" || status == "pausing" }
}
private struct BatchList: Decodable, Sendable { let items: [BatchState] }
private struct BatchSourcePage: Decodable, Sendable { let items: [BatchSource]; let total: Int; let nextCursor: String? }
private func batchStatusTitle(_ status: String) -> String {
    switch status {
    case "ready": "已完成"; case "running": "正在整理"; case "pausing": "完成当前资料后暂停"
    case "paused": "已暂停"; case "partial": "部分资料需要重试"; case "failed": "未完成"
    default: "等待处理"
    }
}
private extension View {
    @ViewBuilder func batchTitle(_ title: String) -> some View {
        #if os(iOS)
        self.navigationTitle(L(title)).navigationBarTitleDisplayMode(.inline)
        #else
        self.toolbar { ToolbarItem(placement: .principal) { Text(L(title)).font(.headline) } }
        #endif
    }
}

struct MemoryBatchView: View {
    @ObservedObject var store: NodeWorkspaceStore
    @Environment(\.dismiss) private var dismiss
    @State private var batches: [BatchState] = []
    @State private var selected: BatchState?
    @State private var problem: String?
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    NavigationLink("选择资料并整理") { MemoryBatchSelectionView(store: store) }
                    Text("每次最多选择 20 份资料。先核对发送范围，再逐份生成待确认建议。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("最近的批量整理") {
                    if batches.isEmpty { Text("还没有批量整理任务。").foregroundStyle(.secondary) }
                    ForEach(batches) { batch in
                        Button { selected = batch } label: {
                            VStack(alignment: .leading, spacing: 8) {
                                Text(L(batchStatusTitle(batch.status))).font(.headline)
                                Text(LF(batch.segmented ? "已处理 %d / %d 段内容" : "已处理 %d / %d 份资料", batch.finishedCount, batch.items.count)).font(.callout)
                                if let date = ISO8601DateFormatter().date(from: batch.createdAt) {
                                    Text(date, format: .dateTime.month().day().hour().minute()).font(.caption).foregroundStyle(.secondary)
                                }
                            }.frame(maxWidth: .infinity, minHeight: 44, alignment: .leading).padding(.vertical, 6)
                        }.buttonStyle(.plain)
                    }
                    Text("这里显示最近 20 次任务。").font(.footnote).foregroundStyle(.secondary)
                }
                if let problem { Section { Text(L(problem)); Button("重新读取") { Task { await load() } } } }
            }.formStyle(.grouped).batchTitle("批量整理")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } } }
                .task { await load() }
                .sheet(item: $selected, onDismiss: { Task { await load() } }) { batch in
                    NavigationStack {
                        MemoryBatchProgressView(store: store, id: batch.id)
                            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { selected = nil } } }
                    }.appLanguage()
                        #if os(macOS)
                        .frame(minWidth: 560, idealWidth: 720, minHeight: 580)
                        #endif
                }
        }
        #if os(macOS)
        .frame(minWidth: 580, idealWidth: 720, minHeight: 600)
        #endif
    }
    @MainActor private func load() async {
        guard let client = store.client else { problem = "请先连接资料库。"; return }
        do { batches = try await client.read("v1/memory-batches", as: BatchList.self).items; problem = nil }
        catch { problem = error.localizedDescription }
    }
}

private struct MemoryBatchSelectionView: View {
    @ObservedObject var store: NodeWorkspaceStore
    @State private var kind = "note"
    @State private var query = ""
    @State private var items: [BatchSource] = []
    @State private var selected: [String: BatchSource] = [:]
    @State private var total = 0
    @State private var cursor: String?
    @State private var loading = false
    @State private var previewing = false
    @State private var problem: String?
    @State private var plan: BatchPlan?
    @State private var showingPreview = false
    @State private var generation = UUID()
    @State private var selectionExpanded = false
    @State private var showingSegments = false
    @State private var segmentSources: [BatchReference] = []
    private let initialReferences: [BatchReference]
    init(store: NodeWorkspaceStore, initialItems: [BatchItem] = []) {
        self.store = store
        initialReferences = initialItems.map(\.reference)
        _kind = State(initialValue: initialItems.first?.kind ?? "note")
        let sources = initialItems.map {
            BatchSource(id: $0.id, kind: $0.kind, title: $0.title, date: $0.date, textAvailable: $0.characters > 0)
        }
        _selected = State(initialValue: Dictionary(sources.map { ($0.identity, $0) }, uniquingKeysWith: { first, _ in first }))
        _selectionExpanded = State(initialValue: !initialItems.isEmpty)
    }
    var body: some View {
        Form {
            Section {
                Picker("资料类型", selection: $kind) {
                    Text("笔记").tag("note"); Text("原音文字").tag("recording")
                    Text("本地输入").tag("input"); Text("导入资料").tag("document")
                }
                TextField("搜索资料", text: $query)
                Text(LF("已选择 %d / 20 份资料", selected.count)).font(.callout)
                if !selected.isEmpty {
                    DisclosureGroup("核对已选资料", isExpanded: $selectionExpanded) {
                        ForEach(selected.values.sorted { $0.identity < $1.identity }, id: \.identity) { source in
                            Button { selected.removeValue(forKey: source.identity) } label: {
                                Label { Text(verbatim: source.title).fixedSize(horizontal: false, vertical: true) }
                                    icon: { Image(systemName: "minus.circle") }
                                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                            }.buttonStyle(.plain).accessibilityLabel(LF("移除所选资料：%@", source.title))
                        }
                        Text("点按条目可移出本次选择，原资料不会删除。")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    Button("清除选择") { selected.removeAll() }
                }
            }
            Section {
                ForEach(items, id: \.identity) { source in
                    Button { toggle(source) } label: {
                        HStack(alignment: .top, spacing: 12) {
                            Image(systemName: selected[source.identity] == nil ? "circle" : "checkmark.circle.fill")
                            VStack(alignment: .leading, spacing: 6) {
                                Text(verbatim: source.title).fixedSize(horizontal: false, vertical: true)
                                Text(verbatim: source.date).font(.caption).foregroundStyle(.secondary)
                                if source.kind == "document" {
                                    Text(L((DocumentContentRole(rawValue: source.contentRole ?? "unknown") ?? .unknown).title))
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                if !source.textAvailable { Text("尚无文字").font(.caption).foregroundStyle(.secondary) }
                            }
                        }.frame(maxWidth: .infinity, minHeight: 44, alignment: .leading).padding(.vertical, 6)
                    }.buttonStyle(.plain)
                        .disabled(selected.count >= 20 && selected[source.identity] == nil)
                        .accessibilityLabel(LF("选择资料：%@", source.title))
                        .accessibilityValue(L(selected[source.identity] == nil ? "未选择" : "已选择"))
                }
                if loading { ProgressView("正在读取") }
                if items.isEmpty && !loading { Text("没有匹配的资料").foregroundStyle(.secondary) }
                Text(LF("已显示 %d / %d 份资料", items.count, total)).font(.caption).foregroundStyle(.secondary)
                if cursor != nil { Button("继续读取") { Task { await load(more: true) } }.disabled(loading) }
            }
            if let problem { Section { Text(L(problem)).foregroundStyle(.secondary) } }
        }.formStyle(.grouped).batchTitle("选择资料").disabled(previewing)
            .safeAreaInset(edge: .bottom) {
                Button(previewing ? "正在读取" : "核对发送范围") { preview() }
                    .buttonStyle(FilledButtonStyle()).disabled(selected.isEmpty || previewing)
                    .padding(16).frame(maxWidth: .infinity, alignment: .trailing).background(.bar)
            }
            .task(id: kind + "\u{1f}" + query) { await load(more: false) }
            .sheet(isPresented: $showingPreview) { if let plan { MemoryBatchPlanSheet(store: store, initial: plan).appLanguage() } }
            .sheet(isPresented: $showingSegments) {
                MemorySegmentSelectionView(store: store, sources: segmentSources, preferred: initialReferences).appLanguage()
            }
    }
    private func toggle(_ item: BatchSource) {
        if selected[item.identity] != nil { selected.removeValue(forKey: item.identity) }
        else if selected.count < 20 { selected[item.identity] = item }
    }
    @MainActor private func load(more: Bool) async {
        guard let client = store.client else { return }
        let request = more ? generation : UUID()
        if !more { generation = request; items = []; cursor = nil; total = 0 }
        loading = true; problem = nil
        defer { if request == generation { loading = false } }
        do {
            if !more { try await Task.sleep(for: .milliseconds(220)) }
            var parameters = ["kind": kind, "q": query]
            if more, let cursor { parameters["cursor"] = cursor }
            let page = try await client.read("v1/memory-batches/sources", query: parameters, as: BatchSourcePage.self)
            guard generation == request, !Task.isCancelled else { return }
            if more { let known = Set(items.map(\.identity)); items += page.items.filter { !known.contains($0.identity) } }
            else { items = page.items }
            cursor = page.nextCursor; total = page.total
        } catch is CancellationError {} catch { if request == generation { problem = error.localizedDescription } }
    }
    private func preview() {
        guard let client = store.client else { return }
        previewing = true; problem = nil
        struct Body: Encodable { let sources: [BatchReference] }
        let refs = selected.values.sorted { $0.identity < $1.identity }.map(\.reference)
        Task {
            defer { previewing = false }
            do {
                let data = try await client.sendData("v1/memory-batches/preview", method: "POST", body: JSONEncoder().encode(Body(sources: refs)))
                let preview = try JSONDecoder().decode(BatchPlan.self, from: data)
                if preview.items.contains(where: { $0.characters > 32000 }) {
                    segmentSources = refs; showingSegments = true
                } else { plan = preview; showingPreview = true }
            } catch { problem = error.localizedDescription }
        }
    }
}

struct MemoryBatchPlanSheet: View {
    @ObservedObject var store: NodeWorkspaceStore
    @Environment(\.dismiss) private var dismiss
    @State private var plan: BatchPlan
    @State private var requestID = UUID()
    @State private var taskID: String?
    @State private var busy = false
    @State private var problem: String?
    @State private var stale = false
    #if os(macOS)
    @Environment(\.openSettings) private var openSettings
    #endif
    init(store: NodeWorkspaceStore, initial: BatchPlan) { self.store = store; _plan = State(initialValue: initial) }
    var body: some View {
        NavigationStack {
            Group {
                if let taskID { MemoryBatchProgressView(store: store, id: taskID) }
                else {
                    Form {
                        Section("发送范围") {
                            Text(LF(plan.segmented ? "%d 段内容，含前后文共 %d 个字符" : "%d 份资料，共 %d 个字符", plan.items.count, plan.characters)).font(.headline)
                            Text(LF("最多新增 %d 次模型请求，已有结果会复用。", plan.newRequests)).font(.callout)
                            Text(L(plan.canReuseAll ? "已有结果可以复用，无需再次发送资料。" : "只发送下方选中的文字。模型可能收费；生成内容均为待确认建议。"))
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                        if plan.newRequests > 0 {
                        Section("模型服务") {
                            if plan.configured {
                                Text(verbatim: plan.model).font(.headline)
                                Text(verbatim: plan.endpoint).font(.callout).textSelection(.enabled)
                            } else {
                                Text("请先在电脑设置中配置文字模型。")
                                #if os(macOS)
                                Button("配置文字模型") {
                                    UserDefaults.standard.set("模型", forKey: "settings.section")
                                    UserDefaults.standard.set("summary", forKey: "model.focusRole"); openSettings()
                                }
                                #endif
                            }
                        }
                        }
                        Section("本次所选资料") {
                            ForEach(Array(plan.items.enumerated()), id: \.element.identity) { index, item in
                                VStack(alignment: .leading, spacing: 8) {
                                    if index == 0 || item.id != plan.items[index - 1].id || item.kind != plan.items[index - 1].kind {
                                        Text(verbatim: item.title).font(.headline)
                                    }
                                    if let segment = item.segment { Text(segment.rangeTitle).font(.callout) }
                                    Text(LF("%d 个字符", item.characters)).font(.caption).foregroundStyle(.secondary)
                                    if let issue = item.problem { Text(L(issue)).foregroundStyle(.secondary) }
                                    else if item.reusable { Text("复用已有结果").font(.caption).foregroundStyle(.secondary) }
                                }.padding(.vertical, 4)
                            }
                        }
                        Section {
                            if let problem { Text(L(problem)).foregroundStyle(.secondary) }
                            if plan.items.contains(where: { $0.problem != nil }) {
                                Text("请返回选择，移除未就绪的资料后再预览。")
                            }
                            Button(plan.canReuseAll ? "读取已有结果" : "发送所选资料并整理") { start() }
                                .buttonStyle(FilledButtonStyle())
                                .disabled(busy || stale || plan.items.contains { $0.problem != nil } || (!plan.configured && plan.newRequests > 0))
                            Button("重新预览范围") { refreshPlan() }.disabled(busy)
                            if busy { ProgressView("正在处理") }
                        }
                    }.formStyle(.grouped).batchTitle("发送范围")
                }
            }.toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button { dismiss() } label: {
                        #if os(iOS)
                        Text(L(taskID == nil ? "返回" : "完成"))
                        #else
                        Text(L(taskID == nil ? "返回选择" : "完成"))
                        #endif
                    }.disabled(busy).accessibilityLabel(L(taskID == nil ? "返回选择" : "完成"))
                }
            }
        }
        #if os(macOS)
        .frame(minWidth: 580, idealWidth: 720, minHeight: 600)
        #endif
    }
    private func refreshPlan() {
        guard let client = store.client else { return }
        busy = true
        struct Body: Encodable { let sources: [BatchReference] }
        Task {
            defer { busy = false }
            do {
                let data = try await client.sendData("v1/memory-batches/preview", method: "POST", body: JSONEncoder().encode(Body(sources: plan.items.map(\.reference))))
                plan = try JSONDecoder().decode(BatchPlan.self, from: data); requestID = UUID(); problem = nil; stale = false
            } catch { problem = error.localizedDescription }
        }
    }
    private func start() {
        guard let client = store.client else { return }
        busy = true; problem = nil
        struct Body: Encodable { let sources: [BatchReference]; let snapshot: String; let sendToModel: Bool; let requestID: UUID }
        Task {
            defer { busy = false }
            do {
                let data = try await client.sendData("v1/memory-batches", method: "POST", body: JSONEncoder().encode(Body(sources: plan.items.map(\.reference), snapshot: plan.snapshot, sendToModel: true, requestID: requestID)))
                taskID = try JSONDecoder().decode(BatchState.self, from: data).id
            } catch { problem = error.localizedDescription; stale = (error as? NodeFailure)?.statusCode == 409 }
        }
    }
}

private struct MemoryBatchProgressView: View {
    @ObservedObject var store: NodeWorkspaceStore
    let id: String
    @State private var state: BatchState?
    @State private var problem: String?
    @State private var busy = false
    @State private var refreshID = UUID()
    @State private var selected: BatchItem?
    #if os(macOS)
    @Environment(\.openSettings) private var openSettings
    #endif
    var body: some View {
        ScrollViewReader { proxy in
            Form {
                if let state {
                    Section {
                        Text(L(batchStatusTitle(state.status))).font(.headline)
                        ProgressView(value: Double(state.finishedCount), total: Double(max(1, state.items.count)))
                        Text(LF(state.segmented ? "已处理 %d / %d 段内容" : "已处理 %d / %d 份资料", state.finishedCount, state.items.count))
                        Text(LF("已保存 %d 条建议", state.candidateCount)).font(.callout)
                        if let issue = state.problem { Text(L(issue)).foregroundStyle(.secondary) }
                        if state.status == "pausing" { Text("当前资料完成后暂停，不再发送下一份。") }
                        else if state.running {
                            Text("可以关闭窗口，稍后从批量整理查看结果。").font(.footnote).foregroundStyle(.secondary)
                            Button("暂停后续资料") { update("pause") }.disabled(busy)
                        } else if state.status != "ready" {
                            Text("继续时只处理未完成项。已保存的建议会复用，来源版本变化的资料会跳过并提示。")
                                .font(.footnote).foregroundStyle(.secondary)
                            Text(L(state.remainingRequests == 0 ? "已有结果可以复用，无需再次发送资料。" : "服务未返回结果的请求，重试可能再次计费。"))
                                .font(.footnote).foregroundStyle(.secondary)
                            if !state.configured && state.remainingRequests > 0 {
                                Text("请先在电脑设置中配置文字模型。").font(.callout)
                                #if os(macOS)
                                Button("配置文字模型") {
                                    UserDefaults.standard.set("模型", forKey: "settings.section")
                                    UserDefaults.standard.set("summary", forKey: "model.focusRole"); openSettings()
                                }
                                #endif
                            }
                            Button("继续未完成项") { update("resume") }.disabled(busy || (!state.configured && state.remainingRequests > 0))
                            NavigationLink("重新选择并预览") {
                                MemoryBatchSelectionView(store: store, initialItems: state.items.filter { $0.status != "ready" })
                            }.disabled(busy)
                            Text("资料或服务变化时，可带回未完成项重新核对。发送后会新建任务，原任务与已完成结果保留。")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                        if let problem { Text(L(problem)).font(.callout).foregroundStyle(.secondary) }
                        Text("模型建议仍需核对，不会自动成为已确认记忆。").font(.footnote).foregroundStyle(.secondary)
                    }
                    .id("batch-state")
                    if !state.model.isEmpty && !state.endpoint.isEmpty {
                    Section("这次选择的服务") {
                        Text(verbatim: state.model)
                        Text(verbatim: state.endpoint).font(.caption).textSelection(.enabled)
                    }
                    }
                    Section("逐份进度") {
                        ForEach(state.items, id: \.identity) { item in
                            VStack(alignment: .leading, spacing: 10) {
                                Text(verbatim: item.title).font(.headline)
                                if let segment = item.segment { Text(segment.rangeTitle).font(.callout) }
                                Text(L(batchStatusTitle(item.status ?? "queued"))).font(.callout).foregroundStyle(.secondary)
                                if let issue = item.problem { Text(L(issue)).font(.callout).foregroundStyle(.secondary) }
                                if !(item.candidateIDs ?? []).isEmpty {
                                    Button(LF("查看 %d 条建议", item.candidateIDs?.count ?? 0)) { selected = item }
                                } else if item.status == "ready" {
                                    Text("没有提出适合长期保留的记忆。").font(.footnote).foregroundStyle(.secondary)
                                }
                            }.padding(.vertical, 6)
                        }
                    }
                } else if problem == nil { ProgressView("正在读取") }
                Section {
                    if state == nil, let problem { Text(L(problem)).foregroundStyle(.secondary) }
                    Button("重新读取") { refreshID = UUID() }.disabled(busy)
                }
            }.formStyle(.grouped).batchTitle("整理进度")
                .onChange(of: state?.status) { previous, current in
                    if previous != nil && previous != current {
                        proxy.scrollTo("batch-state", anchor: .top)
                    }
                }
                .onChange(of: store.status?.analysisConfigured) { _, _ in
                    if state?.running != true { refreshID = UUID() }
                }
                .task(id: refreshID) {
                    repeat {
                        await load()
                        guard state?.running == true else { break }
                        do { try await Task.sleep(for: .seconds(2)) } catch { break }
                    } while !Task.isCancelled
                }
                .sheet(isPresented: Binding(get: { selected != nil }, set: { if !$0 { selected = nil } })) {
                    if let selected { MemoryBatchResultsView(store: store, item: selected).appLanguage() }
                }
        }
    }
    @MainActor private func load() async {
        guard let client = store.client else { problem = "请先连接资料库。"; return }
        do {
            let value = try await client.read("v1/memory-batches/" + id, as: BatchState.self)
            guard !Task.isCancelled else { return }
            let changed = state?.candidateCount != value.candidateCount
            state = value; problem = nil
            if changed { await store.libraryChanged() }
        } catch is CancellationError {} catch { if !Task.isCancelled { problem = error.localizedDescription } }
    }
    private func update(_ action: String) {
        guard let client = store.client, let state else { return }
        busy = true; problem = nil
        struct Body: Encodable { let sendToModel: Bool; let model: String; let endpoint: String }
        Task {
            defer { busy = false }
            do {
                let data = try await client.sendData("v1/memory-batches/" + id + "/" + action, method: "POST", body: JSONEncoder().encode(Body(sendToModel: true, model: state.model, endpoint: state.endpoint)))
                self.state = try JSONDecoder().decode(BatchState.self, from: data); refreshID = UUID()
            } catch { problem = error.localizedDescription }
        }
    }
}

private struct MemoryBatchResultsView: View {
    @ObservedObject var store: NodeWorkspaceStore
    let item: BatchItem
    @Environment(\.dismiss) private var dismiss
    @State private var candidates: [NodeCandidate] = []
    @State private var selected: NodeCandidate?
    @State private var problem: String?
    var body: some View {
        NavigationStack {
            Form {
                Section { Text(verbatim: item.title).font(.headline); if let segment = item.segment { Text(segment.rangeTitle).font(.callout) } }
                ForEach(candidates) { candidate in
                    Button { selected = candidate } label: {
                        VStack(alignment: .leading, spacing: 10) {
                            Text(verbatim: candidate.text).fixedSize(horizontal: false, vertical: true)
                            Text(L(candidate.stateTitle)).font(.caption).foregroundStyle(.secondary)
                        }.frame(maxWidth: .infinity, minHeight: 44, alignment: .leading).padding(.vertical, 4)
                    }.buttonStyle(.plain)
                }
                if let problem { Text(L(problem)) }
                Text("打开建议核对原文、说话人与内容，再决定是否确认。").font(.footnote).foregroundStyle(.secondary)
            }.formStyle(.grouped).batchTitle("整理结果")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } } }
                .task { await load() }
                .sheet(item: $selected, onDismiss: { Task { await load() } }) { candidate in MemoryDetail(store: store, candidate: candidate).appLanguage() }
        }
        #if os(macOS)
        .frame(minWidth: 560, idealWidth: 720, minHeight: 500)
        #endif
    }
    @MainActor private func load() async {
        guard let client = store.client else { return }
        do {
            var values: [NodeCandidate] = []
            for id in item.candidateIDs ?? [] { values.append(try await client.read("v1/candidates/" + id, as: NodeCandidate.self)) }
            candidates = values; problem = nil
        } catch { problem = error.localizedDescription }
    }
}
