import SwiftUI

struct MemorySourceSegment: Codable, Sendable {
    let start: Int
    let end: Int
    var contextStart: Int?
    var contextEnd: Int?
    var index: Int?
    var count: Int?
    var totalCharacters: Int?
    var rangeTitle: String { LF("第 %d / %d 段 · 原文位置 %d–%d", index ?? 1, count ?? 1, start + 1, end) }
}

private struct MemorySourceScope: Decodable, Sendable, Identifiable {
    struct Unit: Decodable, Sendable {
        let segment: MemorySourceSegment?
        let characters: Int
        let focusCharacters: Int
        let excerpt: String
        let textAvailable: Bool
        let reusable: Bool
        let status: String
    }
    let kind: String
    let id: String
    let title: String
    let date: String
    let contentVersion: String
    let characters: Int
    let units: [Unit]
    var identity: String { kind + "/" + id }
    var choices: [MemorySegmentChoice] { units.map { .init(source: self, unit: $0) } }
}

private struct MemorySegmentChoice: Identifiable {
    let source: MemorySourceScope
    let unit: MemorySourceScope.Unit
    var reference: BatchReference {
        .init(kind: source.kind, id: source.id, segment: unit.segment,
              contentVersion: unit.segment == nil ? nil : source.contentVersion)
    }
    var id: String { reference.identity + "/" + source.contentVersion }
}

