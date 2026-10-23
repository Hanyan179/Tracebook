import AVFoundation
import Foundation
import Testing
@testable import VoiceMemoryCore

@Test func receiverAcknowledgesOnlyVerifiedFilesAndPreservesLocalEditsOnRetry() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let source = RecordingLibrary(root: root.appendingPathComponent("watch"))
    let phone = RecordingLibrary(root: root.appendingPathComponent("phone"))
    var record = Recording(trigger: "shortcut")
    record.sourceDevice = .watch; record.status = .saved; record.duration = 1
    try source.save(record)
    try transferFixture(source.audioURL(for: record))
    record.byteCount = Int64(try Data(contentsOf: source.audioURL(for: record)).count)
    try source.save(record)
    let output = try RecordingExport.prepare(id: record.id, from: source, to: root.appendingPathComponent("outgoing"))
    let manifest = try Data(contentsOf: output.manifestURL)
    let receipt = try RecordingIngress.accept(audio: output.audioURL, manifestData: manifest, into: phone)
    #expect(receipt.durable)
    #expect(receipt.recordingID == record.id)
    #expect(receipt.audioSHA256 == (try AudioDigest.sha256(source.audioURL(for: record))))
    try phone.rename(id: record.id, title: "手机上修改过的标题")
    try phone.moveToTrash(id: record.id)
    _ = try RecordingIngress.accept(audio: output.audioURL, manifestData: manifest, into: phone)
    #expect(try phone.load().recordings.count == 1)
    #expect(try phone.recording(id: record.id).title == "手机上修改过的标题")
    #expect(try phone.recording(id: record.id).trashedAt != nil)
    #expect(try Data(contentsOf: source.audioURL(for: record)) == Data(contentsOf: phone.audioURL(for: record)))
}

@Test func corruptedTransferCannotBecomeAnAcceptedRecording() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let source = RecordingLibrary(root: root.appendingPathComponent("source"))
    var record = Recording(trigger: "button");record.status = .saved;record.duration = 1
    try source.save(record);try transferFixture(source.audioURL(for: record))
    record.byteCount = Int64(try Data(contentsOf: source.audioURL(for: record)).count);try source.save(record)
    let output = try RecordingExport.prepare(id: record.id, from: source, to: root.appendingPathComponent("export"))
    let manifest = try Data(contentsOf: output.manifestURL)
    var bytes = try Data(contentsOf: output.audioURL);bytes[bytes.count - 1] ^= 0xff
    try bytes.write(to: output.audioURL)
    let target = RecordingLibrary(root: root.appendingPathComponent("target"))
    #expect(throws: RecordingIngress.IngressError.self) {
        try RecordingIngress.accept(audio: output.audioURL, manifestData: manifest, into: target)
    }
    #expect(try target.load().recordings.isEmpty)
}

@Test func transferJournalSurvivesRestartAndRejectsWrongAcknowledgement() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let journal = TransferJournal(root: root)
    var job = TransferJob(recordingID: UUID(), target: "http://127.0.0.1:8765")
    job.audioSHA256 = String(repeating: "a", count: 64)
    job.state = .sending;job.attempts = 1
    try journal.save(job)
    var loaded = try #require(TransferJournal(root: root).load().first)
    #expect(loaded.state == .sending)
    #expect(throws: RecordingIngress.IngressError.self) {
        try loaded.acknowledge(TransferReceipt(recordingID: UUID(), audioSHA256: job.audioSHA256!, nodeID: "node", durable: true))
    }
    #expect(loaded.state == .sending)
    loaded.fail("offline", now: Date(timeIntervalSince1970: 0))
    #expect(loaded.nextAttemptAt == Date(timeIntervalSince1970: 6))
    try loaded.acknowledge(TransferReceipt(recordingID: loaded.recordingID, audioSHA256: loaded.audioSHA256!, nodeID: "node", durable: true))
    try journal.save(loaded)
    #expect(try TransferJournal(root: root).load().first?.state == .delivered)
    #expect(TransferJob(recordingID: loaded.recordingID, target: "https://other.example").id != loaded.id)
}

private func transferFixture(_ url: URL) throws {
    let format = try #require(AVAudioFormat(standardFormatWithSampleRate: 16000, channels: 1))
    let buffer = try #require(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 16000))
    buffer.frameLength = 16000
    let channel = try #require(buffer.floatChannelData?[0])
    for frame in 0..<16000 { channel[frame] = Float(sin(Double(frame) * .pi * 880 / 16000)) * 0.1 }
    try AVAudioFile(forWriting: url, settings: format.settings).write(from: buffer)
}

