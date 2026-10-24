import AppKit
import Combine
import Foundation
import SwiftUI
import UniformTypeIdentifiers

struct LibraryBackupReceipt: Decodable, Identifiable, Sendable {
    let id: String
    let createdAt: String
    let archiveSHA256: String
    let files: Int
    let bytes: Int64
    let recordings: Int
    let documents: Int
    let notes: Int
    let memories: Int
    let originalDocuments: Int?
    let modifiedOriginalDocuments: Int?
    var date: String {
        ISO8601DateFormatter().date(from: createdAt)?.formatted(
            Date.FormatStyle(date: .abbreviated, time: .shortened).locale(AppLanguage.current.locale)) ?? createdAt
    }
}

@MainActor
final class LibraryBackupController: ObservableObject {
    static let shared = LibraryBackupController()
    @Published private(set) var busy = false
    @Published private(set) var cancellable = false
    @Published private(set) var phase = ""
    @Published private(set) var problem: String?
    @Published private(set) var result: String?
    @Published private(set) var savedFile: URL?
    @Published private(set) var feedbackLibrary = ""
    private let command = LocalNodeCommand()
    private let local = LocalNodeController.shared

    private func begin(_ phase: String) {
        busy = true; self.phase = phase; problem = nil; result = nil; savedFile = nil; command.reset()
    }
    private func end() { busy = false; cancellable = false; feedbackLibrary = local.dataDirectory.path }
    func cancel() {
        guard cancellable, command.isRunning else { return }
        cancellable = false; phase = "正在取消并保留原资料"; command.cancel()
    }
    private func execute(_ arguments: [String]) async throws -> LibraryBackupReceipt {
        try await command.run("backup_cli", arguments: arguments, as: LibraryBackupReceipt.self) { cancellable = $0 }
    }
    private func scratch() throws -> URL {
        let url = local.recoveryDirectory.appendingPathComponent("BackupWork")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        return url
    }
    func create(at file: URL, workspace: NodeWorkspaceStore) async {
        guard !busy else { return }
        begin("正在准备备份"); defer { end() }
        let access = file.startAccessingSecurityScopedResource()
        defer { if access { file.stopAccessingSecurityScopedResource() } }
        do {
            try await local.beginMaintenance(workspace: workspace)
            do {
                phase = "正在备份并核对文件"
                _ = try await execute(["create", "--data", local.dataDirectory.path, "--destination", file.path])
                savedFile = file; result = "备份已保存并校验。"
            } catch { problem = error.localizedDescription }
            phase = "正在重新打开资料库"
            await local.finishMaintenance(workspace: workspace)
            if !local.running { problem = local.problem ?? "资料库暂未重新连接，请在侧栏重试。" }
        } catch { problem = error.localizedDescription }
    }
    func inspect(_ file: URL) async -> LibraryBackupReceipt? {
        guard !busy else { return nil }
        begin("正在校验备份"); defer { end() }
        let access = file.startAccessingSecurityScopedResource()
        defer { if access { file.stopAccessingSecurityScopedResource() } }
        do { return try await execute(["inspect", "--file", file.path, "--scratch", try scratch().path]) }
        catch { problem = error.localizedDescription; return nil }
    }
    func restore(_ file: URL, receipt: LibraryBackupReceipt, localRecovery: Bool, workspace: NodeWorkspaceStore) async -> Bool {
        guard !busy else { return false }
        begin("正在恢复到独立副本"); defer { end() }
        let access = file.startAccessingSecurityScopedResource()
        defer { if access { file.stopAccessingSecurityScopedResource() } }
        do {
            guard localRecovery || !local.needsLocalRecoveryDirectory else {
                throw NodeFailure(message: "原资料库位置已不可用，请重新选择备份，核对新的恢复位置。")
            }
            let parent = localRecovery ? local.recoveryDirectory : local.baseDirectory.appendingPathComponent("RestoredLibraries")
            try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let destination = parent.appendingPathComponent(UUID().uuidString)
            _ = try await execute(["restore", "--file", file.path, "--destination", destination.path,
                                   "--sha256", receipt.archiveSHA256, "--scratch", try scratch().path])
            phase = "正在打开恢复副本"
            do {
                if localRecovery {
                    let checked = try await command.run("library_location_cli", arguments: ["inspect", "--data", destination.path], as: LibraryLocationPreview.self)
                    try await local.openExistingLibrary(checked, selectedURL: destination, workspace: workspace)
                } else { try await local.activateRestored(destination, workspace: workspace) }
            }
            catch {
                savedFile = destination
                throw error
            }
            result = "恢复副本已打开。原资料库保留，接收与自动整理已暂停。"
            return true
        } catch { problem = error.localizedDescription; return false }
    }
    func returnToOriginal(workspace: NodeWorkspaceStore) async {
        guard !busy else { return }
        begin("正在返回原资料库"); defer { end() }
        do {
            try await local.returnToOriginal(workspace: workspace)
            result = "已返回原资料库，恢复副本仍保留。"
        } catch { problem = error.localizedDescription }
    }

}

