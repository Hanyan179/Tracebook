import SwiftUI

/// Reads committed scan diagnostics, never the source file or its raw malformed records.
struct LocalSourceIssuesView: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    let sourceID: String
    let filePath: String
    @Environment(\.dismiss) private var dismiss
    @State private var page: Page?
    @State private var items: [Issue] = []
    @State private var loading = false
    @State private var problem: String?

    private struct Issue: Decodable, Identifiable, Sendable {
        let offset: Int64
        let line: Int64?
        let reason: String
        var id: Int64 { offset }
        var title: String {
            switch reason {
            case "recordTooLarge": "整条记录超过读取上限"
            case "inputTooLarge": "本人输入超过长度上限"
            case "invalidJSON": "记录格式损坏或编码无效"
            case "invalidDate": "本人输入缺少有效时间"
            case "invalidReply": "结构化回答无法解析"
            default: "记录无法识别"
            }
        }
    }
    private struct Page: Decodable, Sendable {
        let items: [Issue]
        let total: Int
        let unlocatedCount: Int
        let stateVersion: String
        let compressed: Bool
        let complete: Bool
        let nextAfter: Int64?
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text(verbatim: filePath).font(.callout.monospaced()).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("位置来自已保存的扫描结果；源文件变化后请返回扫描详情重新检查。这里只保留原因和位置，不保存异常记录正文。")
                        .font(.callout).foregroundStyle(.secondary)
                    if let page {
                        if page.total > 0 || page.unlocatedCount == 0 {
                            Text(LF("已显示 %d / %d 条跳过位置", items.count, page.total)).font(.callout)
                        }
                        if page.compressed { Text("压缩会话的行号和字节位置按解压后的内容计算。").font(.caption).foregroundStyle(.secondary) }
                        if !page.complete { Text("文件仍未读完；这里只显示已扫描部分的跳过位置。").foregroundStyle(.secondary) }
                        if page.unlocatedCount > 0 {
                            Text(LF("有 %d 条旧记录未保存位置。返回详情重新检查此文件可补充，原文件保持只读。", page.unlocatedCount))
                                .foregroundStyle(.secondary)
                        }
                        if page.total == 0 && page.unlocatedCount == 0 {
                            Text(page.complete ? "当前扫描没有跳过记录。" : "已读取部分尚未发现跳过记录。")
                        }
                    }
                }
                ForEach(items) { issue in
                    Section {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(L(issue.title)).font(.headline)
                            if let line = issue.line { Text(LF("第 %lld 行", line)).monospacedDigit() }
                            else { Text("旧检查点未保存行号，请按字节位置核对。").foregroundStyle(.secondary) }
                            Text(LF("字节位置 %lld（从 0 开始）", issue.offset)).font(.caption).monospacedDigit()
                        }.textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    }
                }
                if loading { Section { ProgressView("正在读取") } }
                if page?.nextAfter != nil {
                    Section { Button("加载更多") { Task { await load(reset: false) } }.disabled(loading || problem != nil) }
                }
            }.formStyle(.grouped)
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    if let problem {
                        VStack(alignment: .leading, spacing: 0) {
                            Divider()
                            Text(L(problem)).font(.callout)
                                .fixedSize(horizontal: false, vertical: true)
                                .padding()
                        }.frame(maxWidth: .infinity, alignment: .leading)
                            .background(.background)
                    }
                }
                .navigationTitle(L("跳过记录的位置"))
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("关闭") { dismiss() } }
                    ToolbarItem(placement: .confirmationAction) { Button("刷新") { Task { await load(reset: true) } }.disabled(loading) }
                }
        }.frame(minWidth: 520, idealWidth: 600, minHeight: 500)
            .task { await load(reset: true) }
    }

    private func load(reset: Bool) async {
        guard !loading else { return }
        guard let client = workspace.client else { problem = "请先连接资料库。"; return }
        loading = true; problem = nil
        if reset { items = []; page = nil }
        defer { loading = false }
        do {
            var query = ["path": filePath, "limit": "40"]
            if !reset, let page, let next = page.nextAfter {
                query["stateVersion"] = page.stateVersion; query["after"] = String(next)
            }
            let response = try await client.read("v1/local-sources/" + sourceID + "/file-issues", query: query, as: Page.self)
            guard !Task.isCancelled else { return }
            items += response.items.filter { new in !items.contains { $0.id == new.id } }
            page = response
        } catch {
            guard !Task.isCancelled else { return }
            problem = error.localizedDescription
        }
    }
}
