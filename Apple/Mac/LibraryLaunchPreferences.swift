import CryptoKit
import Darwin
import Foundation

struct LibraryLocationPreview: Decodable, Identifiable, Sendable {
    struct ArchiveReceipt: Decodable, Sendable { let id: String; let outputDirectory: String }
    let id: String
    let snapshot: String
    let nodeID: String
    let dataDirectory: String
    let outputDirectory: String
    let outputAvailable: Bool
    let previousOutputDirectory: String?
    let relocationFiles: Int?
    let modifiedFiles: Int?
    let archiveCopyReceipt: ArchiveReceipt?
    let timeZone: String
    let recordings: Int
    let notes: Int
    let localInputs: Int?
    let documents: Int
    let memories: Int
    let pendingMemories: Int
}

struct PendingArchiveFolder: Codable, Sendable {
    let id: String
    let nodeID: String
    let dataDirectory: String
    let outputDirectory: String
    let bookmark: Data
}

struct RememberedLibrary: Codable, Sendable {
    var version = 1
    let bookmark: Data
    let basePath: String
    let baseNodeID: String
    var baseOutputDirectory: String
    var baseOutputBookmark: Data? = nil
    let port: Int
    var restoredID: UUID?
    var activeNodeID: String?
    var activeOutputDirectory: String?
    var activeOutputBookmark: Data? = nil
    var receivingPaused: Bool
    var pendingArchiveFolder: PendingArchiveFolder? = nil

    /// A committed receipt permits adopting only the exact folder already
    /// selected by the user. An unfinished copy keeps the prior selection.
    func resolvingArchiveCopy(_ preview: LibraryLocationPreview, dataPath: String) throws -> Self {
        guard let pending = pendingArchiveFolder else { return self }
        let base = dataPath == basePath
        let restoredPath = restoredID.map { basePath + "/RestoredLibraries/" + $0.uuidString }
        let expectedID = base ? baseNodeID : activeNodeID
        let expectedOutput = base ? baseOutputDirectory : activeOutputDirectory
        guard base || restoredPath == dataPath, UUID(uuidString: pending.id) != nil, pending.dataDirectory == dataPath,
              pending.nodeID.lowercased() == expectedID?.lowercased(),
              preview.nodeID.lowercased() == expectedID?.lowercased() else {
            throw NodeFailure(message: "归档复制的资料库身份不一致，请重新选择并核对。")
        }
        var next = self
        if preview.outputDirectory != expectedOutput {
            guard preview.archiveCopyReceipt?.id == pending.id,
                  preview.archiveCopyReceipt?.outputDirectory == pending.outputDirectory,
                  preview.outputDirectory == pending.outputDirectory else {
                throw NodeFailure(message: "归档复制结果尚未确认，请重新选择启动资料库并核对实际位置。")
            }
            if base { next.baseOutputDirectory = pending.outputDirectory; next.baseOutputBookmark = pending.bookmark }
            else { next.activeOutputDirectory = pending.outputDirectory; next.activeOutputBookmark = pending.bookmark }
        }
        next.pendingArchiveFolder = nil
        return next
    }

    func resolve() throws -> URL {
        guard version == 1, UUID(uuidString: baseNodeID) != nil, (1024...65535).contains(port) else {
            throw NodeFailure(message: "已保存的资料库选择不可读取，请重新选择。原目录仍保留。")
        }
        var stale = false
        let url = try URL(resolvingBookmarkData: bookmark, options: [.withSecurityScope, .withoutUI],
                          relativeTo: nil, bookmarkDataIsStale: &stale)
        guard !stale, url.isFileURL,
              url.standardizedFileURL.resolvingSymlinksInPath().path == basePath else {
            throw NodeFailure(message: "资料库位置或访问授权已变化，请重新选择并核对。")
        }
        return url.standardizedFileURL.resolvingSymlinksInPath()
    }

    func resolveOutput() throws -> URL? {
        let path = restoredID == nil ? baseOutputDirectory : activeOutputDirectory
        guard let path else { throw NodeFailure(message: "文字资料夹的位置记录不完整，请重新选择资料库。") }
        if path == basePath || path.hasPrefix(basePath + "/") { return nil }
        guard let bookmark = restoredID == nil ? baseOutputBookmark : activeOutputBookmark else {
            throw NodeFailure(message: "文字资料夹位于资料库目录之外，请重新选择资料库并授权文字位置。")
        }
        var stale = false
        let url = try URL(resolvingBookmarkData: bookmark, options: [.withSecurityScope, .withoutUI],
                          relativeTo: nil, bookmarkDataIsStale: &stale)
        guard !stale, url.isFileURL, url.standardizedFileURL.resolvingSymlinksInPath().path == path else {
            throw NodeFailure(message: "文字资料夹位置或访问授权已变化，请重新选择并核对。")
        }
        return url
    }
}

/// This is an app preference, kept separately from the selected user's library.
/// Debug client directories use their own preference file unless explicitly overridden.
@MainActor
final class LibraryLaunchPreferences {
    let file: URL
    private var revision: String?
    init(file: URL) { self.file = file }
    private func read() throws -> Data? {
        guard (try? FileManager.default.destinationOfSymbolicLink(atPath: file.path)) == nil else {
            throw NodeFailure(message: "资料库选择文件异常，未覆盖。请保留原文件并检查权限。")
        }
        guard FileManager.default.fileExists(atPath: file.path) else { return nil }
        let values = try file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true,
              (values.fileSize ?? 0) <= 64 * 1024 else {
            throw NodeFailure(message: "资料库选择文件异常，未覆盖。请保留原文件并检查权限。")
        }
        return try Data(contentsOf: file)
    }
    private func hash(_ data: Data?) -> String? {
        data.map { SHA256.hash(data: $0).map { String(format: "%02x", $0) }.joined() }
    }
    func load() throws -> RememberedLibrary? {
        let data = try read(); revision = hash(data)
        return try data.map { try JSONDecoder().decode(RememberedLibrary.self, from: $0) }
    }
    func save(_ selection: RememberedLibrary) throws {
        let parent = file.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let lockPath = file.appendingPathExtension("lock").path
        let fd = Darwin.open(lockPath, O_CREAT | O_RDWR | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard fd >= 0 else { throw NodeFailure(message: "无法保存资料库选择，请核对保存位置的权限。") }
        defer { flock(fd, LOCK_UN); Darwin.close(fd) }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else {
            throw NodeFailure(message: "另一窗口正在保存资料库选择，请稍后重试。")
        }
        let current = try read()
        guard hash(current) == revision else {
            throw NodeFailure(message: "资料库选择已被另一处修改，请重新打开应用后核对。")
        }
        let encoder = JSONEncoder(); encoder.outputFormatting = .sortedKeys
        let encoded = try encoder.encode(selection)
        guard encoded.count <= 64 * 1024 else {
            throw NodeFailure(message: "资料库位置记录过大，未覆盖原选择。")
        }
        if current == encoded { return }
        if let current {
            let history = parent.appendingPathComponent("LibraryLocationHistory")
            try FileManager.default.createDirectory(at: history, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let snapshot = history.appendingPathComponent(UUID().uuidString + ".json")
            try current.write(to: snapshot, options: .withoutOverwriting)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: snapshot.path)
        }
        try encoded.write(to: file, options: .atomic)
        revision = hash(encoded)
        // The parent is private; a permission adjustment failure must not make a
        // successfully replaced preference look like an unsaved library switch.
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
    }
}
