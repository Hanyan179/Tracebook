import SwiftUI

struct TranscriptionState: Decodable, Sendable {
    let status: String
    let configured: Bool
    let completed: Int
    let total: Int
    let problem: String?
    let generatedPath: String?
    var serviceChanged: Bool?
    var nextRetryAt: Double?
    var active: Bool { ["queued", "running", "retry"].contains(status) }
    var title: String {
        if serviceChanged == true && active { return "等待原语音服务" }
        if !configured && active { return "等待语音服务配置" }
        switch status {
        case "queued": return "等待转写"
        case "running": return "正在转写"
        case "retry": return "等待重试"
        case "paused": return "转写已暂停"
        case "failed": return "转写未完成"
        case "ready": return "转写完成，待校对"
        case "conflict": return "转写已另存"
        default: return "尚未转写"
        }
    }
}

struct TranscriptionView: View {
    @ObservedObject var store: NodeWorkspaceStore
    let recordingID: UUID
    @State private var state: TranscriptionState?
    @State private var problem: String?
    @State private var busy = false
    @State private var confirmSend = false
    @State private var generated: NodeDocument?
    @State private var showGenerated = false
    #if os(macOS)
    @Environment(\.openSettings) private var openSettings
    #endif
    private var path: String { "v1/transcriptions/" + recordingID.uuidString }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(L(state?.title ?? "读取转写状态"), systemImage: "text.bubble")
                .font(.subheadline.weight(.medium))
            if let state {
                if state.total > 0 {
                    Text(LF("已完成 %d / %d 段", state.completed, state.total))
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let next = state.nextRetryAt {
                    Text(L("下次重试") + " " + Date(timeIntervalSince1970: next).formatted(date: .omitted, time: .standard))
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let message = state.problem { Text(L(message)).font(.caption).foregroundStyle(.secondary) }
                if state.serviceChanged == true && state.completed > 0 {
                    Text("服务或模型已更换，请恢复原配置后继续，避免混合转写结果。")
                        .font(.caption).foregroundStyle(.secondary)
                } else if state.serviceChanged == true {
                    Text("服务已更换，继续将使用当前配置。").font(.caption).foregroundStyle(.secondary)
                }
                if !state.configured && !["ready", "conflict"].contains(state.status) {
                    Text("请先在电脑设置中配置语音服务和密钥。原音继续保留。")
                        .font(.caption).foregroundStyle(.secondary)
                    #if os(macOS)
                    Button("设置语音服务") {
                        UserDefaults.standard.set("模型", forKey: "settings.section")
                        UserDefaults.standard.set("transcription", forKey: "model.focusRole")
                        openSettings()
                    }
                    #endif
                }
                if state.status == "ready" {
                    Text("机器转写可能有误，请结合原音校对；不会自动成为已确认记忆。")
                        .font(.caption).foregroundStyle(.secondary)
                } else if state.status == "conflict" {
                    Text("原有文字已保留，请查看另存的转写版本后自行合并。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if state.active && state.serviceChanged != true {
                    Button("暂停转写") { Task { await action("/pause") } }.disabled(busy)
                } else if state.configured && !["ready", "conflict"].contains(state.status) {
                    Button(L(state.status == "idle" ? "转写这条录音" : "继续转写")) { confirmSend = true }
                        .disabled(busy || !state.configured || (state.serviceChanged == true && state.completed > 0))
                }
                if state.generatedPath != nil {
                    Button("查看另存转写") {
                        Task {
                            do {
                                generated = try await store.client?.read(path + "/generated", as: NodeDocument.self)
                                showGenerated = generated != nil
                            } catch { problem = error.localizedDescription }
                        }
                    }
                }
            }
            if let problem {
                Text(L(problem)).font(.caption).foregroundStyle(.secondary)
                Button("重新读取") { Task { await reload() } }
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
            .confirmationDialog("发送这条原音进行转写？", isPresented: $confirmSend, titleVisibility: .visible) {
                Button("发送并转写") { Task { await action("") } }
            } message: {
                Text("将发送到电脑配置的语音服务，可能产生服务费用。网络失败最多尝试三次，已完成片段不重复发送。")
            }
            .sheet(isPresented: $showGenerated) {
                NavigationStack {
                    ScrollView { Text(verbatim: generated?.text ?? "").textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(24) }
                        .navigationTitle(L("另存的转写版本"))
                        #if os(iOS)
                        .navigationBarTitleDisplayMode(.inline)
                        #endif
                        .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { showGenerated = false } } }
                }
                #if os(macOS)
                .frame(minWidth: 540, minHeight: 400)
                #endif
                .appLanguage()
            }
            .task(id: recordingID) {
                while !Task.isCancelled {
                    await reload()
                    do { try await Task.sleep(for: .seconds(3)) } catch { return }
                }
            }
    }

    private func reload() async {
        guard let client = store.client else { return }
        do {
            let next = try await client.read(path, as: TranscriptionState.self)
            let changed = state?.status != next.status
            let hadState = state != nil
            state = next; problem = nil
            if changed && ["ready", "conflict"].contains(next.status) {
                if hadState { await store.libraryChanged() }
                else { await store.refresh() }
            }
        } catch { if !Task.isCancelled { problem = error.localizedDescription } }
    }

    private func action(_ suffix: String) async {
        guard let client = store.client else { return }
        busy = true; defer { busy = false }
        do {
            _ = try await client.sendData(path + suffix, method: "POST", body: Data("{}".utf8))
            await reload()
        } catch { problem = error.localizedDescription }
    }
}
