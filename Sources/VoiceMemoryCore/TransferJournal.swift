import CryptoKit
import Foundation

public enum TransferState: String, Codable, Sendable {
    case queued, sending, awaitingReceipt, delivered, failed
}

public struct TransferJob: Codable, Identifiable, Sendable {
    public let recordingID: UUID
    public let target: String
    public var state: TransferState = .queued
    public var attempts = 0
    public var nextAttemptAt: Date?
    public var audioSHA256: String?
    public var message: String?
    public var updatedAt: Date = .now
    public var id: String {
        let destination = SHA256.hash(data: Data(target.utf8)).prefix(8).map { String(format: "%02x", $0) }.joined()
        return "\(destination)-\(recordingID.uuidString)"
    }
    public init(recordingID: UUID, target: String) {
        self.recordingID = recordingID
        self.target = target
    }
    public mutating func fail(_ reason: String, now: Date = .now) {
        state = .failed
        message = reason
        updatedAt = now
        nextAttemptAt = now.addingTimeInterval(min(600, pow(2, Double(min(attempts, 8))) * 3))
    }
    public mutating func acknowledge(_ receipt: TransferReceipt) throws {
        guard receipt.durable, receipt.recordingID == recordingID,
              let audioSHA256, receipt.audioSHA256 == audioSHA256 else {
            throw RecordingIngress.IngressError.checksum
        }
        state = .delivered
        message = nil
        nextAttemptAt = nil
        updatedAt = .now
    }
}

public struct TransferJournal: Sendable {
    public let root: URL
    public init(root: URL) { self.root = root }
    public func save(_ job: TransferJob) throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try JSONEncoder().encode(job).write(to: root.appendingPathComponent(job.id + ".json"), options: .atomic)
    }
    public func load() throws -> [TransferJob] {
        guard FileManager.default.fileExists(atPath: root.path) else { return [] }
        return try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .map {
                let job = try JSONDecoder().decode(TransferJob.self, from: Data(contentsOf: $0))
                guard $0.deletingPathExtension().lastPathComponent == job.id else {
                    throw RecordingLibrary.LibraryError.invalidMetadata
                }
                return job
            }
    }
}
