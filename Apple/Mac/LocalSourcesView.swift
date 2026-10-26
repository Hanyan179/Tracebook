import AppKit
import SwiftUI

private struct LocalSourceSummary: Decodable, Sendable {
    let inputs: Int
    let sessions: Int
    let activeDays: Int
    let characters: Int
    let files: Int
    let completedFiles: Int
    let pendingFiles: Int?
    let compressedFiles: Int?
    let unknownSizeFiles: Int?
    let waitingFiles: Int?
    let compressionAvailable: Bool?
    let excludedContextBlocks: Int?
    let pendingBytes: Int64
    let processedBytes: Int64
    let redactedInputs: Int
    let skippedRecords: Int
    let unreadableFiles: Int
    let fileLimitReached: Bool
    let days: [Day]
    struct Day: Decodable, Identifiable, Sendable {
        let day: String
        let count: Int
        var id: String { day }
    }
}
private struct LocalSourceConfig: Decodable, Sendable {
    var file: String?
    var present: Bool?
    var model: String?
    var unreadable: Bool?
}
private struct LocalSource: Decodable, Identifiable, Sendable {
    let id: String
    let provider: String
    let directory: String
    let automatic: Bool
    let summary: LocalSourceSummary
    let config: LocalSourceConfig
    let conflicts: [NodeConflict]
    let job: LocalSourceJob?
    var last_scan: String?
    var problem: String?
    var name: String { provider == "codex" ? "Codex" : "Claude Code" }
}
private struct LocalSourceJob: Decodable, Sendable {
    let state: String
    let batches: Int
    let problem: String?
    var active: Bool { ["queued", "running", "pausing"].contains(state) }
    var title: String {
        switch state {
        case "queued", "running": "正在导入全部历史"
        case "pausing": "正在完成当前批次并暂停"
        case "paused": "历史导入已暂停"
        case "complete": "历史扫描已完成"
        case "waiting": "等待来源写入完成"
        default: "历史导入需要处理"
        }
    }
}
private struct LocalDiscovery: Decodable, Identifiable, Sendable {
    let provider: String
    let directory: String
    let available: Bool
    var id: String { provider }
    var name: String { provider == "codex" ? "Codex" : "Claude Code" }
}
private struct LocalSourceListing: Decodable, Sendable {
    let sources: [LocalSource]
    let discovered: [LocalDiscovery]
}
private struct LocalSourcePreview: Decodable, Identifiable, Sendable {
    let id: String
    let sourceID: String
    let provider: String
    let directory: String
    let summary: LocalSourceSummary
    let added: Int
    let updated: Int
    let config: LocalSourceConfig
    let automatic: Bool
    let samples: [Sample]
    struct Sample: Decodable, Sendable { let text: String; let timestamp: String }
}
private struct LocalSourceReport: Decodable, Sendable {
    let added: Int
    let updated: Int
    let conflicts: Int
}
private struct LocalRecentInput: Decodable, Identifiable, Sendable {
    let source: KnowledgeSource
    let provider: String
    var id: String { source.id }
    var providerName: String { provider == "codex" ? "Codex" : "Claude Code" }
    private enum CodingKeys: String, CodingKey { case provider }
    init(from decoder: Decoder) throws {
        source = try KnowledgeSource(from: decoder)
        provider = try decoder.container(keyedBy: CodingKeys.self).decode(String.self, forKey: .provider)
    }
}
private struct LocalRecentPage: Decodable, Sendable {
    let items: [LocalRecentInput]
}

