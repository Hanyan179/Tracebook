import Foundation

enum BundledNodeRuntime {
    static func process(script: URL, arguments: [String]) throws -> Process {
        #if arch(arm64)
        let architecture = "arm64"
        #elseif arch(x86_64)
        let architecture = "x86_64"
        #else
        throw NodeFailure(message: "这个版本不支持当前电脑架构，请使用适配的应用版本。")
        #endif
        guard let resources = Bundle.main.resourceURL else {
            throw NodeFailure(message: "应用运行环境不完整，请重新安装完整版本；已有资料仍保留。")
        }
        let runtime = resources.appendingPathComponent("PythonRuntime/interpreters/macos-" + architecture)
        let executable = runtime.appendingPathComponent("bin/python3.12")
        guard executable.resolvingSymlinksInPath().path.hasPrefix(resources.resolvingSymlinksInPath().path + "/"),
              FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw NodeFailure(message: "应用运行环境不完整，请重新安装完整版本；已有资料仍保留。")
        }
        let process = Process()
        process.executableURL = executable
        process.arguments = ["-E", "-s", "-B", "-X", "utf8", script.path] + arguments
        process.environment = ProcessInfo.processInfo.environment.filter { !$0.key.hasPrefix("PYTHON") }
        process.currentDirectoryURL = resources
        return process
    }
}
