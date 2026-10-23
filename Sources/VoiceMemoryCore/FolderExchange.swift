import Foundation

/// Immutable audio packages and receipts. The file provider owns cloud delivery timing.
public struct FolderExchange: Sendable {
    public struct RecoverySnapshot: Equatable, Sendable {
        public let recordingID: UUID
        public let originalSHA256: String
        public let originalManifestSHA256: String
        public let manifestSHA256: String?
        public let copySHA256: String?
    }
    public enum RecoveryError: Error, LocalizedError {
        case receiptExists, changed, unnecessary
        public var errorDescription: String? {
            switch self {
            case .receiptExists: "此录音已有接收回执，请先在电脑核对。已接收内容不会被重新发送替换。"
            case .changed: "录音或资料夹内容已变化，请重新查看恢复方案。"
            case .unnecessary: "交换副本已可用或正在补传，请重新检查接收状态。"
            }
        }
    }
    public enum ReceiptError: Error, LocalizedError {
        case unreadable, conflicting, changed, unverifiedOriginal
        public var errorDescription: String? {
            switch self {
            case .unreadable: "接收回执暂不可读。电脑会核对已保存的原音并重新提供回执。"
            case .conflicting: "接收回执与这条原音不一致。现有回执已保留，请在电脑核对。"
            case .changed: "接收回执正在变化，请稍后重新检查。"
            case .unverifiedOriginal: "电脑原音尚未通过校验，不能补写回执。请保留手机原音并在电脑核对。"
            }
        }
    }
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

    /// Preview is read-only. Any receipt, even an unreadable one, blocks replacement.
    public func previewRecovery(_ output: RecordingExport) throws -> RecoverySnapshot {
        let manifest = try decodeManifest(Data(contentsOf: output.manifestURL))
        try validate(manifest, id: manifest.recording.id, checksum: manifest.audioSHA256)
        guard try AudioDigest.sha256(output.audioURL) == manifest.audioSHA256 else { throw RecoveryError.changed }
        let snapshot = try recoverySnapshot(manifest.recording.id, originalSHA256: manifest.audioSHA256,
                                            originalManifestSHA256: AudioDigest.sha256(output.manifestURL))
        if snapshot.copySHA256 == manifest.audioSHA256,
           (try? hasPublication(manifest.recording.id, audioSHA256: manifest.audioSHA256)) == true {
            throw RecoveryError.unnecessary
        }
        return snapshot
    }

    /// The caller coordinates this entire operation. A moved package is never removed,
    /// including if publication fails; a later ordinary refresh can finish the missing send.
    public func recover(_ output: RecordingExport, expected: RecoverySnapshot) throws -> URL {
        guard try previewRecovery(output) == expected else { throw RecoveryError.changed }
        let source = try package(expected.recordingID)
        let archive = root.appendingPathComponent("VoiceMemory/recovery")
            .appendingPathComponent(expected.recordingID.uuidString + "-" + UUID().uuidString)
        try safe(archive)
        try FileManager.default.createDirectory(at: archive.deletingLastPathComponent(), withIntermediateDirectories: true)
        try FileManager.default.moveItem(at: source, to: archive)
        _ = try publish(output)
        return archive
    }

    private func recoverySnapshot(_ id: UUID, originalSHA256: String, originalManifestSHA256: String) throws -> RecoverySnapshot {
        let receiptURL = receipts.appendingPathComponent(id.uuidString + ".json")
        try safe(receiptURL)
        guard !FileManager.default.fileExists(atPath: receiptURL.path) else { throw RecoveryError.receiptExists }
        let target = try package(id)
        guard FileManager.default.fileExists(atPath: target.path) else { throw RecoveryError.unnecessary }
        let kind = try target.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard kind.isDirectory == true, kind.isSymbolicLink != true else {
            throw RecordingIngress.IngressError.invalidManifest
        }
        func hash(_ name: String) throws -> String? {
            let url = target.appendingPathComponent(name)
            try safe(url)
            guard FileManager.default.fileExists(atPath: url.path) else { return nil }
            let kind = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
            guard kind.isRegularFile == true, kind.isSymbolicLink != true else {
                throw RecordingIngress.IngressError.invalidManifest
            }
            return try AudioDigest.sha256(url)
        }
        return try RecoverySnapshot(recordingID: id, originalSHA256: originalSHA256,
                                    originalManifestSHA256: originalManifestSHA256,
                                    manifestSHA256: hash("manifest.json"), copySHA256: hash(id.uuidString + ".caf"))
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
        let data = try Data(contentsOf: path)
        do { return try JSONDecoder().decode(TransferReceipt.self, from: data) }
        catch { throw ReceiptError.unreadable }
    }
    /// The caller coordinates the selected folder. Healthy receipts are left unchanged.
    /// Missing or undecodable receipts require a matching durable local recording first.
    @discardableResult
    public func restoreReceipt(_ expected: TransferReceipt, from library: RecordingLibrary) throws -> Bool {
        guard expected.durable, expected.audioSHA256.count == 64,
              expected.audioSHA256.allSatisfy({ $0.isHexDigit }) else { throw ReceiptError.unverifiedOriginal }
        let path = receipts.appendingPathComponent(expected.recordingID.uuidString + ".json")
        func readExisting() throws -> Data? {
            try safe(path)
            guard FileManager.default.fileExists(atPath: path.path) else { return nil }
            let kind = try path.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
            guard kind.isRegularFile == true, kind.isSymbolicLink != true else { throw RecordingIngress.IngressError.invalidManifest }
            return try Data(contentsOf: path)
        }
        let previous = try readExisting()
        if let previous, let actual = try? JSONDecoder().decode(TransferReceipt.self, from: previous) {
            guard actual.durable, actual.recordingID == expected.recordingID,
                  actual.audioSHA256 == expected.audioSHA256 else { throw ReceiptError.conflicting }
            return false
        }
        let record = try library.recording(id: expected.recordingID)
        let audio = library.audioURL(for: record)
        guard record.isPlayable,
              Int64((try audio.resourceValues(forKeys: [.fileSizeKey])).fileSize ?? 0) == record.byteCount,
              try AudioDigest.sha256(audio) == expected.audioSHA256 else { throw ReceiptError.unverifiedOriginal }
        guard try readExisting() == previous else { throw ReceiptError.changed }
        if previous != nil {
            let archive = root.appendingPathComponent("VoiceMemory/recovery/receipts")
                .appendingPathComponent(expected.recordingID.uuidString + "-" + UUID().uuidString + ".json")
            try safe(archive)
            try FileManager.default.createDirectory(at: archive.deletingLastPathComponent(), withIntermediateDirectories: true)
            try FileManager.default.moveItem(at: path, to: archive)
        }
        try FileManager.default.createDirectory(at: receipts, withIntermediateDirectories: true)
        let staging = receipts.appendingPathComponent(".receipt-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: staging) }
        try JSONEncoder().encode(expected).write(to: staging, options: .atomic)
        // Do not replace a different receipt that arrived during local verification.
        try FileManager.default.moveItem(at: staging, to: path)
        return true
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
