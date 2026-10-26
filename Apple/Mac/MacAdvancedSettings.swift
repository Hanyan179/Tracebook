import SwiftUI
import AppKit

struct MacAdvancedSettings: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @ObservedObject private var local = LocalNodeController.shared
    @ObservedObject private var sync = NodeSyncController.shared
    @AppStorage("node.useLocal") private var useLocal = true
    @State private var timeZone = TimeZone.current.identifier
    var body: some View {
        VStack(spacing: 0) {
            Form {
                Section("处理位置") {
                    Toggle("使用这台电脑作为处理节点", isOn: $useLocal)
                        .disabled(local.archiveFolderBusy)
                        .onChange(of: useLocal) { _, enabled in
                            if enabled { Task { await local.start(workspace: workspace) } }
                            else { local.stop(); workspace.disconnect() }
                        }
                    if useLocal {
                        LabeledContent("服务状态", value: L(local.starting ? "正在启动" : (local.running ? "运行中" : "已停止")))
                        HStack {
                            Button(L(local.running ? "重启节点" : "启动节点")) { Task { await local.restart(workspace: workspace) } }
                                .disabled(local.starting || local.archiveFolderBusy)
                            Button("选择归档目录") { Task { await local.chooseFolder(workspace: workspace) } }
                                .disabled(!workspace.connected || local.maintenance || local.archiveFolderBusy || workspace.hasUnfinishedEdits || workspace.status?.archiveCopySupported != true)
                            Button("打开归档目录") {
                                if let path = workspace.status?.outputDirectory { NSWorkspace.shared.open(URL(fileURLWithPath: path)) }
                            }.disabled(workspace.status == nil)
                        }
                        Toggle("允许同一局域网的手机连接", isOn: $local.allowLAN)
                            .onChange(of: local.allowLAN) { _, _ in Task { await local.restart(workspace: workspace) } }
                            .disabled(local.starting || local.archiveFolderBusy || (local.running && !local.managesProcess))
                        ArchiveFolderFeedback()
                        if local.allowLAN {
                            LabeledContent("手机连接地址", value: local.phoneEndpoint)
                            HStack {
                                Button("复制地址") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(local.phoneEndpoint, forType: .string) }
                                Button("复制连接口令") { local.copyToken() }
                            }
                            Text("手机需要填写连接口令。仅在可信局域网内使用此地址。").font(.caption).foregroundStyle(AppPalette.muted)
                        }
                        if let error = local.problem { Text(error).foregroundStyle(.orange) }
                        HStack {
                            TextField("归档时区", text: $timeZone)
                            Button("应用时区") {
                                Task {
                                    do {
                                        try await workspace.client?.send("v1/settings", body: ["timeZone": timeZone])
                                        await workspace.refresh()
                                    } catch { workspace.problem = error.localizedDescription }
                                }
                            }.disabled(!workspace.connected || timeZone == workspace.status?.timeZone)
                        }
                    }
                    if sync.pendingCount > 0 {
                        Button("重试待同步录音") { sync.retry() }
                        if let error = sync.problem { Text(error).font(.caption).foregroundStyle(.orange) }
                    }
                }
            }.formStyle(.grouped).frame(maxHeight: useLocal ? 370 : 100)
            NodeConnectionView(store: workspace, allowsConnectionEditing: !useLocal, showsLibrarySettings: false)
        }
        .onAppear { timeZone = workspace.status?.timeZone ?? TimeZone.current.identifier }
    }
}
