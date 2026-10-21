import AVFoundation
import CryptoKit
import Foundation

public enum AudioDigest {
    public static func sha256(_ url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hash = SHA256()
        while let bytes = try handle.read(upToCount: 1_048_576), !bytes.isEmpty {
            try Task.checkCancellation()
            hash.update(data: bytes)
        }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }
}

public struct TransferReceipt: Codable, Sendable {
    public let recordingID: UUID
    public let audioSHA256: String
    public let nodeID: String
    public let durable: Bool
    public init(recordingID: UUID, audioSHA256: String, nodeID: String, durable: Bool) {
        self.recordingID = recordingID
        self.audioSHA256 = audioSHA256
        self.nodeID = nodeID
        self.durable = durable
    }
}

public enum RecordingIngress {
    /// The caller must first retain any temporary transport file.
    public static func accept(audio: URL, manifestData: Data, into library: RecordingLibrary) throws -> TransferReceipt {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let manifest = try decoder.decode(RecordingExportManifest.self, from: manifestData)
        let record = manifest.recording
        guard manifest.schemaVersion == 1, record.isPlayable, record.trashedAt == nil,
              record.duration.isFinite, record.byteCount > 0,
              manifest.audioFilename == record.audioFilename,
              manifest.audioSHA256.count == 64,
              manifest.audioSHA256.allSatisfy({ $0.isHexDigit }) else { throw IngressError.invalidManifest }
        guard Int64((try audio.resourceValues(forKeys: [.fileSizeKey])).fileSize ?? 0) == record.byteCount,
              try AudioDigest.sha256(audio) == manifest.audioSHA256 else { throw IngressError.checksum }
        guard try AVAudioFile(forReading: audio).length > 0 else { throw IngressError.invalidManifest }
        try FileManager.default.createDirectory(at: library.root, withIntermediateDirectories: true)
        let destination = library.audioURL(for: record)
        if FileManager.default.fileExists(atPath: destination.path) {
            guard try AudioDigest.sha256(destination) == manifest.audioSHA256 else { throw IngressError.conflict }
        } else {
            let staging = library.root.appendingPathComponent(".incoming-\(UUID().uuidString).caf")
            defer { try? FileManager.default.removeItem(at: staging) }
            try FileManager.default.copyItem(at: audio, to: staging)
            try FileManager.default.moveItem(at: staging, to: destination)
        }
        let index = library.root.appendingPathComponent("\(record.id.uuidString).json")
        if FileManager.default.fileExists(atPath: index.path) {
            // Validate, but preserve local title edits and trash state on a retry.
            _ = try library.recording(id: record.id)
        } else {
            try library.save(record)
        }
        return TransferReceipt(recordingID: record.id, audioSHA256: manifest.audioSHA256,
                               nodeID: "paired-phone", durable: true)
    }

    public enum IngressError: Error, LocalizedError {
        case invalidManifest, checksum, conflict
        public var errorDescription: String? {
            switch self {
            case .invalidManifest: "收到的录音信息不完整，原音仍保留在发送设备。"
            case .checksum: "音频校验未通过，等待重新传输。"
            case .conflict: "相同编号对应不同音频，原有录音没有被覆盖。"
            }
        }
    }
}
