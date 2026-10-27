import CryptoKit
import Foundation

public struct LocalNoteDraft: Identifiable, Sendable {
    public let url: URL
    public let draft: PersonalNoteDraft
    public let modifiedAt: Date
    public var id: String { url.lastPathComponent }
}

public struct NoteDraftCursor: Sendable {
    public let offset: Int
    public let inventory: String
}

public struct NoteDraftPage: Sendable {
    public let items: [LocalNoteDraft]
    public let next: NoteDraftCursor?
    public let unreadable: Int
}

/// Reads only the app's local editor directory. Names must match the requested library.
public enum PersonalNoteDraftCatalog {
    public static func filename(endpoint: String, nodeID: String, noteID: UUID?) -> String {
        hash("v2\0" + endpoint + "\0" + nodeID + "\0note-drafts/" + (noteID?.uuidString ?? "new")) + ".json"
    }
    private static func hash(_ text: String) -> String {
        SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    }
    public static func read(directory: URL, endpoint: String, nodeID: String, after: NoteDraftCursor? = nil) throws -> NoteDraftPage {
        guard FileManager.default.fileExists(atPath: directory.path) else {
            return NoteDraftPage(items: [], next: nil, unreadable: 0)
        }
        let root = try directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard root.isDirectory == true, root.isSymbolicLink != true else { throw CocoaError(.fileReadCorruptFile) }
        let files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" && !$0.lastPathComponent.hasPrefix("identity-") }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        let inventory = hash(endpoint + "\0" + nodeID + "\0" + files.map(\.lastPathComponent).joined(separator: "\0"))
        if let after, after.inventory != inventory { throw CocoaError(.fileReadUnknown) }
        var offset = after?.offset ?? 0
        guard offset >= 0, offset <= files.count else { throw CocoaError(.fileReadCorruptFile) }
        var items: [LocalNoteDraft] = [], unreadable = 0, bytes = 0, checked = 0
        while offset < files.count && checked < 64 && bytes < 8 * 1024 * 1024 {
            let url = files[offset]; offset += 1; checked += 1
            do {
                let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey, .contentModificationDateKey])
                guard values.isRegularFile == true, values.isSymbolicLink != true, (values.fileSize ?? 0) <= 4 * 1024 * 1024 else {
                    throw CocoaError(.fileReadCorruptFile)
                }
                if bytes + (values.fileSize ?? 0) > 8 * 1024 * 1024 { offset -= 1; break }
                let handle = try FileHandle(forReadingFrom: url)
                defer { try? handle.close() }
                let data = try handle.read(upToCount: min(4 * 1024 * 1024, 8 * 1024 * 1024 - bytes) + 1) ?? Data()
                guard data.count <= 4 * 1024 * 1024 else { throw CocoaError(.fileReadCorruptFile) }
                if bytes + data.count > 8 * 1024 * 1024 { offset -= 1; break }
                bytes += data.count
                // Other editors share this directory and have no personal-note identity.
                guard let fields = try JSONSerialization.jsonObject(with: data) as? [String: Any], fields["id"] != nil else { continue }
                let draft = try JSONDecoder().decode(PersonalNoteDraft.self, from: data)
                guard url.lastPathComponent == filename(endpoint: endpoint, nodeID: nodeID, noteID: draft.storageID) ||
                      url.lastPathComponent == filename(endpoint: endpoint, nodeID: nodeID, noteID: nil) else { continue }
                guard draft.needsRecovery else { continue }
                items.append(LocalNoteDraft(url: url, draft: draft, modifiedAt: values.contentModificationDate ?? .distantPast))
            } catch { unreadable += 1 }
        }
        return NoteDraftPage(items: items, next: offset < files.count ? NoteDraftCursor(offset: offset, inventory: inventory) : nil, unreadable: unreadable)
    }
}
