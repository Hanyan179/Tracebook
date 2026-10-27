import CryptoKit
import Darwin
import Foundation

public struct PersonalNoteDraftSnapshot: Sendable {
    public let draft: PersonalNoteDraft
    public let revision: String
}

public enum PersonalNoteDraftStoreError: Error { case changed, busy }

/// Cooperative compare-and-swap across windows/processes. Never removes the lock
/// file: replacing its inode would let two writers hold different locks.
public enum PersonalNoteDraftStore {
    public static func read(from url: URL) throws -> PersonalNoteDraftSnapshot? {
        let fd = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard fd >= 0 else {
            if errno == ENOENT { return nil }
            throw CocoaError(.fileReadNoPermission)
        }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        defer { try? handle.close() }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG, info.st_size <= 4 * 1024 * 1024 else {
            throw CocoaError(.fileReadCorruptFile)
        }
        let data = try handle.read(upToCount: 4 * 1024 * 1024 + 1) ?? Data()
        guard data.count <= 4 * 1024 * 1024 else { throw CocoaError(.fileReadCorruptFile) }
        return PersonalNoteDraftSnapshot(draft: try JSONDecoder().decode(PersonalNoteDraft.self, from: data), revision: hash(data))
    }

    @discardableResult
    public static func save(_ draft: PersonalNoteDraft, to url: URL, expecting revision: String?) throws -> String {
        try locked(url) {
            let current = try read(from: url)
            // Repeating an identical write is safe, including an onDisappear callback.
            if current?.draft == draft, let current { return current.revision }
            guard current?.revision == revision else { throw PersonalNoteDraftStoreError.changed }
            let encoder = JSONEncoder(); encoder.outputFormatting = .sortedKeys
            let data = try encoder.encode(draft)
            guard data.count <= 4 * 1024 * 1024 else { throw CocoaError(.fileWriteOutOfSpace) }
            try data.write(to: url, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
            return hash(data)
        }
    }

    /// False means a newer draft was deliberately left in place.
    @discardableResult
    public static func remove(from url: URL, expecting revision: String?) throws -> Bool {
        try locked(url) {
            guard let current = try read(from: url) else { return true }
            guard current.revision == revision else { return false }
            try FileManager.default.removeItem(at: url); return true
        }
    }

    private static func hash(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
    private static func locked<T>(_ url: URL, action: () throws -> T) throws -> T {
        let parent = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let values = try parent.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard values.isDirectory == true, values.isSymbolicLink != true else { throw CocoaError(.fileWriteNoPermission) }
        let fd = Darwin.open(url.appendingPathExtension("lock").path, O_CREAT | O_RDWR | O_NOFOLLOW | O_NONBLOCK, S_IRUSR | S_IWUSR)
        guard fd >= 0 else { throw CocoaError(.fileWriteNoPermission) }
        defer { Darwin.close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG, info.st_nlink == 1 else { throw CocoaError(.fileWriteNoPermission) }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else { throw PersonalNoteDraftStoreError.busy }
        defer { flock(fd, LOCK_UN) }
        return try action()
    }
}
