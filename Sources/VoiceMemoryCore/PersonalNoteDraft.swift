import Foundation

/// Local, unsubmitted edits. The caller supplies a path scoped to a library identity.
public struct PersonalNoteDraft: Codable, Equatable, Sendable {
    public var id: UUID
    /// Conflict copies retain the destination note ID but use their own local file.
    public var draftID: UUID?
    public var storageID: UUID { draftID ?? id }
    public var isConflictCopy: Bool { storageID != id }
    public var title: String
    public var text: String
    public var etag: String
    public var originalTitle: String
    public var originalText: String
    public var submittedTitle: String?
    public var submittedText: String?

    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.id == rhs.id && lhs.draftID == rhs.draftID && lhs.etag == rhs.etag &&
        lhs.title.utf8.elementsEqual(rhs.title.utf8) && lhs.text.utf8.elementsEqual(rhs.text.utf8) &&
        lhs.originalTitle.utf8.elementsEqual(rhs.originalTitle.utf8) && lhs.originalText.utf8.elementsEqual(rhs.originalText.utf8) &&
        lhs.submittedTitle.map { Array($0.utf8) } == rhs.submittedTitle.map { Array($0.utf8) } &&
        lhs.submittedText.map { Array($0.utf8) } == rhs.submittedText.map { Array($0.utf8) }
    }

    public init(id: UUID = UUID(), title: String = "", text: String = "", etag: String = "") {
        self.id = id; self.title = title; self.text = text; self.etag = etag
        originalTitle = title; originalText = text
    }
    public var hasChanges: Bool { !title.utf8.elementsEqual(originalTitle.utf8) || !text.utf8.elementsEqual(originalText.utf8) }
    public var needsRecovery: Bool { hasChanges || submittedText != nil }
    public func conflictCopy() -> Self {
        var copy = self; copy.draftID = UUID(); return copy
    }
    public func newNoteCopy() -> Self {
        var copy = Self(); copy.title = title; copy.text = text; return copy
    }
    public mutating func prepareSubmission() {
        submittedTitle = title.trimmingCharacters(in: .whitespacesAndNewlines); submittedText = text
    }
    /// Only the exact previously submitted contents acknowledge an uncertain save.
    @discardableResult
    public mutating func acknowledge(title savedTitle: String, text savedText: String, etag savedETag: String) -> Bool {
        guard let submittedTitle, let submittedText,
              submittedTitle.utf8.elementsEqual(savedTitle.utf8), submittedText.utf8.elementsEqual(savedText.utf8) else { return false }
        if title.trimmingCharacters(in: .whitespacesAndNewlines).utf8.elementsEqual(savedTitle.utf8) { title = savedTitle }
        originalTitle = savedTitle; originalText = savedText; etag = savedETag
        self.submittedTitle = nil; self.submittedText = nil
        return true
    }
    public func write(to url: URL) throws {
        let data = try JSONEncoder().encode(self)
        guard data.count <= 4 * 1024 * 1024 else { throw CocoaError(.fileWriteOutOfSpace) }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
        try data.write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
    public static func read(from url: URL) throws -> Self? {
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true, (values.fileSize ?? 0) <= 4 * 1024 * 1024 else {
            throw CocoaError(.fileReadCorruptFile)
        }
        return try JSONDecoder().decode(Self.self, from: Data(contentsOf: url))
    }
}
