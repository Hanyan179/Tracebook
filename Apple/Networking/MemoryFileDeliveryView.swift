import SwiftUI

struct MemoryFileDelivery: Codable, Sendable, Equatable {
    let state: String
    let lastSuccessAt: String?
    let lastAttemptAt: String?
    let problem: String?
    let conflicts: [NodeConflict]
    var reviewedFiles: [String]?
    var paused: Bool?
    var isPaused: Bool { paused == true }
    var title: String {
        if isPaused { return "记忆文件更新已暂停" }
        return switch state {
        case "failed": "记忆文件写入失败"
        case "pending": "记忆文件待写入"
        case "review": "记忆文件需核对"
        case "reviewed": "已保留核对后的文件"
        default: "记忆文件已更新"
        }
    }
    var symbol: String {
        if isPaused { return "pause.circle" }
        return switch state {
        case "ready", "reviewed": "checkmark.circle"
        case "pending": "clock"
        default: "exclamationmark.triangle"
        }
    }
}

struct MemoryFileDeliveryButton: View {
    @ObservedObject var store: NodeWorkspaceStore
    @State private var showing = false
    var body: some View {
        if let delivery = store.status?.memoryDelivery {
            Button { showing = true } label: {
                Label(L(delivery.title), systemImage: delivery.symbol)
                    .font(.caption).multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.vertical, 6)
                    #if os(iOS)
                    .frame(minHeight: 44)
                    #endif
            }.buttonStyle(.plain)
                .foregroundStyle(delivery.state == "ready" ? Color.secondary : Color.primary)
                .sheet(isPresented: $showing) { MemoryFileDeliveryView(store: store).appLanguage() }
        }
    }
}

private struct MemoryFileDeliveryView: View {
    @ObservedObject var store: NodeWorkspaceStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var delivery: MemoryFileDelivery?
    @State private var busy = false
    @State private var problem: String?
    @State private var reviewing: NodeConflict?
    private var value: MemoryFileDelivery? { delivery ?? store.status?.memoryDelivery }
    var body: some View {
        NavigationStack {
            Form {
                if let value {
                    Section {
                        (typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8)) : AnyLayout(HStackLayout(alignment: .firstTextBaseline))) {
                            Image(systemName: value.symbol).accessibilityHidden(true)
                            Text(L(value.title)).frame(maxWidth: .infinity, alignment: .leading)
                                .fixedSize(horizontal: false, vertical: true)
                        }.font(.headline)
                        if value.isPaused {
                            Text("当前资料库的记忆文件更新已暂停，原记忆文件保留。新修订仍保存在资料库；核对内容后，可恢复写入资料夹。")
                        }
                        if let issue = value.problem { Text(L(issue)) }
                        else if value.isPaused { EmptyView() }
                        else if value.state == "pending" {
                            Text("电脑运行时会自动补写已保存的记忆。也可以立即重试。")
                        } else if value.state == "review" {
                            Text("人工修改的文件已保留，新内容已另存。打开下方文件核对两个版本，重试不会覆盖人工修改。")
                        } else if value.state == "reviewed" {
                            Text("已保留你选定的文件内容。资料库中的记忆条目未改变；有新的记忆变化时仍会另存并提示核对。")
                        } else {
                            Text("当前记忆文字和修订历史已写入电脑资料夹。此状态不代表云端同步完成。")
                        }
                        if let raw = value.lastAttemptAt, let date = ISO8601DateFormatter().date(from: raw) {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("最近检查").font(.caption).foregroundStyle(.secondary)
                                Text(date, format: .dateTime.year().month().day().hour().minute().second())
                            }
                        }
                        if let raw = value.lastSuccessAt, let date = ISO8601DateFormatter().date(from: raw) {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("最近完整写入").font(.caption).foregroundStyle(.secondary)
                                Text(date, format: .dateTime.year().month().day().hour().minute().second())
                            }
                        }
                    }.id(value.state)
                    Section("电脑资料夹") {
                        Text(verbatim: store.status?.outputDirectory ?? "").font(.callout).textSelection(.enabled)
                    }
                    if !value.conflicts.isEmpty {
                        Section("需要核对的文件") {
                            ForEach(value.conflicts) { file in
                                VStack(alignment: .leading, spacing: 8) {
                                    Text(verbatim: file.path).font(.body.weight(.medium))
                                    if let generated = file.generatedPath {
                                        Text("新内容另存于").font(.caption).foregroundStyle(.secondary)
                                        Text(verbatim: generated).font(.callout)
                                    }
                                }.textSelection(.enabled)
                                Button("核对两个版本") { reviewing = file }.disabled(busy || value.isPaused)
                            }
                            if value.isPaused { Text("恢复文件更新后，可核对人工修改与新生成的版本。") }
                        }
                    }
                    if let files = value.reviewedFiles, !files.isEmpty {
                        Section("已核对的文件") {
                            ForEach(files, id: \.self) { path in
                                VStack(alignment: .leading, spacing: 8) {
                                    Text(verbatim: path).font(.body.weight(.medium))
                                    Button("查看文件核对记录") { reviewing = NodeConflict(path: path, state: "reviewed", generatedPath: nil) }
                                        .disabled(busy)
                                }
                            }
                        }
                    }
                }
                Section {
                    Button(value?.isPaused == true ? "恢复记忆文件更新" : "重新检查并写入") { Task { await run(retry: true) } }.disabled(busy)
                    Text(value?.isPaused == true
                         ? "恢复后补写已保存的记忆和历史；人工修改会保留并另存新版本。接收与模型自动处理保持原设置。"
                         : "只补写已保存的文字和历史，不重新整理或确认记忆。")
                        .font(.footnote).foregroundStyle(.secondary)
                    if busy { ProgressView("正在处理") }
                    if let problem { Text(L(problem)).foregroundStyle(.secondary) }
                }
            }.formStyle(.grouped)
                #if os(iOS)
                .navigationTitle(L("文件状态"))
                .navigationBarTitleDisplayMode(.inline)
                #else
                .toolbar { ToolbarItem(placement: .principal) { Text("记忆文件状态").font(.headline) } }
                #endif
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } } }
        }.task { await run(retry: false) }
            .sheet(item: $reviewing) { file in MemoryFileReviewSheet(store: store, path: file.path).appLanguage() }
            .onChange(of: store.status?.memoryDelivery) { _, value in delivery = value }
            #if os(macOS)
            .frame(minWidth: 520, idealWidth: 600, minHeight: 500)
            #endif
    }
    @MainActor private func run(retry: Bool) async {
        guard let client = store.client else { problem = "请先连接资料库。"; return }
        busy = true; problem = nil
        defer { busy = false }
        do {
            if retry {
                let data = try await client.send("v1/memory-delivery/retry", method: "POST", body: [:])
                delivery = try JSONDecoder().decode(MemoryFileDelivery.self, from: data)
                await store.refresh()
            } else {
                delivery = try await client.read("v1/memory-delivery", as: MemoryFileDelivery.self)
            }
        } catch { problem = error.localizedDescription }
    }
}
