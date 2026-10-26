import AVFoundation
import Foundation
import Testing
@testable import VoiceMemoryCore

@Test func markersPersistMediaTimeAcrossPauseAndRecovery() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let library = RecordingLibrary(root: root)
    var record = Recording(trigger: "button")
    let first = try record.mark(at: 1.2349)
    record.status = .paused
    #expect(try record.mark(at: 1.2349) == first)
    #expect(record.audioMarkers.count == 1)
    record.status = .recording
    try record.mark(at: 9.876)
    try library.save(record)
    var recovered = try RecordingLibrary(root: root).recording(id: record.id)
    #expect(recovered.audioMarkers.map(\.offsetMilliseconds) == [1234, 9876])
    recovered.status = .recovered
    recovered.duration = 9 // Preserve a marker beyond the readable tail, never move it.
    try library.save(recovered)
    #expect(try library.recording(id: record.id).audioMarkers == record.audioMarkers)
    #expect(throws: RecordingLibrary.LibraryError.self) { try recovered.mark(at: 10) }
}

@Test func markersRejectInvalidPositionsDuplicatesAndExcessWithoutDroppingOldItems() throws {
    var record = Recording(trigger: "button")
    for invalid in [-1, Double.nan, Double.infinity, Double(RecordingMarker.maximumOffset)] {
        #expect(throws: RecordingLibrary.LibraryError.self) { try record.mark(at: invalid) }
    }
    for index in 0..<RecordingMarker.maximumCount { try record.mark(at: Double(index)) }
    let before = record.audioMarkers
    #expect(throws: RecordingLibrary.LibraryError.self) { try record.mark(at: 1001) }
    #expect(throws: RecordingLibrary.LibraryError.self) { try record.mark(at: 500) }
    #expect(record.audioMarkers == before)
    #expect(!RecordingMarker.valid([before[0], before[0]]))
    #expect(!RecordingMarker.valid([RecordingMarker(offsetMilliseconds: -1)]))
    #expect(!RecordingMarker.valid([before[2], before[1]]))
}

@Test func markersHaveCrossLanguageStableChecksumAndLegacyMetadataRemainsReadable() throws {
    let markers = [RecordingMarker(id: UUID(uuidString: "00000000-0000-4000-8000-000000000001")!, offsetMilliseconds: 1234),
                   RecordingMarker(id: UUID(uuidString: "00000000-0000-4000-8000-000000000002")!, offsetMilliseconds: 9876)]
    #expect(RecordingMarker.checksum(markers) == "412ef8122d515d8211e2ac94a23e576f6e9dab8e77f32a6880b17336e03af332")
    #expect(RecordingMarker.checksum([]) == nil)
    let old = Recording(trigger: "button")
    let raw = try JSONEncoder().encode(old)
    #expect(!String(decoding: raw, as: UTF8.self).contains("markers"))
    #expect(try JSONDecoder().decode(Recording.self, from: raw).audioMarkers.isEmpty)
}

@Test func markersRequireTheirOwnDurableReceiptAndSurviveQueueRestart() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    var job = TransferJob(recordingID: UUID(), target: "synthetic-receiver")
    job.audioSHA256 = String(repeating: "a", count: 64)
    job.markersSHA256 = RecordingMarker.checksum([RecordingMarker(offsetMilliseconds: 12)])
    let journal = TransferJournal(root: root)
    try journal.save(job)
    var restored = try #require(TransferJournal(root: root).load().first)
    let missing = TransferReceipt(recordingID: job.recordingID, audioSHA256: job.audioSHA256!, nodeID: "synthetic", durable: true)
    #expect(throws: RecordingIngress.IngressError.self) { try restored.acknowledge(missing) }
    #expect(restored.state != .delivered)
    let complete = TransferReceipt(recordingID: job.recordingID, audioSHA256: job.audioSHA256!, nodeID: "synthetic", durable: true, markersSHA256: job.markersSHA256)
    try restored.acknowledge(complete)
    #expect(restored.state == .delivered)
}

@Test func markersTravelWithAudioAndRejectConflictingRetransmissions() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let source = RecordingLibrary(root: root.appendingPathComponent("source"))
    let receiver = RecordingLibrary(root: root.appendingPathComponent("receiver"))
    var record = Recording(trigger: "button")
    record.sourceDevice = .watch
    try record.mark(at: 0.2); try record.mark(at: 0.8)
    record.status = .saved; record.duration = 1
    try source.save(record)
    let format = try #require(AVAudioFormat(standardFormatWithSampleRate: 16000, channels: 1))
    let buffer = try #require(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 16000))
    buffer.frameLength = 16000
    for index in 0..<16000 { buffer.floatChannelData![0][index] = 0 }
    do { let file = try AVAudioFile(forWriting: source.audioURL(for: record), settings: format.settings); try file.write(from: buffer) }
    record.byteCount = Int64(try Data(contentsOf: source.audioURL(for: record)).count)
    try source.save(record)
    let output = try RecordingExport.prepare(id: record.id, from: source, to: root.appendingPathComponent("export"))
    let data = try Data(contentsOf: output.manifestURL)
    let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
    let manifest = try decoder.decode(RecordingExportManifest.self, from: data)
    #expect(manifest.schemaVersion == 2 && manifest.isSupported)
    let receipt = try RecordingIngress.accept(audio: output.audioURL, manifestData: data, into: receiver)
    #expect(receipt.markersSHA256 == record.markersSHA256)
    try receiver.rename(id: record.id, title: "Preserve local title")
    _ = try RecordingIngress.accept(audio: output.audioURL, manifestData: data, into: receiver)
    #expect(try receiver.recording(id: record.id).audioMarkers == record.audioMarkers)
    #expect(try receiver.recording(id: record.id).title == "Preserve local title")
    var changed = record; changed.markers = [RecordingMarker(offsetMilliseconds: 500)]
    let bad = RecordingExportManifest(schemaVersion: 2, recording: changed, audioFilename: record.audioFilename, audioSHA256: manifest.audioSHA256)
    let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
    #expect(throws: RecordingIngress.IngressError.self) {
        try RecordingIngress.accept(audio: output.audioURL, manifestData: encoder.encode(bad), into: receiver)
    }
    let exchange = FolderExchange(root: root.appendingPathComponent("exchange"))
    _ = try exchange.publish(output)
    _ = try exchange.publish(output)
    #expect(try exchange.hasPublication(record.id, audioSHA256: manifest.audioSHA256, markersSHA256: record.markersSHA256))
    #expect(throws: RecordingIngress.IngressError.self) { try exchange.hasPublication(record.id, audioSHA256: manifest.audioSHA256) }
    #expect(try exchange.restoreReceipt(receipt, from: receiver))
    #expect(try exchange.receipt(record.id)?.markersSHA256 == record.markersSHA256)
    try encoder.encode(bad).write(to: output.manifestURL)
    #expect(throws: RecordingIngress.IngressError.self) { try exchange.publish(output) }
    #expect(try receiver.recording(id: record.id).audioMarkers == record.audioMarkers)
    #expect(!RecordingExportManifest(schemaVersion: 1, recording: record, audioFilename: record.audioFilename, audioSHA256: manifest.audioSHA256).isSupported)
}