@Test func sharedFolderKeepsOriginalAndWaitsForDesktopReceipt() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let phone = RecordingLibrary(root: root.appendingPathComponent("phone"))
    let desktop = RecordingLibrary(root: root.appendingPathComponent("desktop"))
    var record = Recording(trigger: "button")
    record.status = .saved; record.duration = 1
    try phone.save(record); try transferFixture(phone.audioURL(for: record))
    record.byteCount = Int64(try Data(contentsOf: phone.audioURL(for: record)).count); try phone.save(record)
    let output = try RecordingExport.prepare(id: record.id, from: phone, to: root.appendingPathComponent("export"))
    let exchange = FolderExchange(root: root.appendingPathComponent("shared"))
    let hash = try exchange.publish(output)
    #expect(try exchange.receipt(record.id) == nil)
    #expect(try exchange.publish(output) == hash)
    #expect(try exchange.packageIDs() == [record.id])
    let package = try exchange.package(record.id)
    let receipt = try RecordingIngress.accept(audio: package.appendingPathComponent(record.audioFilename),
        manifestData: Data(contentsOf: package.appendingPathComponent("manifest.json")), into: desktop)
    try exchange.acknowledge(receipt)
    #expect(try FolderExchange(root: exchange.root).receipt(record.id)?.audioSHA256 == hash)
    #expect(try phone.load().recordings.count == 1)
    #expect(try desktop.load().recordings.count == 1)
}

@Test func incompleteCloudPackageCannotProduceReceipt() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let exchange = FolderExchange(root: root)
    let id = UUID()
    let package = try exchange.package(id)
    try FileManager.default.createDirectory(at: package, withIntermediateDirectories: true)
    try Data("{}".utf8).write(to: package.appendingPathComponent("manifest.json"))
    #expect(throws: (any Error).self) {
        try RecordingIngress.accept(audio: package.appendingPathComponent(id.uuidString + ".caf"),
            manifestData: Data(contentsOf: package.appendingPathComponent("manifest.json")),
            into: RecordingLibrary(root: root.appendingPathComponent("desktop")))
    }
    #expect(try exchange.receipt(id) == nil)
}

@Test func sharedFolderRejectsSymlinkOutsideSelectedFolder() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let selected = root.appendingPathComponent("selected")
    let outside = root.appendingPathComponent("outside")
    try FileManager.default.createDirectory(at: selected, withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
    try FileManager.default.createSymbolicLink(at: selected.appendingPathComponent("VoiceMemory"), withDestinationURL: outside)
    #expect(throws: RecordingIngress.IngressError.self) { try FolderExchange(root: selected).packageIDs() }
    #expect(try FileManager.default.contentsOfDirectory(atPath: outside.path).isEmpty)
}

private func folderRecoveryFixture(_ root: URL) throws -> (RecordingLibrary, Recording, RecordingExport, FolderExchange) {
    let library = RecordingLibrary(root: root.appendingPathComponent("source"))
    var record = Recording(trigger: "synthetic-folder-recovery")
    record.status = .saved; record.duration = 1
    try library.save(record); try transferFixture(library.audioURL(for: record))
    record.byteCount = Int64(try Data(contentsOf: library.audioURL(for: record)).count)
    try library.save(record)
    let output = try RecordingExport.prepare(id: record.id, from: library, to: root.appendingPathComponent("export"))
    return (library, record, output, FolderExchange(root: root.appendingPathComponent("shared")))
}

@Test func pendingFolderSendRepublishesRemovedPackageWithoutDuplicatingTheRecording() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let (source, record, output, exchange) = try folderRecoveryFixture(root)
    let hash = try exchange.publish(output)
    let original = try Data(contentsOf: source.audioURL(for: record))
    var job = TransferJob(recordingID: record.id, target: "folder:synthetic")
    job.audioSHA256 = hash; job.state = .awaitingReceipt
    let journal = TransferJournal(root: root.appendingPathComponent("journal"))
    try journal.save(job)
    try FileManager.default.removeItem(at: exchange.package(record.id))
    let restoredJob = try #require(TransferJournal(root: journal.root).load().first)
    #expect(restoredJob.audioSHA256 == hash)
    #expect(try exchange.hasPublication(record.id, audioSHA256: hash) == false)
    #expect(try exchange.publish(output) == hash)
    #expect(try exchange.hasPublication(record.id, audioSHA256: hash))
    #expect(try exchange.publish(output) == hash)
    #expect(try exchange.packageIDs() == [record.id])
    #expect(try exchange.receipt(record.id) == nil)
    #expect(try Data(contentsOf: source.audioURL(for: record)) == original)
}

