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
    let dataDirectory: URL
    let port: Int
    var localEndpoint: String { "http://127.0.0.1:\(port)" }
    var phoneEndpoint: String {
        let hostname = SCDynamicStoreCopyLocalHostName(nil) as String? ?? "your-mac"
        return "http://\(hostname).local:\(port)"
    }
    private init() {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        #if DEBUG
        dataDirectory = ProcessInfo.processInfo.environment["VOICE_MEMORY_NODE_DATA"].map { URL(fileURLWithPath: $0) }
            ?? support.appendingPathComponent("VoiceMemory/Node")
        port = Int(ProcessInfo.processInfo.environment["VOICE_MEMORY_NODE_PORT"] ?? "") ?? 8765
        #else
        dataDirectory = support.appendingPathComponent("VoiceMemory/Node")
        port = 8765
        #endif
    }
    func start(workspace: NodeWorkspaceStore) async {
        guard !starting else { return }
        starting = true; problem = nil
        defer { starting = false }
        do {
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
            process.arguments = [script.path, "--data", dataDirectory.path, "--host", allowLAN ? "0.0.0.0" : "127.0.0.1", "--port", String(port)]
            let log = dataDirectory.appendingPathComponent("node.log")
            if !FileManager.default.fileExists(atPath: log.path) { FileManager.default.createFile(atPath: log.path, contents: nil, attributes: [.posixPermissions: 0o600]) }
            let handle = try FileHandle(forWritingTo: log); try handle.seekToEnd()
            process.standardOutput = handle; process.standardError = handle
            process.terminationHandler = { [weak self] _ in Task { @MainActor in self?.running = false } }
            try process.run(); self.process = process; managesProcess = true
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
        UserDefaults.standard.set(allowLAN, forKey: "node.allowLAN")
        if let process, process.isRunning {
            process.terminate()
            for _ in 0..<30 where process.isRunning { try? await Task.sleep(for: .milliseconds(100)) }
        }
        process = nil; running = false
        await start(workspace: workspace)
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
