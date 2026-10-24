import AppKit
import Combine
import SwiftUI

@MainActor
final class LibraryLocationController: ObservableObject {
    static let shared = LibraryLocationController()
    @Published private(set) var busy = false
    @Published private(set) var cancellable = false
    @Published private(set) var phase = ""
    @Published private(set) var problem: String?
    @Published private(set) var result: String?
    @Published private(set) var feedbackContext = ""
    private let command = LocalNodeCommand()
    private let local = LocalNodeController.shared
    func cancel() {
        guard cancellable else { return }
        cancellable = false; command.cancel()
    }
    func inspect(_ url: URL) async -> LibraryLocationPreview? {
        guard !busy, !LibraryBackupController.shared.busy else { return nil }
        busy = true; problem = nil; result = nil; phase = "正在核对所选资料库"; command.reset()
        defer { busy = false; cancellable = false; feedbackContext = context }
        let access = url.startAccessingSecurityScopedResource()
        defer { if access { url.stopAccessingSecurityScopedResource() } }
        do {
            return try await command.run("library_location_cli", arguments: ["inspect", "--data", url.path],
                as: LibraryLocationPreview.self) { cancellable = $0 }
        } catch { problem = error.localizedDescription; return nil }
    }
    func open(_ preview: LibraryLocationPreview, url: URL, workspace: NodeWorkspaceStore) async -> Bool {
        guard !busy, !LibraryBackupController.shared.busy else { return false }
        busy = true; problem = nil; result = nil; phase = "正在切换并记住资料库"
        defer { busy = false; feedbackContext = context }
        do {
            try await local.openExistingLibrary(preview, selectedURL: url, workspace: workspace)
            result = "已记住这个资料库。重新打开应用会回到这里；接收与自动处理已暂停，核对后再继续。"
            return true
        } catch { problem = error.localizedDescription; return false }
    }
    func resumeReceiving() {
        do { try local.resumeReceiving(); result = "已恢复接收。模型任务仍需逐项核对后启用。" }
        catch { problem = error.localizedDescription }
        feedbackContext = context
    }
    var context: String { local.dataDirectory.path + "|" + String(local.receivingPaused) }
}

private struct SelectedLibrary: Identifiable {
    let url: URL
    let preview: LibraryLocationPreview
    var id: String { preview.id }
}

