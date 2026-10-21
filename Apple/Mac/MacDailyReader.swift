import SwiftUI

private struct AnalysisProgress: Decodable, Sendable {
    let day: String
    let status: String
    let configured: Bool
    var problem: String?
    var hasDocument: Bool?
    var updated_at: String?
    var coverage: Coverage?
    var currentCoverage: Coverage?
    var freshness: String?
    var generated_path: String?
    var generatedFreshness: String?
    struct Coverage: Decodable, Sendable {
        let sourceCount: Int
        let includedSources: Int
        let missingText: Int
        let truncatedSources: Int
    }
}

struct MacDailyReader: View {
    @ObservedObject var store: NodeWorkspaceStore
    let day: NodeDaily
    @State private var mode = "sources"
    @State private var status: AnalysisProgress?
    @State private var refreshID = UUID()
    @State private var requesting = false
    @State private var problem: String?
    @State private var reviewingVersion = false
    @Environment(\.openSettings) private var openSettings
    var body: some View {
        VStack(spacing: 0) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 16) { modePicker; Spacer(minLength: 8); generateAction }
                VStack(alignment: .leading, spacing: 12) {
                    modePicker
                    HStack { Spacer(); generateAction }
                }
            }.padding(.horizontal, 24).padding(.vertical, 14)
            Divider()
            if let problem = problem ?? status?.problem {
                HStack {
                    Text(L(problem)).font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("重新读取") { refreshID = UUID() }.font(.caption)
                }.padding(16)
            }
            if mode == "sources" {
                MacDocumentReader(store: store, path: "v1/daily/\(day.id)", title: day.id,
                    subtitle: LF("%d 条来源", day.sourceCount), day: day)
            } else {
                VStack(spacing: 0) {
                    if let coverage = status?.currentCoverage {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(LF("当前可分析 %d/%d 条来源", coverage.includedSources, coverage.sourceCount))
                            if coverage.missingText > 0 || coverage.truncatedSources > 0 {
                                Text(LF("%d 条还没有文字，%d 条内容无法完整提供。", coverage.missingText, coverage.truncatedSources))
                            }
                        }.font(.caption).foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 24).padding(.vertical, 12)
                    }
                    if status?.hasDocument == true && status?.freshness != "current" {
                        Label(L(status?.freshness == "stale"
                                ? "来源或规则已变化。当前显示旧分析，请重新生成后核对。"
                                : "这份分析的来源版本未记录，请重新生成后核对。"), systemImage: "exclamationmark.circle")
                            .font(.callout).frame(maxWidth: .infinity, alignment: .leading).padding(16)
                    }
                    if status?.status == "conflict" {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("原分析含有人工修改，已保留。新版本已另存，可对照后编辑。")
                                .fixedSize(horizontal: false, vertical: true)
                            Button("查看另存版本") { reviewingVersion = true }
                        }.font(.caption).foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading).padding(16)
                    }
                    if status?.hasDocument == true {
                        MacDocumentReader(store: store, path: "v1/analysis/\(day.id)/document", title: day.id, subtitle: L("AI 每日分析"))
                            .id(status?.updated_at ?? refreshID.uuidString)
                    } else {
                        ContentUnavailableView {
                            Label(status?.status == "running" ? L("正在分析") : L("把一天整理清楚"), systemImage: "sparkles")
                        } description: {
                            Text(status?.currentCoverage?.includedSources == 0
                                 ? L("当天还没有可分析的文字，原音继续保留。")
                                 : store.status?.analysisConfigured == true ? L("生成时，将当天已有文字发送给你配置的模型。结果保留来源，长期记忆建议仍需确认。") : L("先在设置中填写文字模型和密钥，再生成每日分析。"))
                        } actions: {
                            if store.status?.analysisConfigured != true {
                                Button("配置文字模型") { UserDefaults.standard.set("模型", forKey: "settings.section"); openSettings() }
                            }
                        }
                    }
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }.task(id: refreshID.uuidString + store.libraryRevision.uuidString + String(store.connected)) {
            repeat {
                await load()
                do { try await Task.sleep(for: .seconds(status?.status == "running" ? 3 : 10)) } catch { break }
            } while !Task.isCancelled
        }
        .sheet(isPresented: $reviewingVersion) {
            VStack(spacing: 0) {
                HStack {
                    Text(status?.generatedFreshness == "stale" ? L("来源又有变化，此另存版本也需要重新核对。") : L("这是另存的新版本，已有分析和人工修改仍然保留。"))
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("完成") { reviewingVersion = false }
                }.padding(20)
                MacDocumentReader(store: store, path: "v1/analysis/\(day.id)/generated", title: day.id,
                                  subtitle: L("另存的分析版本"), editable: false)
            }.frame(minWidth: 660, idealWidth: 860, minHeight: 580, idealHeight: 760).appLanguage()
        }
    }
    private var modePicker: some View {
        Picker("每日内容", selection: $mode) {
            Text("来源记录").tag("sources"); Text("AI 每日分析").tag("analysis")
        }.labelsHidden().pickerStyle(.segmented).frame(width: 260)
    }
    @ViewBuilder private var generateAction: some View {
        if status?.status == "running" || requesting {
            HStack { ProgressView().controlSize(.small); Text("正在分析").font(.caption).foregroundStyle(.secondary) }
        } else {
            Button(L(status?.status == "failed" ? "重试分析" : status?.hasDocument == true ? "重新生成" : "生成分析"), systemImage: "sparkles") { Task { await generate() } }
                .disabled(store.status?.analysisConfigured != true || status?.currentCoverage?.includedSources == 0)
                .fixedSize()
        }
    }
    private func load() async {
        do {
            guard let client = store.client else { throw NodeFailure(message: "请先连接资料库。") }
            status = try await client.read("v1/analysis/\(day.id)", as: AnalysisProgress.self); problem = nil
        }
        catch { problem = error.localizedDescription }
    }
    private func generate() async {
        guard let client = store.client else { return }
        mode = "analysis"; requesting = true; problem = nil
        defer { requesting = false }
        do {
            let data = try await client.sendData("v1/analysis/\(day.id)", method: "POST", body: Data(#"{"force":true}"#.utf8))
            status = try JSONDecoder().decode(AnalysisProgress.self, from: data); refreshID = UUID()
        } catch { problem = error.localizedDescription }
    }
}

struct AutomaticAnalysisSection: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @State private var state: ModelRuntimeState?
    @State private var busy = false
    @State private var problem: String?
    var body: some View {
        Section("AI 每日分析") {
            Toggle("自动生成每日分析", isOn: Binding(get: { state?.automatic ?? false }, set: { value in Task { await save(value) } }))
                .disabled(busy || state == nil || (state?.configured != true && state?.automatic != true))
            if let state, state.configured { LabeledContent("文字模型", value: state.model) }
            Text("启用后，在设定时间将最近 7 天新增或变化的文字发送到所选模型。资料不足会注明，不自动确认长期记忆。")
                .font(.caption).foregroundStyle(.secondary)
            if state?.configured != true { Text("请先配置文字模型和密钥。").font(.caption).foregroundStyle(.secondary) }
            if let problem { Text(L(problem)).font(.caption).foregroundStyle(.secondary) }
        }.task {
            do { state = try await workspace.client?.read("v1/analysis-settings", as: ModelRuntimeState.self) }
            catch { problem = error.localizedDescription }
        }
    }
    private func save(_ value: Bool) async {
        guard let client = workspace.client else { return }
        busy = true; defer { busy = false }
        do {
            let data = try await client.sendData("v1/analysis-settings", method: "PUT", body: JSONEncoder().encode(["automatic": value]))
            state = try JSONDecoder().decode(ModelRuntimeState.self, from: data); problem = nil
        } catch { problem = error.localizedDescription }
    }
}
