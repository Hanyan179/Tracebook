import Foundation

/// Immutable audio packages and receipts. The file provider owns cloud delivery timing.
public struct FolderExchange: Sendable {
    public let root: URL
    public init(root: URL) { self.root = root }
    public var inbox: URL { root.appendingPathComponent("VoiceMemory/inbox") }
    private var receipts: URL { root.appendingPathComponent("VoiceMemory/receipts") }

    public func publish(_ output: RecordingExport) throws -> String {
        let data = try Data(contentsOf: output.manifestURL)
        let manifest = try decodeManifest(data)
        try validate(manifest, id: manifest.recording.id, checksum: manifest.audioSHA256)
        let id = manifest.recording.id.uuidString
        let target = inbox.appendingPathComponent(id)
        try safe(target)
        try FileManager.default.createDirectory(at: inbox, withIntermediateDirectories: true)
        let targetManifest = target.appendingPathComponent("manifest.json")
        let targetAudio = target.appendingPathComponent(manifest.audioFilename)
        try safe(targetManifest); try safe(targetAudio)
        if FileManager.default.fileExists(atPath: target.path) {
            if FileManager.default.fileExists(atPath: targetManifest.path) {
                let old = try decodeManifest(Data(contentsOf: targetManifest))
                try validate(old, id: manifest.recording.id, checksum: manifest.audioSHA256)
            }
            if FileManager.default.fileExists(atPath: targetAudio.path) {
                guard try AudioDigest.sha256(targetAudio) == manifest.audioSHA256 else {
                    throw RecordingIngress.IngressError.conflict
                }
            }
            if FileManager.default.fileExists(atPath: targetManifest.path),
               FileManager.default.fileExists(atPath: targetAudio.path) {
                return manifest.audioSHA256
            }
        }
        let staging = inbox.appendingPathComponent(".preparing-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: staging) }
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
        try FileManager.default.copyItem(at: output.audioURL, to: staging.appendingPathComponent(manifest.audioFilename))
        guard try AudioDigest.sha256(staging.appendingPathComponent(manifest.audioFilename)) == manifest.audioSHA256 else {
            throw RecordingIngress.IngressError.checksum
        }
        try data.write(to: staging.appendingPathComponent("manifest.json"), options: .atomic)
        if FileManager.default.fileExists(atPath: target.path) {
            // Restore only missing files. Atomic moves never overwrite a concurrent cloud edit.
            if !FileManager.default.fileExists(atPath: targetAudio.path) {
                try FileManager.default.moveItem(at: staging.appendingPathComponent(manifest.audioFilename), to: targetAudio)
            }
            if !FileManager.default.fileExists(atPath: targetManifest.path) {
                try FileManager.default.moveItem(at: staging.appendingPathComponent("manifest.json"), to: targetManifest)
            }
        } else {
            try FileManager.default.moveItem(at: staging, to: target)
        }
        return manifest.audioSHA256
    }

    /// Cheap availability check for pending sends. Receivers still verify the complete audio checksum.
    public func hasPublication(_ id: UUID, audioSHA256: String) throws -> Bool {
        let target = try package(id)
        let manifestURL = target.appendingPathComponent("manifest.json")
        let audioURL = target.appendingPathComponent(id.uuidString + ".caf")
        try safe(manifestURL); try safe(audioURL)
        guard FileManager.default.fileExists(atPath: manifestURL.path),
              FileManager.default.fileExists(atPath: audioURL.path) else { return false }
        let manifest = try decodeManifest(Data(contentsOf: manifestURL))
        try validate(manifest, id: id, checksum: audioSHA256)
        guard Int64((try audioURL.resourceValues(forKeys: [.fileSizeKey])).fileSize ?? 0) == manifest.recording.byteCount else {
            throw RecordingIngress.IngressError.conflict
        }
        return true
    }

    private func validate(_ manifest: RecordingExportManifest, id: UUID, checksum: String) throws {
        guard manifest.schemaVersion == 1, manifest.recording.id == id,
              manifest.audioFilename == id.uuidString + ".caf", manifest.recording.byteCount > 0,
              manifest.audioSHA256.count == 64, manifest.audioSHA256.allSatisfy({ $0.isHexDigit }) else {
            throw RecordingIngress.IngressError.invalidManifest
        }
        guard manifest.audioSHA256 == checksum else { throw RecordingIngress.IngressError.conflict }
    }

    private func decodeManifest(_ data: Data) throws -> RecordingExportManifest {
        let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
        do { return try decoder.decode(RecordingExportManifest.self, from: data) }
        catch { throw RecordingIngress.IngressError.invalidManifest }
    }

    public func packageIDs() throws -> [UUID] {
        try safe(inbox)
        guard FileManager.default.fileExists(atPath: inbox.path) else { return [] }
        return try FileManager.default.contentsOfDirectory(at: inbox, includingPropertiesForKeys: nil)
            .compactMap { UUID(uuidString: $0.lastPathComponent) }
    }
    public func package(_ id: UUID) throws -> URL {
        let url = inbox.appendingPathComponent(id.uuidString); try safe(url); return url
    }
    public func receipt(_ id: UUID) throws -> TransferReceipt? {
        let path = receipts.appendingPathComponent(id.uuidString + ".json")
        try safe(path)
        guard FileManager.default.fileExists(atPath: path.path) else { return nil }
        return try JSONDecoder().decode(TransferReceipt.self, from: Data(contentsOf: path))
    }
    public func acknowledge(_ receipt: TransferReceipt) throws {
        let path = receipts.appendingPathComponent(receipt.recordingID.uuidString + ".json")
        try safe(path)
        try FileManager.default.createDirectory(at: receipts, withIntermediateDirectories: true)
        try JSONEncoder().encode(receipt).write(to: path, options: .atomic)
    }
    private func safe(_ url: URL) throws {
        let allowed = root.resolvingSymlinksInPath().path + "/"
        var ancestor = url
        while ancestor.path != root.path {
            guard ancestor.path.hasPrefix(root.path + "/"),
                  ancestor.resolvingSymlinksInPath().path.hasPrefix(allowed) else {
                throw RecordingIngress.IngressError.invalidManifest
            }
            ancestor.deleteLastPathComponent()
        }
    }
}