@Test func folderRepairRestoresOnlyMissingFilesAndPreservesExistingManifestEdits() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let (_, record, output, exchange) = try folderRecoveryFixture(root)
    let hash = try exchange.publish(output)
    let package = try exchange.package(record.id)
    let manifest = package.appendingPathComponent("manifest.json")
    let audio = package.appendingPathComponent(record.audioFilename)
    var object = try #require(JSONSerialization.jsonObject(with: Data(contentsOf: manifest)) as? [String: Any])
    var metadata = try #require(object["recording"] as? [String: Any])
    metadata["title"] = "A title changed after publication"; object["recording"] = metadata
    let edited = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    try edited.write(to: manifest)
    try FileManager.default.removeItem(at: audio)
    #expect(try exchange.hasPublication(record.id, audioSHA256: hash) == false)
    _ = try exchange.publish(output)
    #expect(try Data(contentsOf: manifest) == edited)
    #expect(try AudioDigest.sha256(audio) == hash)
    let audioBefore = try Data(contentsOf: audio)
    try FileManager.default.removeItem(at: manifest)
    #expect(try exchange.hasPublication(record.id, audioSHA256: hash) == false)
    _ = try exchange.publish(output)
    #expect(try Data(contentsOf: audio) == audioBefore)
    #expect(try exchange.hasPublication(record.id, audioSHA256: hash))
}

@Test func folderRetryDoesNotReplaceCorruptOrConflictingFilesWithAFalseSuccess() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let (_, record, output, exchange) = try folderRecoveryFixture(root)
    let hash = try exchange.publish(output)
    let package = try exchange.package(record.id)
    let manifest = package.appendingPathComponent("manifest.json")
    let audio = package.appendingPathComponent(record.audioFilename)
    let goodManifest = try Data(contentsOf: manifest)
    let goodAudio = try Data(contentsOf: audio)
    var damaged = goodAudio; damaged[damaged.count - 1] ^= 0xff
    try damaged.write(to: audio)
    #expect(throws: RecordingIngress.IngressError.self) { try exchange.publish(output) }
    #expect(try Data(contentsOf: audio) == damaged)
    try goodAudio.write(to: audio)
    try Data("partial manifest".utf8).write(to: manifest)
    #expect(throws: RecordingIngress.IngressError.self) { try exchange.hasPublication(record.id, audioSHA256: hash) }
    #expect(throws: RecordingIngress.IngressError.self) { try exchange.publish(output) }
    #expect(try Data(contentsOf: manifest) == Data("partial manifest".utf8))
    try goodManifest.write(to: manifest)
    #expect(throws: RecordingIngress.IngressError.self) { try exchange.hasPublication(record.id, audioSHA256: String(repeating: "f", count: 64)) }
    #expect(try exchange.receipt(record.id) == nil)
}

@Test func folderRepairRejectsNestedSymlinksBeforeReadingOrWritingOutsideTheSelection() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let (_, record, output, exchange) = try folderRecoveryFixture(root)
    let hash = try exchange.publish(output)
    let audio = try exchange.package(record.id).appendingPathComponent(record.audioFilename)
    let outside = root.appendingPathComponent("outside.caf")
    try FileManager.default.moveItem(at: audio, to: outside)
    let original = try Data(contentsOf: outside)
    try FileManager.default.createSymbolicLink(at: audio, withDestinationURL: outside)
    #expect(throws: RecordingIngress.IngressError.self) { try exchange.hasPublication(record.id, audioSHA256: hash) }
    #expect(throws: RecordingIngress.IngressError.self) { try exchange.publish(output) }
    #expect(try Data(contentsOf: outside) == original)
}

@Test func explicitFolderRecoveryKeepsTheEntirePreviousCopyAndWaitsForReceipt() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let (source, record, output, exchange) = try folderRecoveryFixture(root)
    let hash = try exchange.publish(output)
    let original = try Data(contentsOf: source.audioURL(for: record))
    let package = try exchange.package(record.id)
    let manifest = try Data(contentsOf: package.appendingPathComponent("manifest.json"))
    let bad = Data("incomplete exchange copy".utf8)
    try bad.write(to: package.appendingPathComponent(record.audioFilename))
    try Data("human recovery note".utf8).write(to: package.appendingPathComponent("note.txt"))
    let plan = try exchange.previewRecovery(output)
    #expect(try Data(contentsOf: package.appendingPathComponent(record.audioFilename)) == bad)
    #expect(!FileManager.default.fileExists(atPath: exchange.root.appendingPathComponent("VoiceMemory/recovery").path))
    let archive = try exchange.recover(output, expected: plan)
    #expect(try Data(contentsOf: archive.appendingPathComponent(record.audioFilename)) == bad)
    #expect(try Data(contentsOf: archive.appendingPathComponent("manifest.json")) == manifest)
    #expect(try String(contentsOf: archive.appendingPathComponent("note.txt"), encoding: .utf8) == "human recovery note")
    #expect(try AudioDigest.sha256(package.appendingPathComponent(record.audioFilename)) == hash)
    #expect(try Data(contentsOf: source.audioURL(for: record)) == original)
    #expect(try exchange.receipt(record.id) == nil)
    #expect(throws: FolderExchange.RecoveryError.self) { try exchange.previewRecovery(output) }
    let receipt = try RecordingIngress.accept(audio: package.appendingPathComponent(record.audioFilename),
                                              manifestData: Data(contentsOf: package.appendingPathComponent("manifest.json")),
                                              into: RecordingLibrary(root: root.appendingPathComponent("desktop")))
    try exchange.acknowledge(receipt)
    #expect(try exchange.receipt(record.id)?.audioSHA256 == hash)
    #expect(try exchange.packageIDs() == [record.id])
}

