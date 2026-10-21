import Foundation

/// Immutable audio packages and receipts. The file provider owns cloud delivery timing.
public struct FolderExchange: Sendable {
    public let root: URL
    public init(root: URL) { self.root = root }
    public var inbox: URL { root.appendingPathComponent("VoiceMemory/inbox") }
    private var receipts: URL { root.appendingPathComponent("VoiceMemory/receipts") }

    public func publish(_ output: RecordingExport) throws -> String {
        let data = try Data(contentsOf: output.manifestURL)
        let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
        let manifest = try decoder.decode(RecordingExportManifest.self, from: data)
        let id = manifest.recording.id.uuidString
        let target = inbox.appendingPathComponent(id)
        try safe(target)
        try FileManager.default.createDirectory(at: inbox, withIntermediateDirectories: true)
        if FileManager.default.fileExists(atPath: target.path) {
            let old = try decoder.decode(RecordingExportManifest.self, from: Data(contentsOf: target.appendingPathComponent("manifest.json")))
            guard old.recording.id == manifest.recording.id, old.audioSHA256 == manifest.audioSHA256 else {
                throw RecordingIngress.IngressError.conflict
            }
            return manifest.audioSHA256
        }
        let staging = inbox.appendingPathComponent(".preparing-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: staging) }
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
        try FileManager.default.copyItem(at: output.audioURL, to: staging.appendingPathComponent(manifest.audioFilename))
        try data.write(to: staging.appendingPathComponent("manifest.json"), options: .atomic)
        try FileManager.default.moveItem(at: staging, to: target)
        return manifest.audioSHA256
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
