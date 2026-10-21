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
