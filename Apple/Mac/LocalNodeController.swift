import AppKit
import Combine
import Foundation
import SystemConfiguration

@MainActor
final class LocalNodeController: ObservableObject {
    static let shared = LocalNodeController()
    @Published private(set) var running = false
    @Published private(set) var starting = false
    @Published private(set) var managesProcess = false
    @Published private(set) var problem: String?
    @Published var allowLAN: Bool = UserDefaults.standard.bool(forKey: "node.allowLAN")
    private var process: Process?
    @Published private(set) var dataDirectory: URL
    @Published private(set) var receivingPaused = false
    @Published private(set) var maintenance = false
    let baseDirectory: URL
    private var selectionProblem: String?
    var isRestoredLibrary: Bool { dataDirectory != baseDirectory }
    var receivingAllowed: Bool { !maintenance && !receivingPaused && selectionProblem == nil }
    private struct LibrarySelection: Codable { let id: UUID; let receivingPaused: Bool }
    private var selectionURL: URL { baseDirectory.appendingPathComponent("active-library.json") }
    let port: Int
    var localEndpoint: String { "http://127.0.0.1:\(port)" }
    var phoneEndpoint: String {
        let hostname = SCDynamicStoreCopyLocalHostName(nil) as String? ?? "your-mac"
        return "http://\(hostname).local:\(port)"
    }
    private init() {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        #if DEBUG
        let base = ProcessInfo.processInfo.environment["VOICE_MEMORY_NODE_DATA"].map { URL(fileURLWithPath: $0) }
            ?? support.appendingPathComponent("VoiceMemory/Node")
        port = Int(ProcessInfo.processInfo.environment["VOICE_MEMORY_NODE_PORT"] ?? "") ?? 8765
        #else
        let base = support.appendingPathComponent("VoiceMemory/Node")
        port = 8765
        #endif
        baseDirectory = base.standardizedFileURL.resolvingSymlinksInPath()
        dataDirectory = baseDirectory
        if FileManager.default.fileExists(atPath: selectionURL.path) {
            do {
                let selected = try JSONDecoder().decode(LibrarySelection.self, from: Data(contentsOf: selectionURL))
                let directory = baseDirectory.appendingPathComponent("RestoredLibraries/\(selected.id.uuidString)")
                try validateRestoredDirectory(directory)
                dataDirectory = directory; receivingPaused = selected.receivingPaused
            } catch { selectionProblem = "恢复副本的选择记录不可读取，请先在数据设置中返回原资料库。" }
        }
    }
    func start(workspace: NodeWorkspaceStore, duringMaintenance: Bool = false) async {
        guard !starting, !maintenance || duringMaintenance else { return }
        starting = true; problem = nil
        workspace.prepareForLocalStartup()
        defer { starting = false }
        do {
            if let selectionProblem { throw NodeFailure(message: selectionProblem) }
            guard let script = Bundle.main.url(forResource: "server", withExtension: "py") else {
                throw NodeFailure(message: "应用中缺少处理节点文件。")
            }
            try FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
            let tokenURL = dataDirectory.appendingPathComponent("connection-token")
            if let token = try? String(contentsOf: tokenURL, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines),
               let url = URL(string: localEndpoint),
               (try? await NodeClient(endpoint: url, token: token).read("v1/status", as: NodeStatus.self)) != nil {
                try workspace.configure(endpoint: localEndpoint, token: token)
                running = true; managesProcess = process?.isRunning == true
                if !managesProcess { problem = "已连接外部启动的本机节点；监听地址请在启动它的位置修改。" }
                await workspace.refresh()
                try await ModelRuntime.activate(workspace: workspace)
                return
            }
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            process.environment = ProcessInfo.processInfo.environment.merging(["PYTHONDONTWRITEBYTECODE": "1"]) { _, new in new }
            process.arguments = [script.path, "--data", dataDirectory.path, "--host", allowLAN && !receivingPaused ? "0.0.0.0" : "127.0.0.1", "--port", String(port)]
            let log = dataDirectory.appendingPathComponent("node.log")
            if !FileManager.default.fileExists(atPath: log.path) { FileManager.default.createFile(atPath: log.path, contents: nil, attributes: [.posixPermissions: 0o600]) }
            let handle = try FileHandle(forWritingTo: log); try handle.seekToEnd()
            process.standardOutput = handle; process.standardError = handle
            process.terminationHandler = { [weak self] ended in Task { @MainActor in
                if self?.process === ended { self?.running = false; self?.managesProcess = false }
            } }
            try process.run(); self.process = process; managesProcess = true
            try? handle.close()
            for _ in 0..<30 {
                try await Task.sleep(for: .milliseconds(250))
                guard process.isRunning else { throw NodeFailure(message: "节点未能启动，请检查本机 Python 3 和端口是否可用。") }
                if let token = try? String(contentsOf: tokenURL, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines),
                   let url = URL(string: localEndpoint),
                   (try? await NodeClient(endpoint: url, token: token).read("v1/status", as: NodeStatus.self)) != nil {
                    try workspace.configure(endpoint: localEndpoint, token: token)
                    running = true; await workspace.refresh()
                    try await ModelRuntime.activate(workspace: workspace)
                    return
                }
            }
            throw NodeFailure(message: "节点启动后尚未响应，请稍后重试。")
        } catch { problem = error.localizedDescription }
    }
    func stop() {
        process?.terminate(); process = nil; running = false; managesProcess = false
    }
    func restart(workspace: NodeWorkspaceStore) async {
        guard !maintenance else { return }
        UserDefaults.standard.set(allowLAN, forKey: "node.allowLAN")
        do {
            try await beginMaintenance(workspace: workspace)
            await finishMaintenance(workspace: workspace)
        } catch { problem = error.localizedDescription }
    }
    private func validateRestoredDirectory(_ directory: URL) throws {
        let root = baseDirectory.appendingPathComponent("RestoredLibraries")
        guard directory.deletingLastPathComponent() == root,
              UUID(uuidString: directory.lastPathComponent) != nil,
              directory.resolvingSymlinksInPath() == directory,
              let marker = try JSONSerialization.jsonObject(with: Data(contentsOf: directory.appendingPathComponent("restored-library.json"))) as? [String: Any],
              marker["format"] as? String == "tracebook-library-backup",
              marker["dataDirectory"] as? String == directory.path,
              FileManager.default.fileExists(atPath: directory.appendingPathComponent("node.sqlite3").path) else {
            throw NodeFailure(message: "恢复副本尚未完成校验，未切换资料库。")
        }
    }
    private func saveSelection() throws {
        if isRestoredLibrary, let id = UUID(uuidString: dataDirectory.lastPathComponent) {
            try JSONEncoder().encode(LibrarySelection(id: id, receivingPaused: receivingPaused)).write(to: selectionURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: selectionURL.path)
        } else if FileManager.default.fileExists(atPath: selectionURL.path) {
            try FileManager.default.removeItem(at: selectionURL)
        }
    }
    func beginMaintenance(workspace: NodeWorkspaceStore) async throws {
        guard !maintenance, !starting, managesProcess || !running else {
            throw NodeFailure(message: "请先使用应用管理的本机资料库；外部启动的节点须在原启动位置停止。")
        }
        maintenance = true
        do {
            for _ in 0..<300 where FolderSyncController.shared.busy { try await Task.sleep(for: .milliseconds(100)) }
            guard !FolderSyncController.shared.busy else { throw NodeFailure(message: "资料夹仍在同步，请完成后重试。") }
            try await NodeSyncController.shared.suspendForLibraryMaintenance()
            if let owned = process, owned.isRunning {
                owned.terminate()
                for _ in 0..<300 where owned.isRunning { try await Task.sleep(for: .milliseconds(100)) }
                guard !owned.isRunning else { throw NodeFailure(message: "资料库尚未停止，未开始文件操作。请稍后重试。") }
            }
            process = nil; running = false; managesProcess = false
            workspace.prepareForLocalStartup()
        } catch { maintenance = false; throw error }
    }
    func finishMaintenance(workspace: NodeWorkspaceStore) async {
        await start(workspace: workspace, duringMaintenance: true)
        maintenance = false
        if receivingAllowed { NodeSyncController.shared.refresh(); await FolderSyncController.shared.refresh() }
    }
    func activateRestored(_ directory: URL, workspace: NodeWorkspaceStore) async throws {
        try validateRestoredDirectory(directory)
        try await switchLibrary(to: directory, workspace: workspace)
    }
    func returnToOriginal(workspace: NodeWorkspaceStore) async throws {
        if !running && process?.isRunning != true {
            selectionProblem = nil; dataDirectory = baseDirectory; receivingPaused = false
            try saveSelection(); await start(workspace: workspace)
            guard running else { throw NodeFailure(message: problem ?? "资料库暂未重新连接，请在侧栏重试。") }
            return
        }
        try await switchLibrary(to: baseDirectory, workspace: workspace)
    }
    private func switchLibrary(to directory: URL, workspace: NodeWorkspaceStore) async throws {
        let previous = dataDirectory, paused = receivingPaused
        try await beginMaintenance(workspace: workspace)
        do { try NodeSyncController.shared.prepareForLibrarySwitch() }
        catch { await finishMaintenance(workspace: workspace); throw error }
        dataDirectory = directory; receivingPaused = directory != baseDirectory
        await start(workspace: workspace, duringMaintenance: true)
        do {
            guard running && workspace.connected else { throw NodeFailure(message: problem ?? "恢复副本未能启动，已保留原资料库。") }
            try saveSelection()
        } catch {
            // Stop only this controller's new process before returning to the old library.
            if let owned = process, owned.isRunning {
                owned.terminate()
                for _ in 0..<300 where owned.isRunning { try? await Task.sleep(for: .milliseconds(100)) }
                guard !owned.isRunning else { maintenance = false; throw error }
            }
            process = nil; running = false; managesProcess = false
            dataDirectory = previous; receivingPaused = paused
            do { try saveSelection() }
            catch {
                await finishMaintenance(workspace: workspace)
                throw NodeFailure(message: "资料库选择未能保存。资料仍保留，请核对目录权限后重试。")
            }
            await finishMaintenance(workspace: workspace)
            if running && workspace.connected {
                throw NodeFailure(message: "切换未完成，已重新打开此前的资料库。原目录与恢复副本均保留。")
            }
            throw error
        }
        maintenance = false
        if receivingAllowed { NodeSyncController.shared.refresh(); await FolderSyncController.shared.refresh() }
    }
    func resumeReceiving() throws {
        let previous = receivingPaused
        receivingPaused = false
        do { try saveSelection() } catch { receivingPaused = previous; throw error }
        NodeSyncController.shared.refresh()
        Task { await FolderSyncController.shared.refresh() }
    }
    func copyToken() {
        guard let token = try? String(contentsOf: dataDirectory.appendingPathComponent("connection-token"), encoding: .utf8) else { return }
        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(token, forType: .string)
    }
    func chooseFolder(workspace: NodeWorkspaceStore) async {
        let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.canCreateDirectories = true
        panel.prompt = L("用作归档目录")
        guard panel.runModal() == .OK, let url = panel.url, let client = workspace.client else { return }
        do {
            try await client.send("v1/settings", body: ["outputDirectory": url.path])
            await workspace.refresh()
        } catch { problem = error.localizedDescription }
    }
}