@Test func explicitFolderRecoveryRefusesChangesSincePreviewWithoutMovingFiles() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let (_, record, output, exchange) = try folderRecoveryFixture(root)
    _ = try exchange.publish(output)
    let package = try exchange.package(record.id)
    try Data("first damaged copy".utf8).write(to: package.appendingPathComponent(record.audioFilename))
    let preview = try exchange.previewRecovery(output)
    let changed = Data("a later change".utf8)
    try changed.write(to: package.appendingPathComponent("manifest.json"))
    #expect(throws: FolderExchange.RecoveryError.self) { try exchange.recover(output, expected: preview) }
    #expect(try Data(contentsOf: package.appendingPathComponent("manifest.json")) == changed)
    #expect(!FileManager.default.fileExists(atPath: exchange.root.appendingPathComponent("VoiceMemory/recovery").path))
}

@Test func explicitFolderRecoveryRefusesAnyReceiptIncludingOneArrivingAfterPreview() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let (_, record, output, exchange) = try folderRecoveryFixture(root)
    let hash = try exchange.publish(output)
    let package = try exchange.package(record.id)
    let bad = Data("preserved conflicting audio".utf8)
    try bad.write(to: package.appendingPathComponent(record.audioFilename))
    let preview = try exchange.previewRecovery(output)
    try exchange.acknowledge(TransferReceipt(recordingID: record.id, audioSHA256: hash, nodeID: "synthetic", durable: true))
    #expect(throws: FolderExchange.RecoveryError.self) { try exchange.recover(output, expected: preview) }
    let receipt = exchange.root.appendingPathComponent("VoiceMemory/receipts/" + record.id.uuidString + ".json")
    try Data("unfinished receipt".utf8).write(to: receipt)
    #expect(throws: FolderExchange.RecoveryError.self) { try exchange.previewRecovery(output) }
    #expect(try Data(contentsOf: package.appendingPathComponent(record.audioFilename)) == bad)
    #expect(try Data(contentsOf: receipt) == Data("unfinished receipt".utf8))
    #expect(!FileManager.default.fileExists(atPath: exchange.root.appendingPathComponent("VoiceMemory/recovery").path))
}

@Test func explicitFolderRecoveryRejectsArchiveSymlinkAndKeepsOriginalPackage() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let (_, record, output, exchange) = try folderRecoveryFixture(root)
    _ = try exchange.publish(output)
    let package = try exchange.package(record.id)
    try Data("preserve me".utf8).write(to: package.appendingPathComponent(record.audioFilename))
    let preview = try exchange.previewRecovery(output)
    let outside = root.appendingPathComponent("outside")
    try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
    try FileManager.default.createSymbolicLink(at: exchange.root.appendingPathComponent("VoiceMemory/recovery"), withDestinationURL: outside)
    #expect(throws: RecordingIngress.IngressError.self) { try exchange.recover(output, expected: preview) }
    #expect(try FileManager.default.contentsOfDirectory(atPath: outside.path).isEmpty)
    #expect(FileManager.default.fileExists(atPath: package.path))
}

@Test func explicitFolderRecoveryRequiresNewReviewAfterTheOriginalTitleChanges() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let (source, record, output, exchange) = try folderRecoveryFixture(root)
    _ = try exchange.publish(output)
    let package = try exchange.package(record.id)
    try Data("old conflicting copy".utf8).write(to: package.appendingPathComponent(record.audioFilename))
    let preview = try exchange.previewRecovery(output)
    try source.rename(id: record.id, title: "A revised local title")
    let updated = try RecordingExport.prepare(id: record.id, from: source, to: root.appendingPathComponent("updated"))
    #expect(throws: FolderExchange.RecoveryError.self) { try exchange.recover(updated, expected: preview) }
    #expect(try source.recording(id: record.id).title == "A revised local title")
    #expect(FileManager.default.fileExists(atPath: package.path))
    #expect(!FileManager.default.fileExists(atPath: exchange.root.appendingPathComponent("VoiceMemory/recovery").path))
}