/// This is a read-only scope picker. Only the existing final batch preview can start requests.
struct MemorySegmentSelectionView: View {
    @ObservedObject var store: NodeWorkspaceStore
    let sources: [BatchReference]
    var preferred: [BatchReference] = []
    @Environment(\.dismiss) private var dismiss
    @State private var scopes: [MemorySourceScope] = []
    @State private var selection = Set<String>()
    @State private var visible: [String: Int] = [:]
    @State private var loading = true
    @State private var loaded = 0
    @State private var failures: [String] = []
    @State private var problem: String?
    @State private var reviewing: MemorySegmentChoice?
    @State private var plan: BatchPlan?
    @State private var showingPlan = false
    @State private var previewing = false
    private var choices: [MemorySegmentChoice] { scopes.flatMap(\.choices) }
    private var selected: [MemorySegmentChoice] { choices.filter { selection.contains($0.id) } }
    private var selectedCharacters: Int { selected.reduce(0) { $0 + $1.unit.focusCharacters } }
    private var totalCharacters: Int { scopes.reduce(0) { $0 + $1.characters } }
    private var contextCharacters: Int { selected.reduce(0) { $0 + $1.unit.characters - $1.unit.focusCharacters } }
    var body: some View {
        NavigationStack {
            Form {
                Section("本次范围") {
                    if loading { ProgressView(LF("正在读取范围 %d / %d", loaded, sources.count)) }
                    Text(LF("已选择 %d / 20 段内容", selected.count)).font(.headline)
                    Text(LF("已选正文 %d / %d 个字符", selectedCharacters, totalCharacters))
                    Text(LF("另带 %d 个字符的前后文用于核对归属。", contextCharacters))
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("每段最多发送 32000 个字符，包含前后文。只从所选正文提出建议；未选部分不会自动整理。")
                        .font(.footnote).foregroundStyle(.secondary)
                    Button("选择下一批未整理内容") {
                        selection = Set(choices.filter { $0.unit.textAvailable && !$0.unit.reusable }.prefix(20).map(\.id))
                    }.disabled(loading || previewing || !failures.isEmpty)
                    if !selection.isEmpty { Button("清除选择") { selection.removeAll() }.disabled(previewing) }
                }
                ForEach(scopes, id: \.identity) { scope in
                    Section {
                        Text(verbatim: scope.title).font(.headline)
                        Text(LF("当前版本已有结果：%d / %d 段内容", scope.units.filter(\.reusable).count, scope.units.count))
                            .font(.callout).foregroundStyle(.secondary)
                        ForEach(Array(scope.choices.prefix(visible[scope.identity] ?? 20))) { choice in
                            VStack(alignment: .leading, spacing: 10) {
                                Button { toggle(choice) } label: {
                                    HStack(alignment: .top, spacing: 12) {
                                        Image(systemName: selection.contains(choice.id) ? "checkmark.circle.fill" : "circle")
                                        VStack(alignment: .leading, spacing: 6) {
                                            Text(choice.unit.segment?.rangeTitle ?? L("完整文字"))
                                                .fixedSize(horizontal: false, vertical: true)
                                            Text(LF("正文 %d 个字符 · 含前后文共 %d 个字符", choice.unit.focusCharacters, choice.unit.characters))
                                                .font(.caption).foregroundStyle(.secondary)
                                        }
                                    }.frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                                }.buttonStyle(.plain)
                                    .disabled(!choice.unit.textAvailable || previewing || (selection.count >= 20 && !selection.contains(choice.id)))
                                    .accessibilityLabel(LF("选择范围：%@", choice.unit.segment?.rangeTitle ?? L("完整文字")))
                                    .accessibilityValue(L(selection.contains(choice.id) ? "已选择" : "未选择"))
                                if !choice.unit.excerpt.isEmpty {
                                    Text(verbatim: choice.unit.excerpt).font(.callout).lineLimit(3).foregroundStyle(.secondary)
                                }
                                if choice.unit.reusable { Text("复用已有结果").font(.caption).foregroundStyle(.secondary) }
                                if !choice.unit.textAvailable { Text("这段正文为空，无需发送。").font(.caption).foregroundStyle(.secondary) }
                                Button("核对正文与前后文") { reviewing = choice }.disabled(previewing)
                            }.padding(.vertical, 6)
                        }
                        if (visible[scope.identity] ?? 20) < scope.units.count {
                            Button("显示更多片段") { visible[scope.identity, default: 20] += 20 }.disabled(previewing)
                        }
                    }
                }
                if !failures.isEmpty {
                    Section {
                        ForEach(Array(failures.enumerated()), id: \.offset) { _, value in Text(verbatim: value).foregroundStyle(.secondary) }
                        Text("部分资料未就绪，请返回选择检查。原文变化时需重新打开资料。")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                if let problem { Section { Text(L(problem)).foregroundStyle(.secondary) } }
                if !loading { Section { Button("重新读取范围") { Task { await load(initial: false) } }.disabled(previewing) } }
            }.formStyle(.grouped)
                .safeAreaInset(edge: .bottom) {
                    Button(previewing ? "正在读取" : "核对发送范围") { preview() }
                        .buttonStyle(FilledButtonStyle())
                        .disabled(loading || previewing || selected.isEmpty || !failures.isEmpty)
                        .padding(16).frame(maxWidth: .infinity, alignment: .trailing).background(.bar)
                }
                #if os(iOS)
                .navigationTitle(L("选择范围")).navigationBarTitleDisplayMode(.inline)
                #else
                .toolbar { ToolbarItem(placement: .principal) { Text("选择范围").font(.headline) } }
                #endif
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button { dismiss() } label: {
                            #if os(iOS)
                            Text("返回")
                            #else
                            Text("返回选择")
                            #endif
                        }.disabled(previewing).accessibilityLabel(L("返回选择"))
                    }
                }
                .task { await load(initial: true) }
        }
        .sheet(item: $reviewing) { choice in MemorySegmentTextSheet(store: store, choice: choice).appLanguage() }
        .sheet(isPresented: $showingPlan, onDismiss: { Task { await load(initial: false) } }) {
            if let plan { MemoryBatchPlanSheet(store: store, initial: plan).appLanguage() }
        }
        #if os(macOS)
        .frame(minWidth: 580, idealWidth: 720, minHeight: 620)
        #endif
    }
    private func toggle(_ choice: MemorySegmentChoice) {
        if selection.contains(choice.id) { selection.remove(choice.id) }
        else if selection.count < 20 { selection.insert(choice.id) }
    }
    @MainActor private func load(initial: Bool) async {
        guard let client = store.client else { problem = "请先连接资料库。"; loading = false; return }
        loading = true; loaded = 0; failures = []; problem = nil
        var result: [MemorySourceScope] = []
        for (index, source) in sources.enumerated() {
            if Task.isCancelled { return }
            do {
                var query: [String: String] = [:]
                if let version = source.contentVersion { query["version"] = version }
                let value = try await client.read("v1/memory-segments/" + source.kind + "/" + source.id, query: query, as: MemorySourceScope.self)
                if Task.isCancelled { return }
                result.append(value)
            } catch {
                if Task.isCancelled { return }
                failures.append(LF("第 %d 份资料：%@", index + 1, error.localizedDescription))
            }
            loaded = index + 1
        }
        scopes = result; loading = false
        let available = choices.filter(\.unit.textAvailable)
        if initial {
            let ranges = preferred.filter { $0.segment != nil }
            if !ranges.isEmpty {
                let keys = Set(ranges.map { $0.identity + "/" + ($0.contentVersion ?? "") })
                let whole = Set(preferred.filter { $0.segment == nil }.map { $0.kind + "/" + $0.id })
                selection = Set(available.filter { keys.contains($0.id) || whole.contains($0.source.identity) }.prefix(20).map(\.id))
                if selection.isEmpty { problem = "原片段版本已变化，请重新核对并选择范围。" }
            } else if available.count <= 20 { selection = Set(available.map(\.id)) }
        } else {
            let valid = Set(available.map(\.id)); let before = selection
            selection.formIntersection(valid)
            if before != selection { problem = "资料版本已变化，过期选择已清除，请重新核对。" }
        }
    }
    private func preview() {
        guard let client = store.client else { return }
        previewing = true; problem = nil
        struct Body: Encodable { let sources: [BatchReference] }
        let body = Body(sources: selected.map(\.reference))
        Task {
            defer { previewing = false }
            do {
                let data = try await client.sendData("v1/memory-batches/preview", method: "POST", body: JSONEncoder().encode(body))
                plan = try JSONDecoder().decode(BatchPlan.self, from: data); showingPlan = true
            } catch { problem = error.localizedDescription }
        }
    }
}