private struct BackupPreview: Identifiable {
    let file: URL
    let receipt: LibraryBackupReceipt
    let localRecovery: Bool
    var id: String { receipt.id }
}

struct LibraryBackupSection: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @ObservedObject private var local = LocalNodeController.shared
    @ObservedObject private var controller = LibraryBackupController.shared
    @ObservedObject private var location = LibraryLocationController.shared
    @State private var preview: BackupPreview?
    var body: some View {
        Section("备份与还原") {
            VStack(alignment: .leading, spacing: 10) {
                Text("备份包含已接收原音、归档文字、已保留的文档原件、记忆与修订历史。外部来源目录及未保留的附件不包含在内。")
                    .font(.callout).fixedSize(horizontal: false, vertical: true)
                Text("备份未加密，请妥善保存。应用的模型密钥、设备连接口令和外部读取授权不随备份迁移。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                ViewThatFits(in: .horizontal) {
                    HStack { actions }
                    VStack(alignment: .leading, spacing: 8) { actions }
                }
                if local.running && !local.managesProcess && !controller.busy {
                    Text("备份与切换资料库需要应用管理的本机节点；外部启动的节点请先在原启动位置停止，再重新打开应用。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            if local.isRestoredLibrary || local.problem == "恢复副本的选择记录不可读取，请先在数据设置中返回原资料库。" {
                VStack(alignment: .leading, spacing: 10) {
                    Label("当前使用恢复副本", systemImage: "externaldrive.badge.checkmark").font(.headline)
                    Text(verbatim: local.dataDirectory.path).font(.caption).textSelection(.enabled)
                    Button("返回原资料库") { Task { await controller.returnToOriginal(workspace: workspace) } }
                        .disabled(controller.busy || location.busy || workspace.hasUnfinishedEdits)
                }
            }
            progressAndResult
        }
        .sheet(item: $preview) { item in
            NavigationStack {
                Form {
                    Section("备份内容") {
                        backupValue("备份时间", item.receipt.date)
                        backupValue("原音", String(item.receipt.recordings))
                        backupValue("导入资料", String(item.receipt.documents))
                        backupValue("文档原件副本", String(item.receipt.originalDocuments ?? 0))
                        if let modified = item.receipt.modifiedOriginalDocuments, modified > 0 {
                            backupValue("有改动的原件副本", String(modified))
                            Text("这些副本与导入时的原件不同。恢复会保留修改后的文件，原件核对状态仍会提示差异。")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                        backupValue("笔记", String(item.receipt.notes))
                        backupValue("记忆（含待确认与停用）", String(item.receipt.memories))
                        backupValue("文件大小（展开后）", item.receipt.bytes.formatted(.byteCount(style: .file).locale(AppLanguage.current.locale)))
                    }
                    Section("恢复方式") {
                        Text("恢复到这台电脑上的独立副本，原资料库继续保留。原位置可用时可以重新打开。")
                        if item.localRecovery {
                            Text("原资料库位置不可用，这次恢复会保存在本机独立目录，并记住新位置。")
                            Text(verbatim: local.recoveryDirectory.path).font(.caption).textSelection(.enabled)
                        }
                        Text("新副本暂停接收和自动整理；外部来源须重新选择，手机和外部读取方须重新连接。")
                        Text("只恢复备份中已留存的资料，外部目录与原始附件不会自动下载。")
                    }
                }.formStyle(.grouped)
                    .safeAreaInset(edge: .top, spacing: 0) {
                        if controller.busy || controller.problem != nil || workspace.hasUnfinishedEdits {
                            VStack(alignment: .leading, spacing: 8) {
                                if workspace.hasUnfinishedEdits {
                                    Text("还有未完成的编辑。请先保存或复制内容，并关闭编辑页面，再切换或备份资料库。")
                                }
                                progressAndResult
                            }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(16).background(AppPalette.surface)
                        }
                    }
                    .navigationTitle(L("核对备份"))
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("返回") { preview = nil }.disabled(controller.busy)
                        }
                        ToolbarItem(placement: .confirmationAction) {
                            Button("恢复并打开副本") {
                                Task { if await controller.restore(item.file, receipt: item.receipt, localRecovery: item.localRecovery, workspace: workspace) { preview = nil } }
                            }.disabled(controller.busy || controller.problem != nil || (local.running && !local.managesProcess) || workspace.hasUnfinishedEdits)
                        }
                    }
            }.frame(minWidth: 500, idealWidth: 580, minHeight: 560, idealHeight: 650)
                .interactiveDismissDisabled().appLanguage()
        }
    }
    @ViewBuilder private var actions: some View {
        Button("创建备份…") { chooseBackup() }.disabled(controller.busy || location.busy || local.starting || !workspace.connected || !local.managesProcess || workspace.hasUnfinishedEdits)
        Button("选择备份还原…") { chooseRestore() }.disabled(controller.busy || location.busy || local.starting)
    }
    private func backupValue(_ label: String, _ value: String) -> some View {
        LabeledContent(L(label), value: value)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(Text(L(label)))
            .accessibilityValue(Text(verbatim: value))
    }
    @ViewBuilder private var progressAndResult: some View {
        if controller.busy {
            HStack {
                ProgressView().controlSize(.small)
                Text(L(controller.phase)).font(.callout)
                Spacer(minLength: 8)
                if controller.cancellable { Button("停止") { controller.cancel() } }
            }.accessibilityElement(children: .contain)
        }
        if controller.feedbackLibrary == local.dataDirectory.path {
            if let problem = controller.problem { Text(L(problem)).font(.callout).foregroundStyle(.secondary).textSelection(.enabled).id(problem) }
            if let result = controller.result { Text(L(result)).font(.callout).textSelection(.enabled).id(result) }
            if let file = controller.savedFile {
                Button("显示保存位置") { NSWorkspace.shared.activateFileViewerSelecting([file]) }
            }
        }
    }
    private func chooseBackup() {
        let panel = NSSavePanel(); panel.allowedContentTypes = [.zip]
        let date = DateFormatter()
        date.locale = Locale(identifier: "en_US_POSIX")
        date.calendar = Calendar(identifier: .gregorian)
        date.timeZone = .current
        date.dateFormat = "yyyy-MM-dd"
        panel.nameFieldStringValue = "Tracebook-\(date.string(from: .now)).zip"
        panel.message = L("选择新的备份文件名。备份期间本机资料库会短暂停止，完成后重新打开。")
        guard panel.runModal() == .OK, let file = panel.url else { return }
        Task { await controller.create(at: file, workspace: workspace) }
    }
    private func chooseRestore() {
        let panel = NSOpenPanel(); panel.allowedContentTypes = [.zip]; panel.allowsMultipleSelection = false
        panel.message = L("选择通过“创建备份”保存的文件。便携文字导出包不能用于还原。")
        guard panel.runModal() == .OK, let file = panel.url else { return }
        Task { if let receipt = await controller.inspect(file) { preview = BackupPreview(file: file, receipt: receipt, localRecovery: local.needsLocalRecoveryDirectory) } }
    }
}