struct LocalSourcesView: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @State private var listing: LocalSourceListing?
    @State private var preview: LocalSourcePreview?
    @State private var selected: LocalSource?
    @State private var coverageSource: LocalSource?
    @State private var inspectingInput: KnowledgeSource?
    @State private var recentInputs: [LocalRecentInput] = []
    @State private var showingDocuments = false
    @State private var documentCount = 0
    @State private var automatic = true
    @State private var busy = false
    @State private var loading = false
    @State private var problem: String?
    @State private var loadProblem: String?
    @State private var recentProblem: String?
    @State private var result: String?
    private var isLocal: Bool { ["localhost", "127.0.0.1", "::1", "[::1]"].contains(workspace.client?.endpoint.host ?? "") }
    private var hasSources: Bool { listing?.sources.isEmpty == false || documentCount > 0 }
    var body: some View {
        ScrollViewReader { scroller in
            GeometryReader { geometry in
                ScrollView {
                Group {
                    if !isLocal {
                        ContentUnavailableView("请在本机管理资料来源", systemImage: "desktopcomputer",
                            description: Text("先连接这台电脑的资料库，再选择工具目录。"))
                    } else if let listing {
                        if !hasSources {
                            emptyState(listing, width: min(1040, max(0, geometry.size.width - 64)))
                        } else {
                            archiveState(listing, width: min(1320, max(0, geometry.size.width - 64)))
                        }
                    } else if let loadProblem {
                        ContentUnavailableView {
                            Label("暂时无法读取资料来源", systemImage: "exclamationmark.circle")
                        } description: { Text(L(loadProblem)) } actions: { reloadButton }
                    } else {
                        ProgressView("正在读取资料来源")
                    }
                }
                .frame(maxWidth: hasSources ? 1320 : 1040)
                .padding(32)
                .frame(maxWidth: .infinity, alignment: .leading)
                .frame(minHeight: geometry.size.height, alignment: .topLeading)
                .id("source-page-start")
                }
            }
            .onChange(of: problem) { _, message in
                if message != nil { scroller.scrollTo("source-page-start", anchor: .top) }
            }
            .onChange(of: result) { _, message in
                if message != nil { scroller.scrollTo("source-page-start", anchor: .top) }
            }
            .onChange(of: hasSources) { _, _ in scroller.scrollTo("source-page-start", anchor: .top) }
        }
        .task(id: workspace.endpoint + String(workspace.connected)) {
            repeat {
                if !busy { await load() }
                do { try await Task.sleep(for: .seconds(listing?.sources.contains(where: { $0.job?.active == true }) == true ? 2 : 15)) } catch { break }
            } while !Task.isCancelled
        }
        .sheet(item: $preview) { value in
            NavigationStack {
                Form {
                    Section("扫描预览") {
                        Text(verbatim: value.directory).font(.caption).textSelection(.enabled)
                        LabeledContent("本次新增输入", value: String(value.added))
                        LabeledContent("本次更新输入", value: String(value.updated))
                        LabeledContent("归档后累计输入", value: String(value.summary.inputs))
                        LabeledContent("会话", value: String(value.summary.sessions))
                        coverage(value.summary)
                    }
                    Section("归档方式") {
                        Text("连接后会在后台继续导入全部可读取的历史。可以暂停，重新打开应用后会从已保存的位置继续。")
                        Toggle("自动归档新增输入", isOn: $automatic)
                        Text("启用后，应用运行时每分钟分批检查新增内容。关闭后仍保留已归档文件，可随时手动扫描。")
                        Text("不包含助手回答、工具输出、登录令牌和完整配置。识别到的常见凭证会隐藏；请在预览中检查敏感内容。")
                        Text("原来源删减不会自动删除已归档内容。人工修改冲突会另存。")
                    }
                    if !value.samples.isEmpty {
                        Section("本人输入样本") {
                            ForEach(Array(value.samples.enumerated()), id: \.offset) { _, sample in
                                VStack(alignment: .leading, spacing: 8) {
                                    Text(verbatim: sample.timestamp).font(.caption).foregroundStyle(.secondary)
                                    Text(verbatim: sample.text).lineLimit(6).textSelection(.enabled)
                                }
                            }
                        }
                    } else { Section { Text("这一批还没有识别到本人输入，可连接后继续扫描。") } }
                    if let problem { Section { Text(L(problem)).foregroundStyle(.secondary) } }
                }.formStyle(.grouped).disabled(busy)
                    .navigationTitle(L("连接资料来源"))
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) { Button("取消") { Task { await cancel(value) } }.disabled(busy) }
                        ToolbarItem(placement: .confirmationAction) { Button("确认归档") { Task { await commit(value) } }.disabled(busy) }
                    }
            }.frame(minWidth: 660, idealWidth: 760, minHeight: 620, idealHeight: 760)
                .interactiveDismissDisabled().appLanguage()
        }
        .sheet(item: $selected) { source in LocalSourceInputsView(workspace: workspace, source: source).appLanguage() }
        .sheet(item: $coverageSource, onDismiss: { Task { await load() } }) { source in
            LocalSourceCoverageView(workspace: workspace, source: source).appLanguage()
        }
        .sheet(item: $inspectingInput) { item in KnowledgeSourceReader(workspace: workspace, source: item).appLanguage() }
        .sheet(isPresented: $showingDocuments, onDismiss: { Task { await load() } }) { DocumentLibraryView(workspace: workspace).appLanguage() }
    }
    private var reloadButton: some View {
        Button("刷新", systemImage: "arrow.clockwise") { problem = nil; Task { await load() } }
            .disabled(busy || loading)
    }
    @ViewBuilder private var operationStatus: some View {
        if let message = problem ?? loadProblem {
            Label(L(message), systemImage: "exclamationmark.circle").font(.callout)
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        if busy { HStack(spacing: 10) { ProgressView().controlSize(.small);Text("正在处理本地资料").font(.callout) } }
        if let result { Text(verbatim: result).font(.callout).foregroundStyle(.secondary) }
    }
    private func emptyState(_ listing: LocalSourceListing, width: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 22) {
            VStack(alignment: .leading, spacing: 8) {
                Text("添加第一份资料").font(.title2.weight(.semibold))
                Text("导入文档，或连接工具中的本人输入。原文与来源一起保留。")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            operationStatus
            documentsCard
            Text("连接工具输入").font(.headline).padding(.top, 4)
            discoveryGrid(listing, width: width)
            destination
            privacyNote
        }
    }
    private func archiveState(_ listing: LocalSourceListing, width: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 24) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("已归档资料").font(.title2.weight(.semibold))
                    Text(listing.sources.isEmpty ? LF("已导入 %d 份文字资料", documentCount) : LF("已连接 %d 个来源，归档 %d 条本人输入。", listing.sources.count,
                            listing.sources.reduce(0) { $0 + $1.summary.inputs })).foregroundStyle(.secondary)
                }
                Spacer(minLength: 16)
                if loading { ProgressView().controlSize(.small) }
                reloadButton
            }
            operationStatus
            documentsCard
            let columns = width >= 1100 && listing.sources.count > 1 ? 2 : 1
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 20, alignment: .topLeading), count: columns), spacing: 20) {
                ForEach(listing.sources) { source in
                    sourceCard(source, width: (width - CGFloat(columns - 1) * 20) / CGFloat(columns))
                }
            }
            if !listing.sources.isEmpty { recentInputList }
            DisclosureGroup("添加资料来源") {
                discoveryGrid(listing, width: width).padding(.top, 16)
            }.font(.headline)
            destination
            privacyNote
        }
    }
    private var documentsCard: some View {
        SurfaceCard(inset: 18, cornerRadius: 14) {
            HStack(spacing: 16) {
                Image(systemName: "doc.text").font(.title2).foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 6) {
                    Text("文字资料").font(.headline)
                    Text(documentCount > 0 ? LF("已导入 %d 份文字资料", documentCount) : L("导入文档或图片中的文字。"))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button(L(documentCount > 0 ? "查看与导入" : "导入文字资料")) { showingDocuments = true }
                    .buttonStyle(FilledButtonStyle()).disabled(busy)
            }
        }
    }
    @ViewBuilder private var destination: some View {
        if let directory = workspace.status?.outputDirectory {
            HStack(spacing: 16) {
                Image(systemName: "folder").font(.title3).foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 6) {
                    Text("归档到个人资料夹").font(.subheadline.weight(.semibold))
                    Text(verbatim: directory).font(.caption).foregroundStyle(.secondary)
                        .lineLimit(2).truncationMode(.middle).textSelection(.enabled).help(directory)
                }.frame(maxWidth: .infinity, alignment: .leading)
                Button("打开资料夹") { NSWorkspace.shared.open(URL(fileURLWithPath: directory)) }
                    .disabled(!isLocal).controlSize(.small)
            }.padding(18).background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 12))
        }
    }
    private var privacyNote: some View {
        Label("来源只读，登录文件不归档；本地输入不自动发送给模型。", systemImage: "lock")
            .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
    }
    private func discoveryGrid(_ listing: LocalSourceListing, width: CGFloat) -> some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 20, alignment: .topLeading),
                                 count: width >= 760 ? 2 : 1), spacing: 20) {
            ForEach(listing.discovered) { candidate in
                let connected = listing.sources.contains(where: { $0.directory == candidate.directory })
                SurfaceCard(inset: 18, cornerRadius: 14) {
                    VStack(alignment: .leading, spacing: 14) {
                        Label(candidate.name, systemImage: "terminal").font(.title3.weight(.semibold))
                        Text(L(connected ? "此目录已连接" : candidate.available ? "已找到目录，尚未归档" : "选择目录后即可预览"))
                            .font(.callout).foregroundStyle(.secondary)
                        Text(verbatim: candidate.directory).font(.caption).foregroundStyle(.secondary)
                            .lineLimit(2).textSelection(.enabled).help(candidate.directory)
                        ViewThatFits(in: .horizontal) {
                            HStack(spacing: 12) { discoveryActions(candidate, connected: connected) }
                            VStack(alignment: .leading, spacing: 12) { discoveryActions(candidate, connected: connected) }
                        }.disabled(busy || loading || !workspace.connected)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }
    @ViewBuilder private func discoveryActions(_ candidate: LocalDiscovery, connected: Bool) -> some View {
        if candidate.available && !connected {
            Button("预览并连接") { scan(candidate.provider, candidate.directory) }.buttonStyle(.bordered)
            Button("选择其他目录") { choose(candidate) }.buttonStyle(.borderless)
        } else {
            Button(L(connected ? "选择其他目录" : "选择目录")) { choose(candidate) }.buttonStyle(.bordered)
        }
    }
    private var recentInputList: some View {
        SurfaceCard(inset: 18, cornerRadius: 14) {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Text("最近的本人输入").font(.headline)
                    Spacer()
                    if !recentInputs.isEmpty { Text(LF("显示最近 %d 条", recentInputs.count)).font(.caption).foregroundStyle(.secondary) }
                }
                if let recentProblem {
                    Text(L(recentProblem)).font(.callout).foregroundStyle(.secondary)
                    Button("重新读取") { Task { await loadRecent() } }.disabled(loading || busy)
                } else if recentInputs.isEmpty {
                    Text(loading ? L("正在读取资料来源") : L("尚未归档本人输入，可继续扫描历史记录，或等待来源产生新输入。"))
                        .font(.callout).foregroundStyle(.secondary).padding(.vertical, 16)
                }
                ForEach(recentInputs) { item in
                    if item.id != recentInputs.first?.id { Divider() }
                    Button { inspectingInput = item.source } label: {
                        HStack(spacing: 20) {
                            VStack(alignment: .leading, spacing: 8) {
                                Text(verbatim: item.source.title).font(.body).lineLimit(2).foregroundStyle(.primary)
                                Text(verbatim: item.providerName + " · " + item.source.date).font(.caption).foregroundStyle(.secondary)
                                if !item.source.textAvailable { Text("原文文件暂不可读").font(.caption).foregroundStyle(.secondary) }
                            }
                            Spacer(minLength: 0)
                            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
                        }.padding(.vertical, 8).frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
                    }.buttonStyle(.plain).disabled(!item.source.textAvailable)
                        .accessibilityLabel(item.source.title + ", " + item.providerName + ", " + item.source.date)
                        .accessibilityHint("查看完整输入")
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
    private func sourceCard(_ source: LocalSource, width: CGFloat) -> some View {
        SurfaceCard(inset: 18, cornerRadius: 14) {
            VStack(alignment: .leading, spacing: 18) {
                ViewThatFits(in: .horizontal) {
                    HStack { Text(source.name).font(.headline); Spacer(); automaticToggle(source) }
                    VStack(alignment: .leading) { Text(source.name).font(.headline); automaticToggle(source) }
                }
                Text(verbatim: source.directory).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), alignment: .leading), count: width > 720 ? 4 : 2), alignment: .leading, spacing: 18) {
                    metric("本人输入", source.summary.inputs);metric("会话", source.summary.sessions)
                    metric("活跃天数", source.summary.activeDays);metric("输入字符", source.summary.characters)
                }
                coverage(source.summary)
                Button("查看扫描详情", systemImage: "list.bullet.rectangle") { coverageSource = source }
                if let job = source.job {
                    HStack(spacing: 8) {
                        if job.active { ProgressView().controlSize(.small) }
                        Text(L(job.title)).font(.caption)
                    }
                    if let problem = job.problem { Text(L(problem)).font(.caption).foregroundStyle(.secondary) }
                }
                if let model = source.config.model { LabeledContent("配置中的模型", value: model).font(.caption) }
                else { Text(L(source.config.present == true ? "已找到配置，仅记录基础摘要。" : "未找到配置文件，会话资料仍可归档。")).font(.caption).foregroundStyle(.secondary) }
                if !source.summary.days.isEmpty {
                    DisclosureGroup("最近有输入的日期") {
                        ForEach(source.summary.days) { day in
                            HStack { Text(verbatim: day.day); Spacer(); Text(LF("%d 条输入", day.count)) }
                                .font(.caption).padding(.vertical, 3)
                        }
                    }
                }
                if let problem = source.problem { Label(L(problem), systemImage: "exclamationmark.circle").font(.caption) }
                if !source.conflicts.isEmpty {
                    ViewThatFits(in: .horizontal) {
                        HStack {
                        Text(LF("%d 份人工修改已保留，新内容另存。", source.conflicts.count)).font(.caption)
                            conflictButton(source)
                        }
                        VStack(alignment: .leading, spacing: 8) {
                            Text(LF("%d 份人工修改已保留，新内容另存。", source.conflicts.count)).font(.caption)
                            conflictButton(source)
                        }
                    }
                }
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                    if source.job?.active == true {
                        Button("暂停历史导入", systemImage: "pause") {
                            run { client in _ = try await send(client, "v1/local-sources/"+source.id+"/pause-import") }
                        }.disabled(busy || source.job?.state == "pausing")
                    } else {
                        Button(L(source.summary.completedFiles < source.summary.files ? "导入剩余历史" : "扫描新增"), systemImage: "arrow.clockwise") { refresh(source) }.disabled(busy)
                    }
                    Button("查看输入", systemImage: "text.alignleft") { selected = source }.disabled(source.summary.inputs == 0)
                    }
                    if let time = source.last_scan, let date = ISO8601DateFormatter().date(from: time) {
                        Text(date, format: .dateTime.year().month().day().hour().minute()).font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
    private func conflictButton(_ source: LocalSource) -> some View {
        Button("查看冲突文件") {
            guard let root = workspace.status?.outputDirectory else { return }
            let files = source.conflicts.compactMap(\.generatedPath).map { URL(fileURLWithPath: root).appendingPathComponent($0) }
            NSWorkspace.shared.activateFileViewerSelecting(files)
        }
    }
    private func automaticToggle(_ source: LocalSource) -> some View {
        Toggle("自动归档", isOn: Binding(get: { source.automatic }, set: { value in
            run { client in
                _ = try await send(client, "v1/local-sources/"+source.id, method: "PUT", values: ["automatic": value])
            }
        })).toggleStyle(.switch).fixedSize().disabled(busy)
    }
    private func metric(_ title: String, _ value: Int) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(value, format: .number).font(.title2.monospacedDigit())
            Text(L(title)).font(.caption).foregroundStyle(.secondary)
        }
    }
    @ViewBuilder private func coverage(_ summary: LocalSourceSummary) -> some View {
        Text(LF("已扫描 %d/%d 个会话文件", summary.completedFiles, summary.files)).font(.caption).foregroundStyle(.secondary)
        if let compressed = summary.compressedFiles, compressed > 0 {
            Text(LF("包含 %d 个压缩会话文件", compressed)).font(.caption).foregroundStyle(.secondary)
            if summary.compressionAvailable == false {
                Text("压缩读取组件不可用，这部分历史尚未导入。").font(.caption).foregroundStyle(.secondary)
            }
        }
        if summary.files > 0 && summary.pendingBytes > 0 && (summary.unknownSizeFiles ?? 0) == 0 {
            ProgressView(value: Double(summary.processedBytes), total: Double(summary.processedBytes + summary.pendingBytes))
        }
        if (summary.unknownSizeFiles ?? 0) > 0 {
            Label(LF("还有 %d 个会话文件待处理，部分数据大小尚不可知。", summary.files-summary.completedFiles), systemImage: "clock")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        } else if summary.pendingBytes > 0 {
            Label(LF("还有 %@ 历史数据待扫描，当前统计只覆盖已读取部分。",
                     ByteCountFormatter.string(fromByteCount: summary.pendingBytes, countStyle: .file)), systemImage: "clock")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        if summary.skippedRecords > 0 || summary.unreadableFiles > 0 {
            Text(LF("跳过 %d 条过大或异常记录；%d 个文件暂不可读。", summary.skippedRecords, summary.unreadableFiles))
                .font(.caption).foregroundStyle(.secondary)
        }
        if let waiting = summary.waitingFiles, waiting > 0 {
            Text(LF("%d 个文件的末条记录尚未完整，已保留读取位置。", waiting))
                .font(.caption).foregroundStyle(.secondary)
        }
        if summary.redactedInputs > 0 { Text(LF("%d 条输入中的常见凭证已隐藏。", summary.redactedInputs)).font(.caption).foregroundStyle(.secondary) }
        if let count = summary.excludedContextBlocks, count > 0 { Text(LF("已排除 %d 段系统或引用上下文。", count)).font(.caption).foregroundStyle(.secondary) }
        if summary.fileLimitReached { Text("会话文件达到本次扫描上限，统计不是全部历史。").font(.caption).foregroundStyle(.secondary) }
    }
    private func choose(_ value: LocalDiscovery) {
        let panel = NSOpenPanel();panel.canChooseDirectories = true;panel.canChooseFiles = false
        panel.allowsMultipleSelection = false;panel.showsHiddenFiles = true
        panel.directoryURL = URL(fileURLWithPath: value.directory).deletingLastPathComponent()
        panel.prompt = L("预览此目录")
        guard panel.runModal() == .OK, let url = panel.url else { return }
        scan(value.provider, url.path)
    }
    private func scan(_ provider: String, _ directory: String) {
        run { client in
            let data = try await send(client, "v1/local-sources/preview", values: ["provider": provider, "directory": directory])
            let value = try JSONDecoder().decode(LocalSourcePreview.self, from: data)
            automatic = listing?.sources.contains(where: { $0.id == value.sourceID }) == true ? value.automatic : true
            preview = value
        }
    }
    private func refresh(_ source: LocalSource) {
        run { client in
            _ = try await send(client, "v1/local-sources/"+source.id+"/import-history")
            result = nil
        }
    }
    private func commit(_ value: LocalSourcePreview) async {
        guard let client = workspace.client else { return }
        busy = true;problem = nil;defer { busy = false }
        do {
            let data = try await send(client, "v1/local-sources/previews/"+value.id+"/commit", values: ["automatic": automatic])
            showReport(try JSONDecoder().decode(LocalSourceReport.self, from: data));preview = nil
            if value.summary.completedFiles < value.summary.files {
                _ = try await send(client, "v1/local-sources/"+value.sourceID+"/import-history")
                result = nil
            }
            await workspace.libraryChanged();await load()
        } catch { problem = error.localizedDescription }
    }
    private func cancel(_ value: LocalSourcePreview) async {
        guard let client = workspace.client else { preview = nil;return }
        busy = true;defer { busy = false }
        do { _ = try await send(client, "v1/local-sources/previews/"+value.id+"/cancel");preview = nil }
        catch { problem = error.localizedDescription }
    }
    private func showReport(_ report: LocalSourceReport) {
        result = LF("新增 %d 条，更新 %d 条，%d 份人工修改需核对。", report.added, report.updated, report.conflicts)
    }
    private func load() async {
        guard !loading, isLocal, let client = workspace.client else { return }
        loading = true
        defer { loading = false }
        do {
            listing = try await client.read("v1/local-sources", as: LocalSourceListing.self)
            documentCount = try await client.read("v1/documents", as: DocumentPage.self).count
            loadProblem = nil
            if hasSources { await loadRecent() } else { recentInputs = [];recentProblem = nil }
        }
        catch { loadProblem = error.localizedDescription }
    }
    private func loadRecent() async {
        guard let client = workspace.client else { return }
        do {
            recentInputs = try await client.read("v1/local-sources/recent-inputs", query: ["limit": "8"], as: LocalRecentPage.self).items
            recentProblem = nil
        } catch { recentProblem = error.localizedDescription }
    }
    private func send(_ client: NodeClient, _ path: String, method: String = "POST", values: [String: Any] = [:]) async throws -> Data {
        try await client.sendData(path, method: method, body: JSONSerialization.data(withJSONObject: values))
    }
    private func run(_ action: @escaping @MainActor (NodeClient) async throws -> Void) {
        guard !busy, let client = workspace.client else { return }
        busy = true;problem = nil;result = nil
        Task { defer { busy = false };do { try await action(client);await load() } catch { problem = error.localizedDescription } }
    }
}

private struct LocalSourceFile: Decodable, Identifiable, Sendable {
    let path: String
    let stateVersion: String
    let status: String
    let issues: Issues
    let processedBytes: Int64
    let totalBytes: Int64?
    var id: String { path }
    struct Issues: Decodable, Sendable {
        let oversized: Int
        let malformed: Int
        let invalidDates: Int
        let other: Int
    }
    var title: String {
        switch status {
        case "unreadable": "文件暂不可读"
        case "waiting": "末条记录尚未完整"
        case "pending": "尚未读完"
        case "skipped": "读取完成，有记录跳过"
        default: "读取完成"
        }
    }
    var symbol: String {
        switch status {
        case "unreadable", "skipped": "exclamationmark.circle"
        case "waiting", "pending": "clock"
        default: "checkmark.circle"
        }
    }
}

private struct LocalSourceCoverageView: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let source: LocalSource
    @Environment(\.dismiss) private var dismiss
    @State private var page: Page?
    @State private var items: [LocalSourceFile] = []
    @State private var attentionOnly = true
    @State private var loading = false
    @State private var retrying: String?
    @State private var problem: String?
    @State private var result: String?
    @State private var issueFile: LocalSourceFile?
    private var path: String { "v1/local-sources/" + source.id }
    private struct Page: Decodable, Sendable {
        let items: [LocalSourceFile]
        let total: Int
        let nextOffset: Int?
        let summary: LocalSourceSummary
        let importActive: Bool
        let issuePositionsSupported: Bool?
    }
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text(verbatim: source.name).font(.headline)
                    Text("重新检查只读取所选文件，保留已归档输入和人工修改。")
                        .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Toggle("仅显示待处理或有跳过的文件", isOn: $attentionOnly)
                        .disabled(loading || retrying != nil)
                    if let page {
                        Text(LF("已扫描 %d/%d 个会话文件", page.summary.completedFiles, page.summary.files))
                            .font(.callout).foregroundStyle(.secondary)
                        if page.importActive {
                            Text("请先暂停历史导入，再单独检查文件。")
                                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if let problem { Label(L(problem), systemImage: "exclamationmark.circle").foregroundStyle(.secondary) }
                    if let result { Text(verbatim: result).foregroundStyle(.secondary).textSelection(.enabled) }
                }
                if items.isEmpty, !loading, page != nil {
                    Section {
                        Label("当前范围没有待处理或跳过的文件。", systemImage: "checkmark.circle")
                        Text("读取完成表示已检查文件；助手回答、工具输出等内容仍按规则排除。")
                            .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
                ForEach(items) { file in
                    Section {
                        VStack(alignment: .leading, spacing: 10) {
                            Text(verbatim: file.path).font(.callout.monospaced()).textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                            Label(L(file.title), systemImage: file.symbol).font(.headline)
                            if file.status == "unreadable" {
                                Text("请确认文件已下载完整且可读取，再重新检查。")
                                    .fixedSize(horizontal: false, vertical: true)
                            } else if file.status == "waiting" {
                                Text("已保留读取位置。来源写完后可继续检查；若长期不变，请检查源文件。")
                                    .fixedSize(horizontal: false, vertical: true)
                            } else if file.status == "pending" {
                                Text("本次按上限继续读取，未完成部分仍保留为待处理。")
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            if file.issues.oversized > 0 { Text(LF("%d 条记录超过大小上限。", file.issues.oversized)) }
                            if file.issues.malformed > 0 { Text(LF("%d 条记录格式不完整或无法解析。", file.issues.malformed)) }
                            if file.issues.invalidDates > 0 { Text(LF("%d 条本人输入缺少有效时间，未推测归档日期。", file.issues.invalidDates)) }
                            if file.issues.other > 0 { Text(LF("%d 条记录未被识别，或旧扫描未区分原因。", file.issues.other)) }
                            if file.issues.oversized + file.issues.malformed + file.issues.invalidDates + file.issues.other > 0 {
                                Button("查看跳过记录的位置") { issueFile = file }
                                    .disabled(page?.issuePositionsSupported != true)
                                    .accessibilityLabel(L("查看跳过记录的位置") + " · " + file.path)
                                if page?.issuePositionsSupported != true {
                                    Text("当前节点不支持记录位置，请更新电脑应用。").font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            Text(byteProgress(file)).font(.caption).foregroundStyle(.secondary)
                            HStack(spacing: 10) {
                                Button("重新检查此文件") { retry(file) }
                                    .disabled(loading || retrying != nil || page?.importActive == true)
                                    .accessibilityLabel(L("重新检查此文件") + " · " + file.path)
                                if retrying == file.path { ProgressView().controlSize(.small) }
                            }
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 4)
                    }
                }
                if loading { Section { ProgressView("正在读取") } }
                if let next = page?.nextOffset {
                    Section { Button("加载更多") { Task { await load(offset: next) } }.disabled(loading || retrying != nil) }
                }
            }.formStyle(.grouped)
                .sheet(item: $issueFile) { file in
                    LocalSourceIssuesView(workspace: workspace, sourceID: source.id, filePath: file.path).appLanguage()
                }
                .navigationTitle(L("扫描详情"))
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("关闭") { dismiss() }.disabled(retrying != nil) }
                    ToolbarItem(placement: .primaryAction) {
                        Button("刷新") { Task { await load() } }.disabled(loading || retrying != nil)
                    }
                }
        }.frame(minWidth: 520, idealWidth: 640, minHeight: 480, idealHeight: 700)
            .task(id: attentionOnly) { await load() }
            .interactiveDismissDisabled(retrying != nil)
    }
    private func byteProgress(_ file: LocalSourceFile) -> String {
        let format = ByteCountFormatStyle(style: .file, spellsOutZero: false).locale(AppLanguage.current.locale)
        let read = file.processedBytes.formatted(format)
        if let total = file.totalBytes {
            return LF("已读取 %@ / %@", read, total.formatted(format))
        }
        return LF("已读取 %@，总大小未知。", read)
    }
    private func load(offset: Int = 0) async {
        guard let client = workspace.client else { problem = "请先连接资料库。"; return }
        loading = true
        defer { loading = false }
        do {
            let value = try await client.read(path + "/files", query: ["offset": String(offset), "attention": String(attentionOnly)], as: Page.self)
            items = offset == 0 ? value.items : items + value.items.filter { new in !items.contains { $0.id == new.id } }
            page = value; problem = nil
        } catch { problem = error.localizedDescription }
    }
    private func retry(_ file: LocalSourceFile) {
        guard let client = workspace.client, retrying == nil else { return }
        retrying = file.path; problem = nil; result = nil
        Task {
            defer { retrying = nil }
            do {
                let body = try JSONSerialization.data(withJSONObject: ["path": file.path, "stateVersion": file.stateVersion])
                let data = try await client.sendData(path + "/retry-file", method: "POST", body: body)
                let report = try JSONDecoder().decode(LocalSourceReport.self, from: data)
                result = LF("新增 %d 条，更新 %d 条，%d 份人工修改需核对。", report.added, report.updated, report.conflicts)
                await workspace.libraryChanged()
                await load()
            } catch { problem = error.localizedDescription }
        }
    }
}

private struct LocalSourceInputsView: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let source: LocalSource
    @Environment(\.dismiss) private var dismiss
    @State private var items: [KnowledgeSource] = []
    @State private var nextOffset: Int?
    @State private var selected: KnowledgeSource?
    @State private var busy = false
    @State private var problem: String?
    private struct Page: Decodable, Sendable { let items: [KnowledgeSource]; let nextOffset: Int? }
    var body: some View {
        NavigationStack {
            List {
                ForEach(items) { item in
                    Button { selected = item } label: {
                        HStack(spacing: 16) {
                            VStack(alignment: .leading, spacing: 7) {
                                Text(verbatim: item.title).lineLimit(3).foregroundStyle(.primary)
                                Text(verbatim: item.date).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer(minLength: 0)
                            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
                        }.padding(.vertical, 8).frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
                    }.buttonStyle(.plain)
                }
                if busy { ProgressView() }
                if let problem { Text(L(problem));Button("重新读取") { Task { await load(reset: true) } } }
                if nextOffset != nil { Button("更多输入") { Task { await load(reset: false) } }.disabled(busy) }
            }.navigationTitle(source.name + " · " + L("本人输入"))
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
        }.frame(minWidth: 660, idealWidth: 760, minHeight: 600, idealHeight: 760)
            .task { await load(reset: true) }
            .sheet(item: $selected) { item in KnowledgeSourceReader(workspace: workspace, source: item).appLanguage() }
    }
    private func load(reset: Bool) async {
        guard let client = workspace.client, !busy else { return }
        busy = true;problem = nil;defer { busy = false }
        do {
            let page = try await client.read("v1/local-sources/"+source.id+"/inputs",
                query: ["offset": String(reset ? 0 : (nextOffset ?? 0)), "limit": "40"], as: Page.self)
            items = reset ? page.items : items + page.items;nextOffset = page.nextOffset
        } catch { problem = error.localizedDescription }
    }
}
