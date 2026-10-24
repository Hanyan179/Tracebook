import Darwin
import Foundation

// Compile alongside the production preference store; no app, Keychain or user library is opened.
struct NodeFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

@main
struct LibraryPreferencesCheck {
    @MainActor static func main() throws {
        let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent("artifacts/library-preferences-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("library-location.json")
        let first = LibraryLaunchPreferences(file: file)
        let second = LibraryLaunchPreferences(file: file)
        check(try first.load() == nil)
        check(try second.load() == nil)
        let selection = RememberedLibrary(bookmark: Data(), basePath: root.appendingPathComponent("synthetic-library").path,
            baseNodeID: UUID().uuidString, baseOutputDirectory: root.appendingPathComponent("synthetic-text").path,
            port: 18803, receivingPaused: true)
        try mustReject { _ = try selection.resolveOutput() }
        var nested = selection; nested.baseOutputDirectory = nested.basePath + "/Library"
        check(try nested.resolveOutput() == nil)
        nested.restoredID = UUID(); nested.activeOutputDirectory = root.appendingPathComponent("other-text").path
        try mustReject { _ = try nested.resolveOutput() }
        try first.save(selection)
        let initial = try Data(contentsOf: file)
        try mustReject { try second.save(selection) }
        check(try Data(contentsOf: file) == initial)
        _ = try second.load()
        var changed = selection; changed.receivingPaused = false
        try second.save(changed)
        try mustReject { try first.save(selection) }
        let history = root.appendingPathComponent("LibraryLocationHistory")
        let snapshots = try FileManager.default.contentsOfDirectory(at: history, includingPropertiesForKeys: nil)
        precondition(snapshots.count == 1)
        check(try Data(contentsOf: snapshots[0]) == initial)
        let corrupt = Data("{synthetic damaged preference".utf8)
        try corrupt.write(to: file)
        let repair = LibraryLaunchPreferences(file: file)
        try mustReject { _ = try repair.load() }
        try repair.save(selection)
        let preserved = try FileManager.default.contentsOfDirectory(at: history, includingPropertiesForKeys: nil)
        check(try preserved.contains { try Data(contentsOf: $0) == corrupt })
        let permissions = try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as? NSNumber
        precondition(permissions?.intValue == 0o600)
        let lock = open(file.appendingPathExtension("lock").path, O_RDWR)
        precondition(lock >= 0 && flock(lock, LOCK_EX | LOCK_NB) == 0)
        try mustReject { try repair.save(changed) }
        flock(lock, LOCK_UN); close(lock)
        let link = root.appendingPathComponent("linked-preference.json")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: file)
        let linked = LibraryLaunchPreferences(file: link)
        try mustReject { _ = try linked.load() }
        try mustReject { try linked.save(changed) }
        check(try Data(contentsOf: file) == initial)
        print("Library preferences: output access boundaries, stale writers, revision history, corrupt-file recovery, private permissions, lock and symlink checks passed.")
    }

    static func check(_ condition: Bool) { precondition(condition) }

    static func mustReject(_ action: () throws -> Void) throws {
        var rejected = false
        do { try action() } catch { rejected = true }
        precondition(rejected, "Unsafe preference write was accepted")
    }
}
