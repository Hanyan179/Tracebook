import SwiftUI

private struct MemoryExtractionState: Decodable, Sendable {
    let status: String
    let configured: Bool
    let model: String
    let endpoint: String
    let characters: Int
    let limit: Int
    let sourceChanged: Bool
    let canResume: Bool
    let candidateIDs: [UUID]
    let problem: String?
}

/// Generating is an explicit action on one source; closing the sheet leaves its task intact.
struct MemoryExtractionSection: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let source: KnowledgeSource
    var saved: () -> Void
    @State private var state: MemoryExtractionState?
    @State private var candidates: [NodeCandidate] = []
    @State private var selected: NodeCandidate?
    @State private var requesting = false
    @State private var problem: String?
    @State private var refreshID = UUID()
    @State private var choosingSegments = false
    #if os(macOS)
    @Environment(\.openSettings) private var openSettings
    #endif
    private var path: String { "v1/memory-extractions/" + source.identity }
    var body: some View {
        Group {
            Section("所选资料") {
                Label(source.title, systemImage: source.symbol)
                if let state {
                    Text(LF(state.characters > state.limit ? "资料完整文字共 %d 个字符；发送范围将在选择后确认。" : "本次包含这份资料的完整文字，共 %d 个字符。", state.characters))
                        .font(.callout).foregroundStyle(.secondary)
                    if state.sourceChanged {
                        Text("原文已变化，请重新打开资料后生成。").foregroundStyle(.secondary)
                    } else if state.characters > state.limit {
                        Text("这份资料较长，可以选择片段后分批整理。").foregroundStyle(.secondary)
                        Button("选择片段并整理") { choosingSegments = true }.disabled(requesting)
                    }
                }
            }
            Section("模型整理") {
                if let state {
                    if state.status == "running" {
                        HStack(spacing: 12) {
                            ProgressView().controlSize(.small)
                            Text("正在整理这份资料…")
                        }
                        Text("可以关闭窗口，稍后从这份资料查看结果。")
                            .font(.footnote).foregroundStyle(.secondary)
                    } else if state.status == "ready" {
                        Label(candidates.isEmpty ? L("没有提出适合长期保留的记忆。") : L("整理完成，请核对下方结果。"),
                              systemImage: candidates.isEmpty ? "text.magnifyingglass" : "checkmark.circle")
                        Text("重复打开会显示已有结果，不会重复生成。")
                            .font(.footnote).foregroundStyle(.secondary)
                    } else {
                        if state.canResume {
                            Text("建议已保存到资料库，文字文件尚待恢复。重试不会再次请求模型。")
                                .font(.callout).foregroundStyle(.secondary)
                            Button("重试保存") { start() }.disabled(requesting)
                        } else if state.characters > state.limit {
                            Text("长资料按片段整理，请先在上方选择范围。").font(.callout).foregroundStyle(.secondary)
                        } else if state.configured {
                            Text(verbatim: state.model).font(.headline)
                            Text(verbatim: state.endpoint).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                            Text("点击后将这份资料的文字发送到上方服务。其他资料不会一同发送；建议需核对后确认。")
                                .font(.callout).foregroundStyle(.secondary)
                            Button(state.status == "idle" ? "发送这份资料并生成" : "重试整理") { start() }
                                .buttonStyle(FilledButtonStyle())
                                .disabled(requesting || state.sourceChanged || state.characters > state.limit || state.characters == 0)
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
                    if let failure = state.problem { Text(L(failure)).foregroundStyle(.secondary) }
                } else if problem == nil {
                    ProgressView("正在读取")
                }
                if let problem { Text(L(problem)).foregroundStyle(.secondary) }
                if state?.status != "running" {
                    Button("重新读取") { refreshID = UUID() }.disabled(requesting)
                }
            }
            if !candidates.isEmpty {
                Section("整理结果") {
                    ForEach(candidates) { candidate in
                        Button { selected = candidate } label: {
                            VStack(alignment: .leading, spacing: 8) {
                                Text(verbatim: candidate.text).foregroundStyle(.primary)
                                    .fixedSize(horizontal: false, vertical: true)
                                Label(L(candidate.stateTitle), systemImage: "chevron.right").font(.caption).foregroundStyle(.secondary)
                            }.frame(maxWidth: .infinity, minHeight: 44, alignment: .leading).padding(.vertical, 6)
                        }.buttonStyle(.plain)
                    }
                }
            }
            Text("模型可能误判说话人与内容。请核对原文；引用、他人发言和推测不能直接成为本人的事实。")
                .font(.footnote).foregroundStyle(.secondary)
        }
            .task(id: refreshID) {
                repeat {
                    await load()
                    guard state?.status == "running" else { break }
                    do { try await Task.sleep(for: .seconds(2)) } catch { break }
                } while !Task.isCancelled
            }
            .sheet(isPresented: $choosingSegments, onDismiss: { refreshID = UUID() }) {
                MemorySegmentSelectionView(store: workspace, sources: [.init(kind: source.kind, id: source.id, contentVersion: source.contentVersion)]).appLanguage()
            }
            .sheet(item: $selected, onDismiss: { refreshID = UUID() }) { candidate in
                MemoryDetail(store: workspace, candidate: candidate).appLanguage()
            }
    }
    private func load() async {
        guard let client = workspace.client else { problem = "请先连接资料库。"; return }
        do {
            let value = try await client.read(path, query: ["version": source.contentVersion], as: MemoryExtractionState.self)
            var result: [NodeCandidate] = []
            for id in value.candidateIDs {
                result.append(try await client.read("v1/candidates/" + id.uuidString, as: NodeCandidate.self))
            }
            let becameReady = state?.status != "ready" && value.status == "ready"
            state = value; candidates = result; problem = nil
            if becameReady {
                await workspace.libraryChanged()
                if result.contains(where: { $0.state == "pending" }) { saved() }
            }
        } catch { problem = error.localizedDescription }
    }
    private func start() {
        guard let client = workspace.client else { problem = "请先连接资料库。"; return }
        requesting = true; problem = nil
        struct Request: Encodable { let contentVersion: String; let sendToModel: Bool; let model: String; let endpoint: String }
        Task {
            defer { requesting = false }
            do {
                try await client.sendData(path, method: "POST", body: JSONEncoder().encode(Request(contentVersion: source.contentVersion, sendToModel: true, model: state?.model ?? "", endpoint: state?.endpoint ?? "")))
                await workspace.libraryChanged()
                refreshID = UUID()
            } catch { problem = error.localizedDescription }
        }
    }
}
