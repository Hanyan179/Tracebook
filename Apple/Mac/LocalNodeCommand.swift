import Foundation

/// Runs a bundled local helper; its bounded JSON response never contains credentials.
@MainActor
final class LocalNodeCommand {
    private struct Failure: Decodable { let ok: Bool; let error: String? }
    private var process: Process?
    private var cancelled = false
    var isRunning: Bool { process?.isRunning == true }
    func reset() { cancelled = false }
    func cancel() {
        guard let process, process.isRunning else { return }
        cancelled = true; process.terminate()
    }
    func run<T: Decodable & Sendable>(_ resource: String, arguments: [String], as: T.Type,
                                      activity: (Bool) -> Void = { _ in }) async throws -> T {
        guard !isRunning, let script = Bundle.main.url(forResource: resource, withExtension: "py") else {
            throw NodeFailure(message: "本机资料工具暂不可用，请使用完整版本并稍后重试。")
        }
        guard !cancelled else { throw NodeFailure(message: "操作已取消，当前资料库保持不变。") }
        let child = try BundledNodeRuntime.process(script: script, arguments: arguments), output = Pipe()
        child.standardOutput = output; child.standardError = FileHandle.nullDevice
        try child.run(); process = child; activity(true)
        defer { process = nil; activity(false) }
        let (status, data) = await Task.detached(priority: .utility) {
            let data = output.fileHandleForReading.readDataToEndOfFile()
            child.waitUntilExit(); return (child.terminationStatus, data)
        }.value
        guard !cancelled else { throw NodeFailure(message: "操作已取消，当前资料库保持不变。") }
        guard data.count <= 1024 * 1024, let reply = try? JSONDecoder().decode(Failure.self, from: data) else {
            throw NodeFailure(message: "本机资料操作未完成，原资料保留。请重新检查。")
        }
        guard status == 0, reply.ok else {
            throw NodeFailure(message: reply.error ?? "本机资料操作未完成，原资料保留。请重新检查。")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}
