import AVFoundation
import CryptoKit
import Foundation

/// The file boundary shared by manual exports and the future sync receiver.
public struct RecordingExportManifest: Codable, Sendable {
    public let schemaVersion: Int
    public let recording: Recording
    public let audioFilename: String
    public let audioSHA256: String
}

public struct RecordingExport: Sendable {
    public let audioURL: URL
    public let manifestURL: URL
    public var files: [URL] { [audioURL, manifestURL] }

    /// Destination must be new. A failed export never alters the source recording.
    public static func prepare(id: UUID, from library: RecordingLibrary, to destination: URL) throws -> Self {
        let record = try library.recording(id: id)
        guard record.trashedAt == nil, record.isPlayable else { throw ExportError.unavailable }
        let source = library.audioURL(for: record)
        let file = try AVAudioFile(forReading: source)
        guard file.length > 0 else { throw ExportError.unavailable }
        guard !FileManager.default.fileExists(atPath: destination.path) else { throw ExportError.destinationExists }
        try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
        do {
            let audio = destination.appendingPathComponent(record.audioFilename)
            try FileManager.default.copyItem(at: source, to: audio)
            let checksum = try AudioDigest.sha256(audio)
            let manifest = RecordingExportManifest(schemaVersion: 1, recording: record,
                                                   audioFilename: record.audioFilename, audioSHA256: checksum)
            let manifestURL = destination.appendingPathComponent("\(record.id.uuidString).json")
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            encoder.dateEncodingStrategy = .iso8601
            try encoder.encode(manifest).write(to: manifestURL, options: .atomic)
            return Self(audioURL: audio, manifestURL: manifestURL)
        } catch {
            try? FileManager.default.removeItem(at: destination)
            throw error
        }
    }

    public enum ExportError: Error, LocalizedError {
        case unavailable, destinationExists
        public var errorDescription: String? {
            switch self {
            case .unavailable: "这条录音暂时没有可导出的完整音频。"
            case .destinationExists: "导出位置已存在，请重新导出。"
            }
        }
    }
}