struct LibraryLocationSection: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @ObservedObject private var local = LocalNodeController.shared
    @ObservedObject private var controller = LibraryLocationController.shared
    @ObservedObject private var backup = LibraryBackupController.shared
    @State private var selected: SelectedLibrary?
    @State private var resumeConfirmation = false
    var body: some View {
        Section("启动资料库") {
            VStack(alignment: .leading, spacing: 10) {
                if local.selectionProblem == nil || local.hasRememberedLocation {
                    Text(verbatim: local.dataDirectory.path).font(.callout).textSelection(.enabled).id(local.dataDirectory)
                } else { Text("尚未连接资料库").foregroundStyle(.secondary) }
                Text(L(local.hasRememberedLocation ? "已记住位置，重新打开应用会回到这个资料库。" : "可以选择已有资料库，并记住下次打开的位置。"))
                    .font(.caption).foregroundStyle(.secondary)
                Button("选择启动资料库…") { choose() }
                    .disabled(controller.busy || backup.busy || local.starting || local.maintenance || workspace.hasUnfinishedEdits)
                Text("资料库包含原音、记忆和历史；文字归档目录另列在下方。切换不会合并或删除此前的资料。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if local.receivingPaused {
                VStack(alignment: .leading, spacing: 8) {
                    Label("接收已暂停", systemImage: "pause.circle").font(.headline)
                    Text("接收已暂停。请先核对资料；继续接收会使用此前选择的同步资料夹和本机保留的原音。")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("恢复接收…") { resumeConfirmation = true }.disabled(controller.busy || backup.busy || !local.running)
                }
            }
            if !local.running, let issue = local.selectionProblem ?? local.problem {
                Text(L(issue)).font(.callout).foregroundStyle(.secondary).textSelection(.enabled).id(issue)
                Text("可以重新连接存储位置、重新选择资料库，或使用下方备份还原。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            messages
        }
        .confirmationDialog("继续向当前资料库接收资料？", isPresented: $resumeConfirmation, titleVisibility: .visible) {
            Button("恢复接收") { controller.resumeReceiving() }
            Button("取消", role: .cancel) { }
        } message: { Text("将使用此前的同步资料夹和本机保留的原音。模型整理和转写不会因此自动启用。") }
        .sheet(item: $selected) { item in
            NavigationStack {
                Form {
                    Section("所选资料库") {
                        Text(verbatim: item.preview.dataDirectory).textSelection(.enabled)
                        count("原音", item.preview.recordings)
                        count("笔记", item.preview.notes)
                        count("导入资料", item.preview.documents)
                        count("记忆（含待确认与停用）", item.preview.memories)
                        count("待确认记忆", item.preview.pendingMemories)
                    }
                    Section("文字保存位置") {
                        Text(verbatim: item.preview.outputDirectory).textSelection(.enabled)
                        if !item.preview.outputAvailable {
                            Text("文字资料夹暂不可用，请连接存储位置后重试，或从备份还原。")
                        }
                    }
                    Section("打开后") {
                        Text("记住所选资料库及文字位置，下次启动继续使用。此前的资料库保留，不合并资料。")
                        Text("先暂停接收、自动转写、模型整理和来源扫描。核对后可分别继续，已有文字与人工修改保留。")
                    }
                }.formStyle(.grouped)
                    .safeAreaInset(edge: .top, spacing: 0) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("记住位置、保留原库；接收与自动处理先暂停。").font(.callout)
                            if workspace.hasUnfinishedEdits {
                                Text("还有未完成的编辑。请先保存或复制内容，并关闭编辑页面，再切换或备份资料库。").font(.callout)
                            }
                            if controller.busy || controller.problem != nil { messages }
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(16).background(AppPalette.surface)
                    }
                    .navigationTitle(L("核对启动资料库"))
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("返回") { selected = nil }.disabled(controller.busy)
                        }
                        ToolbarItem(placement: .confirmationAction) {
                            Button("打开并记住") {
                                Task { if await controller.open(item.preview, url: item.url, workspace: workspace) { selected = nil } }
                            }.disabled(controller.busy || backup.busy || !item.preview.outputAvailable || controller.problem != nil || workspace.hasUnfinishedEdits)
                        }
                    }
            }.frame(minWidth: 500, idealWidth: 580, minHeight: 560, idealHeight: 670)
                .interactiveDismissDisabled().appLanguage()
        }
    }
    private func count(_ label: String, _ value: Int) -> some View {
        LabeledContent(L(label), value: String(value)).accessibilityElement(children: .ignore)
            .accessibilityLabel(Text(L(label))).accessibilityValue(Text(verbatim: String(value)))
    }
    @ViewBuilder private var messages: some View {
        if controller.busy {
            HStack {
                ProgressView().controlSize(.small)
                Text(L(controller.phase)).font(.callout)
                Spacer(minLength: 8)
                if controller.cancellable { Button("停止") { controller.cancel() } }
            }
        }
        if controller.feedbackContext == controller.context {
            if let issue = controller.problem { Text(L(issue)).font(.callout).foregroundStyle(.secondary).textSelection(.enabled).id(issue) }
            if let result = controller.result { Text(L(result)).font(.callout).textSelection(.enabled).id(result) }
        }
    }
    private func choose() {
        let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false; panel.canCreateDirectories = false
        panel.directoryURL = local.dataDirectory
        panel.prompt = L("核对此资料库")
        panel.message = L("选择已有迹库资料库的文件夹。普通文档请使用资料导入，备份压缩包请使用备份还原。")
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task { if let preview = await controller.inspect(url) { selected = SelectedLibrary(url: url, preview: preview) } }
    }
}
