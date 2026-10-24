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
    private var process: Process?
    private var cancelled = false
    private let local = LocalNodeController.shared

    private func begin(_ phase: String) {
        busy = true; self.phase = phase; problem = nil; result = nil; savedFile = nil; cancelled = false
    }
    private func end() { busy = false; cancellable = false; process = nil }
    func cancel() {
        guard cancellable, let process, process.isRunning else { return }
        cancelled = true; cancellable = false; phase = "正在取消并保留原资料"
        process.terminate()
    }
    private func execute(_ arguments: [String]) async throws -> LibraryBackupReceipt {
        guard let script = Bundle.main.url(forResource: "backup_cli", withExtension: "py") else {
            throw NodeFailure(message: "应用中缺少备份工具，请使用包含此功能的完整版本。")
        }
        if cancelled { throw NodeFailure(message: "操作已取消，当前资料库保持不变。") }
        let child = Process(), output = Pipe()
        child.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        child.arguments = [script.path] + arguments
        child.environment = ProcessInfo.processInfo.environment.merging(["PYTHONDONTWRITEBYTECODE": "1"]) { _, new in new }
        child.standardOutput = output; child.standardError = FileHandle.nullDevice
        try child.run(); process = child; cancellable = true
        let (status, data) = await Task.detached(priority: .utility) {
            let data = output.fileHandleForReading.readDataToEndOfFile()
            child.waitUntilExit()
            return (child.terminationStatus, data)
        }.value
        cancellable = false; process = nil
        if cancelled { throw NodeFailure(message: "操作已取消，当前资料库保持不变。") }
        struct Reply: Decodable { let ok: Bool; let error: String? }
        guard data.count <= 1024 * 1024, let reply = try? JSONDecoder().decode(Reply.self, from: data) else {
            throw NodeFailure(message: "备份操作未能完成，原资料库保留。请重新检查。")
        }
        guard status == 0, reply.ok else {
            throw NodeFailure(message: reply.error ?? "备份操作未能完成，原资料库保留。请重新检查。")
        }
        return try JSONDecoder().decode(LibraryBackupReceipt.self, from: data)
    }
    private func scratch() throws -> URL {
        let url = local.baseDirectory.appendingPathComponent("BackupWork")
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
    func restore(_ file: URL, receipt: LibraryBackupReceipt, workspace: NodeWorkspaceStore) async -> Bool {
        guard !busy else { return false }
        begin("正在恢复到独立副本"); defer { end() }
        let access = file.startAccessingSecurityScopedResource()
        defer { if access { file.stopAccessingSecurityScopedResource() } }
        do {
            let parent = local.baseDirectory.appendingPathComponent("RestoredLibraries")
            try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let destination = parent.appendingPathComponent(UUID().uuidString)
            _ = try await execute(["restore", "--file", file.path, "--destination", destination.path,
                                   "--sha256", receipt.archiveSHA256, "--scratch", try scratch().path])
            phase = "正在打开恢复副本"
            do { try await local.activateRestored(destination, workspace: workspace) }
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
    func resumeReceiving() {
        do { try local.resumeReceiving(); result = "已恢复接收。模型任务仍需逐项核对后启用。" }
        catch { problem = error.localizedDescription }
    }
}

private struct BackupPreview: Identifiable {
    let file: URL
    let receipt: LibraryBackupReceipt
    var id: String { receipt.id }
}

struct LibraryBackupSection: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @ObservedObject private var local = LocalNodeController.shared
    @ObservedObject private var controller = LibraryBackupController.shared
    @State private var preview: BackupPreview?
    @State private var resumeConfirmation = false
    var body: some View {
        Section("备份与还原") {
            VStack(alignment: .leading, spacing: 10) {
                Text("备份包含已接收原音、归档文字、记忆、来源记录和修订历史。外部来源目录与尚未留存的原始附件不包含在内。")
                    .font(.callout).fixedSize(horizontal: false, vertical: true)
                Text("备份未加密，请妥善保存。密钥、设备连接口令和外部读取授权不随备份迁移。")
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
                    if local.receivingPaused {
                        Text("接收已暂停。请先核对资料；继续接收会使用此前选择的同步资料夹和本机保留的原音。")
                            .font(.caption).foregroundStyle(.secondary)
                        Button("恢复接收…") { resumeConfirmation = true }.disabled(controller.busy)
                    }
                    Button("返回原资料库") { Task { await controller.returnToOriginal(workspace: workspace) } }
                        .disabled(controller.busy)
                }
            }
            progressAndResult
        }
        .confirmationDialog("继续向恢复副本接收资料？", isPresented: $resumeConfirmation, titleVisibility: .visible) {
            Button("恢复接收") { controller.resumeReceiving() }
            Button("取消", role: .cancel) { }
        } message: { Text("将使用此前的同步资料夹和本机保留的原音。模型整理和转写不会因此自动启用。") }
        .sheet(item: $preview) { item in
            NavigationStack {
                Form {
                    Section("备份内容") {
                        backupValue("备份时间", item.receipt.date)
                        backupValue("原音", String(item.receipt.recordings))
                        backupValue("导入资料", String(item.receipt.documents))
                        backupValue("笔记", String(item.receipt.notes))
                        backupValue("记忆（含待确认与停用）", String(item.receipt.memories))
                        backupValue("文件大小（展开后）", item.receipt.bytes.formatted(.byteCount(style: .file).locale(AppLanguage.current.locale)))
                    }
                    Section("恢复方式") {
                        Text("恢复到这台电脑上的独立副本，原资料库继续保留。打开副本后可以随时返回原资料库。")
                        Text("新副本暂停接收和自动整理；外部来源须重新选择，手机和外部读取方须重新连接。")
                        Text("只恢复备份中已留存的资料，外部目录与原始附件不会自动下载。")
                    }
                }.formStyle(.grouped)
                    .safeAreaInset(edge: .top, spacing: 0) {
                        if controller.busy || controller.problem != nil {
                            VStack(alignment: .leading, spacing: 8) { progressAndResult }
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
                                Task { if await controller.restore(item.file, receipt: item.receipt, workspace: workspace) { preview = nil } }
                            }.disabled(controller.busy || controller.problem != nil || (local.running && !local.managesProcess))
                        }
                    }
            }.frame(minWidth: 500, idealWidth: 580, minHeight: 560, idealHeight: 650)
                .interactiveDismissDisabled().appLanguage()
        }
    }
    @ViewBuilder private var actions: some View {
        Button("创建备份…") { chooseBackup() }.disabled(controller.busy || local.starting || (local.running && !local.managesProcess))
        Button("选择备份还原…") { chooseRestore() }.disabled(controller.busy || local.starting)
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
        if let problem = controller.problem { Text(L(problem)).font(.callout).foregroundStyle(.secondary).textSelection(.enabled).id(problem) }
        if let result = controller.result { Text(L(result)).font(.callout).textSelection(.enabled).id(result) }
        if let file = controller.savedFile {
            Button("显示保存位置") { NSWorkspace.shared.activateFileViewerSelecting([file]) }
        }
    }
    private func chooseBackup() {
        let panel = NSSavePanel(); panel.allowedContentTypes = [.zip]
        panel.nameFieldStringValue = "Tracebook-\(Date.now.formatted(.iso8601.year().month().day().dateSeparator(.dash))).zip"
        panel.message = L("选择新的备份文件名。备份期间本机资料库会短暂停止，完成后重新打开。")
        guard panel.runModal() == .OK, let file = panel.url else { return }
        Task { await controller.create(at: file, workspace: workspace) }
    }
    private func chooseRestore() {
        let panel = NSOpenPanel(); panel.allowedContentTypes = [.zip]; panel.allowsMultipleSelection = false
        panel.message = L("选择通过“创建备份”保存的文件。便携文字导出包不能用于还原。")
        guard panel.runModal() == .OK, let file = panel.url else { return }
        Task { if let receipt = await controller.inspect(file) { preview = BackupPreview(file: file, receipt: receipt) } }
    }
}
