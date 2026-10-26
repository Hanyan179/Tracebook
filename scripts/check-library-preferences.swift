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
        let copyID = UUID().uuidString
        let copiedOutput = root.appendingPathComponent("copied-text").path
        func copyPreview(node: String, output: String, receipt: String? = nil) throws -> LibraryLocationPreview {
            var value: [String: Any] = ["id": "synthetic", "snapshot": "synthetic", "nodeID": node,
                "dataDirectory": selection.basePath, "outputDirectory": output, "outputAvailable": true,
                "timeZone": "Asia/Shanghai", "recordings": 0, "notes": 1, "documents": 0, "memories": 0, "pendingMemories": 0]
            if let receipt { value["archiveCopyReceipt"] = ["id": receipt, "outputDirectory": output] }
            return try JSONDecoder().decode(LibraryLocationPreview.self, from: JSONSerialization.data(withJSONObject: value))
        }
        var copying = selection
        copying.pendingArchiveFolder = PendingArchiveFolder(id: copyID, nodeID: selection.baseNodeID,
            dataDirectory: selection.basePath, outputDirectory: copiedOutput, bookmark: Data([1, 2, 3]))
        let unfinished = try copying.resolvingArchiveCopy(copyPreview(node: selection.baseNodeID, output: selection.baseOutputDirectory), dataPath: selection.basePath)
        check(unfinished.pendingArchiveFolder == nil && unfinished.baseOutputDirectory == selection.baseOutputDirectory)
        let completed = try copying.resolvingArchiveCopy(copyPreview(node: selection.baseNodeID, output: copiedOutput, receipt: copyID), dataPath: selection.basePath)
        check(completed.pendingArchiveFolder == nil && completed.baseOutputDirectory == copiedOutput && completed.baseOutputBookmark == Data([1, 2, 3]))
        try mustReject { _ = try copying.resolvingArchiveCopy(copyPreview(node: selection.baseNodeID, output: copiedOutput), dataPath: selection.basePath) }
        try mustReject { _ = try copying.resolvingArchiveCopy(copyPreview(node: UUID().uuidString, output: copiedOutput, receipt: copyID), dataPath: selection.basePath) }
        try mustReject { _ = try copying.resolvingArchiveCopy(copyPreview(node: selection.baseNodeID, output: copiedOutput, receipt: UUID().uuidString), dataPath: selection.basePath) }
        var restored = copying; restored.restoredID = UUID(); restored.activeNodeID = UUID().uuidString
        restored.activeOutputDirectory = root.appendingPathComponent("restored-old-text").path
        let restoredPath = restored.basePath + "/RestoredLibraries/" + restored.restoredID!.uuidString
        restored.pendingArchiveFolder = PendingArchiveFolder(id: copyID, nodeID: restored.activeNodeID!, dataDirectory: restoredPath,
            outputDirectory: copiedOutput, bookmark: Data([4]))
        let restoredResult = try restored.resolvingArchiveCopy(copyPreview(node: restored.activeNodeID!, output: copiedOutput, receipt: copyID), dataPath: restoredPath)
        check(restoredResult.activeOutputDirectory == copiedOutput && restoredResult.baseOutputDirectory == selection.baseOutputDirectory)
        try mustReject { _ = try restored.resolvingArchiveCopy(copyPreview(node: restored.activeNodeID!, output: copiedOutput, receipt: copyID), dataPath: root.appendingPathComponent("wrong-library").path) }
        let pendingFile = root.appendingPathComponent("archive-case/library-location.json")
        let pendingStore = LibraryLaunchPreferences(file: pendingFile)
        check(try pendingStore.load() == nil); try pendingStore.save(copying)
        check(try pendingStore.load()?.pendingArchiveFolder?.id == copyID)
        try pendingStore.save(completed)
        let savedCompleted = try Data(contentsOf: pendingFile)
        var oversized = completed; oversized.baseOutputBookmark = Data(repeating: 0, count: 70 * 1024)
        try mustReject { try pendingStore.save(oversized) }
        check(try Data(contentsOf: pendingFile) == savedCompleted)
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
