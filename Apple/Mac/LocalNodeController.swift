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
    @Published private(set) var hasAttemptedStartup = false
    @Published private(set) var baseDirectory: URL
    @Published private(set) var selectionProblem: String?
    @Published private(set) var rememberedLocation: RememberedLibrary?
    let locationPreferences: LibraryLaunchPreferences
    let recoveryDirectory: URL
    private let startupCommand = LocalNodeCommand()
    private var scopedLocation: URL?
    private var scopedOutput: URL?
    var isRestoredLibrary: Bool { dataDirectory != baseDirectory }
    var hasRememberedLocation: Bool { rememberedLocation?.basePath == baseDirectory.path }
    var needsLocalRecoveryDirectory: Bool {
        !FileManager.default.fileExists(atPath: baseDirectory.appendingPathComponent("node.sqlite3").path)
    }
    var receivingAllowed: Bool {
        let usesLocal = UserDefaults.standard.object(forKey: "node.useLocal") as? Bool ?? true
        return !maintenance && !receivingPaused && selectionProblem == nil && (!usesLocal || running)
    }
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
        let environment = ProcessInfo.processInfo.environment
        let client = environment["VOICE_MEMORY_CLIENT_DATA"].map { URL(fileURLWithPath: $0) }
            ?? support.appendingPathComponent("VoiceMemory")
        let forced = environment["VOICE_MEMORY_NODE_DATA"].map { URL(fileURLWithPath: $0).standardizedFileURL.resolvingSymlinksInPath() }
        let settings = environment["VOICE_MEMORY_LIBRARY_LOCATION_SETTINGS"].map { URL(fileURLWithPath: $0) }
            ?? client.appendingPathComponent("library-location.json")
        let requestedPort = Int(environment["VOICE_MEMORY_NODE_PORT"] ?? "")
        #else
        let client = support.appendingPathComponent("VoiceMemory")
        let forced: URL? = nil
        let settings = client.appendingPathComponent("library-location.json")
        let requestedPort: Int? = nil
        #endif
        let preferences = LibraryLaunchPreferences(file: settings)
        locationPreferences = preferences
        recoveryDirectory = client.appendingPathComponent("RecoveredLibraries")
        var remembered: RememberedLibrary?
        var chosen: URL?
        var issue: String?
        do {
            remembered = try preferences.load()
            if let saved = remembered, forced == nil || forced?.path == saved.basePath {
                chosen = try saved.resolve()
            }
        } catch { if forced == nil { issue = "已保存的资料库位置暂不可用，请连接存储位置或重新选择。原目录仍保留。" } }
        let unavailablePath = issue == nil ? nil : remembered.map { URL(fileURLWithPath: $0.basePath) }
        let initialBase = forced ?? chosen ?? unavailablePath ?? client.appendingPathComponent("Node").standardizedFileURL.resolvingSymlinksInPath()
        baseDirectory = initialBase; dataDirectory = initialBase
        let savedPort = remembered.flatMap { (1...65535).contains($0.port) ? $0.port : nil }
        port = requestedPort ?? savedPort ?? 8765
        rememberedLocation = remembered
        selectionProblem = issue
        if let chosen, chosen.startAccessingSecurityScopedResource() { scopedLocation = chosen }
        if chosen != nil, let saved = remembered {
            receivingPaused = saved.receivingPaused
            if let restored = saved.restoredID {
                let directory = baseDirectory.appendingPathComponent("RestoredLibraries/\(restored.uuidString)")
                do { try validateRestoredDirectory(directory); dataDirectory = directory }
                catch { selectionProblem = "恢复副本的选择记录不可读取，请先在数据设置中返回原资料库。" }
            }
        } else if issue == nil && FileManager.default.fileExists(atPath: selectionURL.path) {
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
        guard !workspace.hasUnfinishedEdits else {
            problem = "还有未完成的编辑。请先保存或复制内容，并关闭编辑页面，再切换或备份资料库。"
            return
        }
        starting = true; hasAttemptedStartup = true; problem = nil
        workspace.prepareForLocalStartup()
        defer { starting = false }
        do {
            if selectionProblem != nil, hasRememberedLocation, let saved = rememberedLocation {
                let resolved = try saved.resolve()
                if scopedLocation == nil && resolved.startAccessingSecurityScopedResource() { scopedLocation = resolved }
                if let restored = saved.restoredID {
                    let directory = baseDirectory.appendingPathComponent("RestoredLibraries/\(restored.uuidString)")
                    try validateRestoredDirectory(directory); dataDirectory = directory
                }
                selectionProblem = nil
            }
            if let selectionProblem { throw NodeFailure(message: selectionProblem) }
            guard let script = Bundle.main.url(forResource: "server", withExtension: "py") else {
                throw NodeFailure(message: "应用中缺少处理节点文件。")
            }
            if hasRememberedLocation, let saved = rememberedLocation {
                var outputScope = saved
                if !isRestoredLibrary { outputScope.restoredID = nil }
                try accessOutput(for: outputScope)
                startupCommand.reset()
                let checked = try await startupCommand.run("library_location_cli", arguments: ["inspect", "--data", dataDirectory.path], as: LibraryLocationPreview.self)
                let expectedID = isRestoredLibrary ? saved.activeNodeID : saved.baseNodeID
                let expectedOutput = isRestoredLibrary ? saved.activeOutputDirectory : saved.baseOutputDirectory
                guard checked.nodeID.lowercased() == expectedID?.lowercased(), checked.outputDirectory == expectedOutput,
                      checked.outputAvailable else {
                    throw NodeFailure(message: "上次选择的资料库身份或文字位置已变化，请重新选择并核对。")
                }
            } else {
                try FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
            }
            let tokenURL = dataDirectory.appendingPathComponent("connection-token")
            if let token = try? String(contentsOf: tokenURL, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines),
               let url = URL(string: localEndpoint),
               (try? await NodeClient(endpoint: url, token: token).read("v1/status", timeout: 1, as: NodeStatus.self)) != nil {
                try workspace.configure(endpoint: localEndpoint, token: token)
                running = true; managesProcess = process?.isRunning == true
                if !managesProcess { problem = "已连接外部启动的本机节点；监听地址请在启动它的位置修改。" }
                await workspace.refresh()
                try await ModelRuntime.activate(workspace: workspace)
                return
            }
            let process: Process
            if let existing = self.process, existing.isRunning {
                // A connection timeout is not evidence that the owned process exited.
                process = existing
            } else {
                let created = try BundledNodeRuntime.process(script: script, arguments: ["--data", dataDirectory.path,
                    "--host", allowLAN && !receivingPaused ? "0.0.0.0" : "127.0.0.1", "--port", String(port)])
                let log = dataDirectory.appendingPathComponent("node.log")
                if !FileManager.default.fileExists(atPath: log.path) { FileManager.default.createFile(atPath: log.path, contents: nil, attributes: [.posixPermissions: 0o600]) }
                let handle = try FileHandle(forWritingTo: log); try handle.seekToEnd()
                defer { try? handle.close() }
                created.standardOutput = handle; created.standardError = handle
                created.terminationHandler = { [weak self, weak workspace] ended in Task { @MainActor in
                    guard let self, self.process === ended else { return }
                    self.running = false; self.managesProcess = false
                    if !self.maintenance && !self.starting {
                        let message = "本机资料库已停止。已有资料保留，请重新打开。"
                        self.problem = message; workspace?.localProcessStopped(message)
                    }
                } }
                try created.run(); self.process = created; process = created
            }
            managesProcess = true
            let clock = ContinuousClock()
            let deadline = clock.now.advanced(by: .seconds(10))
            while clock.now < deadline {
                try await Task.sleep(for: .milliseconds(250))
                guard process.isRunning else { throw NodeFailure(message: "资料库未能启动，请检查应用是否完整及连接端口是否被占用。") }
                if let token = try? String(contentsOf: tokenURL, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines),
                   let url = URL(string: localEndpoint),
                   (try? await NodeClient(endpoint: url, token: token).read("v1/status", timeout: 1, as: NodeStatus.self)) != nil {
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
        startupCommand.cancel()
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
        guard directory.deletingLastPathComponent().path == root.path,
              UUID(uuidString: directory.lastPathComponent) != nil,
              directory.resolvingSymlinksInPath().path == directory.path,
              let marker = try JSONSerialization.jsonObject(with: Data(contentsOf: directory.appendingPathComponent("restored-library.json"))) as? [String: Any],
              marker["format"] as? String == "tracebook-library-backup",
              marker["dataDirectory"] as? String == directory.path,
              FileManager.default.fileExists(atPath: directory.appendingPathComponent("node.sqlite3").path) else {
            throw NodeFailure(message: "恢复副本尚未完成校验，未切换资料库。")
        }
    }
    private func saveSelection(workspace: NodeWorkspaceStore? = nil) throws {
        if hasRememberedLocation, var saved = rememberedLocation {
            saved.restoredID = isRestoredLibrary ? UUID(uuidString: dataDirectory.lastPathComponent) : nil
            if isRestoredLibrary {
                saved.activeNodeID = workspace?.status?.nodeID ?? saved.activeNodeID
                saved.activeOutputDirectory = workspace?.status?.outputDirectory ?? saved.activeOutputDirectory
            } else {
                saved.activeNodeID = nil; saved.activeOutputDirectory = nil; saved.activeOutputBookmark = nil
                if let output = workspace?.status?.outputDirectory { saved.baseOutputDirectory = output }
            }
            saved.receivingPaused = receivingPaused
            try locationPreferences.save(saved); rememberedLocation = saved
            return
        }
        if isRestoredLibrary, let id = UUID(uuidString: dataDirectory.lastPathComponent) {
            try JSONEncoder().encode(LibrarySelection(id: id, receivingPaused: receivingPaused)).write(to: selectionURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: selectionURL.path)
        } else if FileManager.default.fileExists(atPath: selectionURL.path) {
            try FileManager.default.removeItem(at: selectionURL)
        }
    }
    private func accessOutput(for saved: RememberedLibrary) throws {
        let output = try saved.resolveOutput()
        guard output?.path != scopedOutput?.path else { return }
        let granted = output?.startAccessingSecurityScopedResource() == true
        scopedOutput?.stopAccessingSecurityScopedResource()
        scopedOutput = granted ? output : nil
    }
    func beginMaintenance(workspace: NodeWorkspaceStore) async throws {
        guard !workspace.hasUnfinishedEdits else {
            throw NodeFailure(message: "还有未完成的编辑。请先保存或复制内容，并关闭编辑页面，再切换或备份资料库。")
        }
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
        let previousIssue = selectionProblem
        selectionProblem = nil
        do { try await switchLibrary(to: baseDirectory, workspace: workspace) }
        catch { if !running { selectionProblem = previousIssue }; throw error }
    }
    private func switchLibrary(to directory: URL, workspace: NodeWorkspaceStore) async throws {
        let previous = dataDirectory, paused = receivingPaused, previousRemembered = rememberedLocation
        try await beginMaintenance(workspace: workspace)
        do { try NodeSyncController.shared.prepareForLibrarySwitch() }
        catch { await finishMaintenance(workspace: workspace); throw error }
        dataDirectory = directory; receivingPaused = directory != baseDirectory || hasRememberedLocation
        do {
            if isRestoredLibrary && hasRememberedLocation, var saved = rememberedLocation {
                startupCommand.reset()
                let checked = try await startupCommand.run("library_location_cli", arguments: ["inspect", "--data", directory.path], as: LibraryLocationPreview.self)
                saved.restoredID = UUID(uuidString: directory.lastPathComponent)
                saved.activeNodeID = checked.nodeID; saved.activeOutputDirectory = checked.outputDirectory
                rememberedLocation = saved
            }
            await start(workspace: workspace, duringMaintenance: true)
            guard running && workspace.connected else { throw NodeFailure(message: problem ?? "恢复副本未能启动，已保留原资料库。") }
            try saveSelection(workspace: workspace)
        } catch {
            // Stop only this controller's new process before returning to the old library.
            if let owned = process, owned.isRunning {
                owned.terminate()
                for _ in 0..<300 where owned.isRunning { try? await Task.sleep(for: .milliseconds(100)) }
                guard !owned.isRunning else { maintenance = false; throw error }
            }
            process = nil; running = false; managesProcess = false
            dataDirectory = previous; receivingPaused = paused; rememberedLocation = previousRemembered
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
    func openExistingLibrary(_ preview: LibraryLocationPreview, selectedURL: URL,
                             workspace: NodeWorkspaceStore) async throws {
        let chosen = selectedURL.standardizedFileURL.resolvingSymlinksInPath()
        guard chosen.path == preview.dataDirectory, preview.outputAvailable else {
            throw NodeFailure(message: "所选资料库或文字位置不可用，请重新核对。")
        }
        let access = selectedURL.startAccessingSecurityScopedResource()
        var keepAccess = false
        defer { if access && !keepAccess { selectedURL.stopAccessingSecurityScopedResource() } }
        let bookmark = try selectedURL.bookmarkData(options: .withSecurityScope, includingResourceValuesForKeys: nil, relativeTo: nil)
        var outputURL: URL?
        if preview.outputDirectory != chosen.path && !preview.outputDirectory.hasPrefix(chosen.path + "/") {
            let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true
            panel.allowsMultipleSelection = false; panel.canCreateDirectories = false
            panel.directoryURL = URL(fileURLWithPath: preview.outputDirectory)
            panel.prompt = L("授权文字资料夹")
            panel.message = L("文字资料夹位于资料库目录之外，请选择并授权已显示的位置。")
            guard panel.runModal() == .OK, let selected = panel.url,
                  selected.standardizedFileURL.resolvingSymlinksInPath().path == preview.outputDirectory else {
                throw NodeFailure(message: "未授权所选资料库的文字位置，当前资料库保持不变。")
            }
            outputURL = selected
        }
        let outputAccess = outputURL?.startAccessingSecurityScopedResource() == true
        defer { if outputAccess { outputURL?.stopAccessingSecurityScopedResource() } }
        let outputBookmark = try outputURL?.bookmarkData(options: .withSecurityScope, includingResourceValuesForKeys: nil, relativeTo: nil)
        let oldBase = baseDirectory, oldDirectory = dataDirectory, oldPaused = receivingPaused
        let oldRemembered = rememberedLocation, oldIssue = selectionProblem
        try await beginMaintenance(workspace: workspace)
        do {
            startupCommand.reset()
            let checked = try await startupCommand.run("library_location_cli",
                arguments: ["prepare", "--data", chosen.path, "--snapshot", preview.snapshot], as: LibraryLocationPreview.self)
            try NodeSyncController.shared.prepareForLibrarySwitch()
            let saved = RememberedLibrary(bookmark: bookmark, basePath: chosen.path, baseNodeID: checked.nodeID,
                baseOutputDirectory: checked.outputDirectory, baseOutputBookmark: outputBookmark, port: port, receivingPaused: true)
            baseDirectory = chosen; dataDirectory = chosen; rememberedLocation = saved
            selectionProblem = nil; receivingPaused = true
            await start(workspace: workspace, duringMaintenance: true)
            guard running && workspace.connected else {
                throw NodeFailure(message: problem ?? "所选资料库未能打开，原目录仍保留。")
            }
            try locationPreferences.save(saved)
            scopedLocation?.stopAccessingSecurityScopedResource()
            scopedLocation = access ? selectedURL : nil; keepAccess = access
            UserDefaults.standard.set(true, forKey: "node.useLocal")
            maintenance = false
        } catch {
            if let owned = process, owned.isRunning {
                owned.terminate()
                for _ in 0..<300 where owned.isRunning { try? await Task.sleep(for: .milliseconds(100)) }
                guard !owned.isRunning else { maintenance = false; throw error }
            }
            process = nil; running = false; managesProcess = false
            baseDirectory = oldBase; dataDirectory = oldDirectory; receivingPaused = oldPaused
            rememberedLocation = oldRemembered; selectionProblem = oldIssue
            await finishMaintenance(workspace: workspace)
            throw error
        }
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
            if hasRememberedLocation, var saved = rememberedLocation {
                let bookmark = try url.bookmarkData(options: .withSecurityScope, includingResourceValuesForKeys: nil, relativeTo: nil)
                if isRestoredLibrary { saved.activeOutputBookmark = bookmark }
                else { saved.baseOutputBookmark = bookmark }
                rememberedLocation = saved
                try saveSelection(workspace: workspace)
                if let saved = rememberedLocation { try accessOutput(for: saved) }
            }
        } catch { problem = error.localizedDescription }
    }
}
