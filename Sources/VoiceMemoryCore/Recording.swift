import Foundation

public enum RecordingStatus: String, Codable, Sendable {
    case recording, paused, saved, interrupted, recovered, failed
}

public enum RecordingDevice: String, Codable, Sendable {
    case phone, watch
}

public struct Recording: Codable, Identifiable, Equatable, Sendable {
    public let id: UUID
    public let startedAt: Date
    public let timeZone: String
    public let trigger: String
    public var status: RecordingStatus
    public var duration: TimeInterval
    public var byteCount: Int64
    public var note: String?
    public var title: String?
    public var sourceDevice: RecordingDevice?
    public var trashedAt: Date?
    public var markers: [RecordingMarker]?
    public var audioFilename: String { "\(id.uuidString).caf" }
    public var isInProgress: Bool { status == .recording || status == .paused }
    public var isPlayable: Bool {
        !isInProgress && status != .failed && duration > 0
    }

    public init(id: UUID = UUID(), startedAt: Date = .now, trigger: String) {
        self.id = id
        self.startedAt = startedAt
        self.timeZone = TimeZone.current.identifier
        self.trigger = trigger
        self.status = .recording
        self.duration = 0
        self.byteCount = 0
    }
}

public struct RecordingLibrary: Sendable {
    public let root: URL
    public init(root: URL) { self.root = root }
    public func audioURL(for recording: Recording) -> URL {
        root.appendingPathComponent(recording.audioFilename)
    }
    public func recording(id: UUID) throws -> Recording {
        let url = root.appendingPathComponent("\(id.uuidString).json")
        guard FileManager.default.fileExists(atPath: url.path) else { throw LibraryError.notFound }
        let record = try JSONDecoder().decode(Recording.self, from: Data(contentsOf: url))
        guard record.id == id, record.duration.isFinite, record.duration >= 0, record.byteCount >= 0,
              RecordingMarker.valid(record.audioMarkers) else {
            throw LibraryError.invalidMetadata
        }
        return record
    }

    public func rename(id: UUID, title: String) throws {
        var record = try recording(id: id)
        guard !record.isInProgress else { throw LibraryError.inProgress }
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count <= 120 else { throw LibraryError.titleTooLong }
        record.title = trimmed.isEmpty ? nil : trimmed
        try save(record)
    }

    /// A reversible metadata change. Audio is never moved or removed.
    public func moveToTrash(id: UUID, at date: Date = .now) throws {
        var record = try recording(id: id)
        guard !record.isInProgress else { throw LibraryError.inProgress }
        record.trashedAt = record.trashedAt ?? date
        try save(record)
    }

    public func restore(id: UUID) throws {
        var record = try recording(id: id)
        record.trashedAt = nil
        try save(record)
    }
    public func save(_ recording: Recording) throws {
        guard recording.duration.isFinite, recording.duration >= 0, recording.byteCount >= 0,
              RecordingMarker.valid(recording.audioMarkers) else {
            throw LibraryError.invalidMetadata
        }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let data = try JSONEncoder().encode(recording)
        try data.write(to: root.appendingPathComponent("\(recording.id.uuidString).json"), options: .atomic)
    }
    /// A corrupt manifest is reported separately; other recordings remain available.
    public func load() throws -> (recordings: [Recording], unreadable: [String]) {
        guard FileManager.default.fileExists(atPath: root.path) else { return ([], []) }
        var recordings: [Recording] = []
        var unreadable: [String] = []
        let urls = try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
        for url in urls where url.pathExtension == "json" {
            do {
                let recording = try JSONDecoder().decode(Recording.self, from: Data(contentsOf: url))
                guard url.deletingPathExtension().lastPathComponent == recording.id.uuidString,
                      recording.duration.isFinite, recording.duration >= 0, recording.byteCount >= 0,
                      RecordingMarker.valid(recording.audioMarkers) else {
                    throw LibraryError.invalidMetadata
                }
                recordings.append(recording)
            } catch { unreadable.append(url.lastPathComponent) }
        }
        return (recordings.sorted { $0.startedAt > $1.startedAt }, unreadable.sorted())
    }
    public enum LibraryError: Error, LocalizedError {
        case invalidMetadata, notFound, inProgress, titleTooLong, tooManyMarkers
        public var errorDescription: String? {
            switch self {
            case .invalidMetadata: "记录信息不完整，原文件已保留。"
            case .notFound: "找不到这条录音。"
            case .inProgress: "请先结束并保存这条录音。"
            case .titleTooLong: "标题请控制在 120 个字以内。"
            case .tooManyMarkers: "这条录音已达到 1000 个重点标记，已有标记与原音均保留。"
            }
        }
    }
}

/// Prevents overlapping shortcut invocations from starting or stopping twice.
public struct TriggerGate: Sendable {
    private var inFlight = false
    private var lastCompleted: Date?
    public init() {}
    public mutating func begin(at now: Date = .now) -> Bool {
        guard !inFlight else { return false }
        if let lastCompleted, now.timeIntervalSince(lastCompleted) < 1.2 { return false }
        inFlight = true
        return true
    }
    public mutating func complete(at now: Date = .now) {
        inFlight = false
        lastCompleted = now
    }
}
