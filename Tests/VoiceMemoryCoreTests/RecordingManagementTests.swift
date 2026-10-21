import AVFoundation
import CryptoKit
import Foundation
import Testing
@testable import VoiceMemoryCore

@Test func renameTrashAndRestorePreserveIdentityAndAudioAcrossRestart() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let library = RecordingLibrary(root: root)
    var record = Recording(trigger: "shortcut")
    record.status = .saved
    record.sourceDevice = .phone
    record.duration = 4
    try library.save(record)
    let original = Data([1, 2, 3, 4])
    try original.write(to: library.audioURL(for: record))
    try library.rename(id: record.id, title: "  一个想法  ")
    let firstTrash = Date(timeIntervalSince1970: 100)
    try library.moveToTrash(id: record.id, at: firstTrash)
    try library.moveToTrash(id: record.id, at: firstTrash.addingTimeInterval(5))
    let reopened = RecordingLibrary(root: root)
    let trashed = try reopened.recording(id: record.id)
    #expect(trashed.title == "一个想法")
    #expect(trashed.trashedAt == firstTrash)
    #expect(trashed.sourceDevice == .phone)
    try reopened.restore(id: record.id)
    let restored = try reopened.recording(id: record.id)
    #expect(restored.trashedAt == nil)
    #expect(restored.duration == record.duration)
    #expect(restored.startedAt == record.startedAt)
    #expect(try Data(contentsOf: reopened.audioURL(for: restored)) == original)
}

@Test func activeAndPausedRecordingsCannotBeRenamedOrTrashed() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let library = RecordingLibrary(root: root)
    for status in [RecordingStatus.recording, .paused] {
        var record = Recording(trigger: "button")
        record.status = status
        try library.save(record)
        #expect(throws: RecordingLibrary.LibraryError.self) { try library.rename(id: record.id, title: "changed") }
        #expect(throws: RecordingLibrary.LibraryError.self) { try library.moveToTrash(id: record.id) }
        #expect(try library.recording(id: record.id) == record)
    }
}

@Test func invalidRenameCannotReplaceSavedMetadata() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let library = RecordingLibrary(root: root)
    var record = Recording(trigger: "button")
    record.status = .saved
    record.title = "保留这个标题"
    try library.save(record)
    #expect(throws: RecordingLibrary.LibraryError.self) {
        try library.rename(id: record.id, title: String(repeating: "字", count: 121))
    }
    #expect(try library.recording(id: record.id).title == record.title)
    try library.rename(id: record.id, title: " \n ")
    #expect(try library.recording(id: record.id).title == nil)
}

@Test func oldManifestLoadsWithoutDeviceOrTrashMetadata() throws {
    var record = Recording(trigger: "button")
    record.status = .saved
    let original = try JSONEncoder().encode(record)
    let decoded = try JSONDecoder().decode(Recording.self, from: original)
    #expect(decoded.sourceDevice == nil)
    #expect(decoded.trashedAt == nil)
    #expect(decoded.id == record.id)
}

@Test func exportedAudioAndManifestAgreeWithoutChangingOriginal() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let library = RecordingLibrary(root: root.appendingPathComponent("recordings"))
    var record = Recording(trigger: "button")
    record.status = .saved
    record.duration = 1
    record.sourceDevice = .watch
    record.title = "会议 / 想法"
    try library.save(record)
    try writeExportFixture(to: library.audioURL(for: record))
    record.byteCount = Int64((try Data(contentsOf: library.audioURL(for: record))).count)
    try library.save(record)
    let original = try Data(contentsOf: library.audioURL(for: record))
    let destination = root.appendingPathComponent("export")
    let output = try RecordingExport.prepare(id: record.id, from: library, to: destination)
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .iso8601
    let manifest = try decoder.decode(RecordingExportManifest.self, from: Data(contentsOf: output.manifestURL))
    #expect(manifest.schemaVersion == 1)
    #expect(manifest.recording.id == record.id)
    #expect(manifest.recording.title == record.title)
    #expect(manifest.recording.sourceDevice == .watch)
    #expect(manifest.audioFilename == record.audioFilename)
    #expect(manifest.audioSHA256 == SHA256.hash(data: original).map { String(format: "%02x", $0) }.joined())
    #expect(try Data(contentsOf: output.audioURL) == original)
    #expect(try Data(contentsOf: library.audioURL(for: record)) == original)
    #expect(try AVAudioFile(forReading: output.audioURL).length == 16_000)
    #expect(throws: RecordingExport.ExportError.self) {
        try RecordingExport.prepare(id: record.id, from: library, to: destination)
    }
    #expect(try Data(contentsOf: output.audioURL) == original)
    try library.moveToTrash(id: record.id)
    #expect(throws: RecordingExport.ExportError.self) {
        try RecordingExport.prepare(id: record.id, from: library, to: root.appendingPathComponent("trashed-export"))
    }
}

private func writeExportFixture(to url: URL) throws {
    let format = try #require(AVAudioFormat(standardFormatWithSampleRate: 16_000, channels: 1))
    let buffer = try #require(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 16_000))
    buffer.frameLength = 16_000
    let channel = try #require(buffer.floatChannelData?[0])
    for frame in 0..<16_000 { channel[frame] = Float(sin(Double(frame) * .pi * 2 * 440 / 16_000)) * 0.1 }
    let output = try AVAudioFile(forWriting: url, settings: format.settings)
    try output.write(from: buffer)
}