private struct MemorySegmentText: Decodable, Sendable {
    let title: String
    let prefix: String
    let text: String
    let suffix: String
    let segment: MemorySourceSegment?
}
private struct MemorySegmentTextSheet: View {
    @ObservedObject var store: NodeWorkspaceStore
    let choice: MemorySegmentChoice
    @Environment(\.dismiss) private var dismiss
    @State private var value: MemorySegmentText?
    @State private var problem: String?
    @State private var pages: [String] = []
    @State private var visiblePages = 1
    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        if let value {
                            Text(verbatim: value.title).font(.headline)
                            if let segment = value.segment { Text(segment.rangeTitle).font(.callout).foregroundStyle(.secondary) }
                            Text("请求模型时会一同发送前后文；复用已有结果不会再次发送。仅从正文范围提出记忆。")
                                .font(.footnote).foregroundStyle(.secondary)
                            if !value.prefix.isEmpty {
                                DisclosureGroup("前文（用于核对）") {
                                    Text(verbatim: value.prefix).font(.body).textSelection(.enabled)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                }
                            }
                            Text("本段正文").font(.headline)
                            ForEach(Array(pages.prefix(visiblePages).enumerated()), id: \.offset) { index, text in
                                Text(verbatim: text).font(.body).textSelection(.enabled)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .frame(maxWidth: .infinity, alignment: .leading).id(index)
                            }
                            Text(LF("已显示正文第 %d / %d 页", min(visiblePages, pages.count), pages.count))
                                .font(.footnote).foregroundStyle(.secondary)
                            if visiblePages < pages.count { Button("继续阅读本段") { visiblePages += 1 } }
                            else { Text("本段正文已完整显示。").font(.footnote).foregroundStyle(.secondary) }
                            if !value.suffix.isEmpty {
                                DisclosureGroup("后文（用于核对）") {
                                    Text(verbatim: value.suffix).font(.body).textSelection(.enabled)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                }
                            }
                        } else if problem == nil { ProgressView("正在读取") }
                        if let problem { Text(L(problem)).foregroundStyle(.secondary) }
                    }.padding(24).frame(maxWidth: .infinity, alignment: .leading)
                }
                .onChange(of: visiblePages) { _, count in proxy.scrollTo(count - 1, anchor: .top) }
            }
            #if os(iOS)
            .navigationTitle(L("核对片段")).navigationBarTitleDisplayMode(.inline)
            #else
            .toolbar { ToolbarItem(placement: .principal) { Text("核对片段").font(.headline) } }
            #endif
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } } }
            .task {
                guard let client = store.client else { problem = "请先连接资料库。"; return }
                do {
                    var reference = choice.reference; reference.contentVersion = choice.source.contentVersion
                    let data = try await client.sendData("v1/memory-segments/" + reference.kind + "/" + reference.id, method: "POST", body: JSONEncoder().encode(reference))
                    let result = try JSONDecoder().decode(MemorySegmentText.self, from: data)
                    value = result; pages = readingPages(result.text)
                } catch { problem = error.localizedDescription }
            }
        }
        #if os(macOS)
        .frame(minWidth: 540, idealWidth: 700, minHeight: 580)
        #endif
    }
    private func readingPages(_ text: String) -> [String] {
        var result: [String] = []; var start = text.startIndex
        while start < text.endIndex {
            let end = text.index(start, offsetBy: 2000, limitedBy: text.endIndex) ?? text.endIndex
            result.append(String(text[start..<end])); start = end
        }
        return result
    }
}
