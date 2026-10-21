import Foundation
import Testing
@testable import VoiceMemoryCore

@Test func repeatedSaveUpdatesOneRecordAndPreservesAudio() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let library = RecordingLibrary(root: root)
    var recording = Recording(trigger: "shortcut")
    try library.save(recording)
    let audio = Data([0, 1, 2, 3])
    try audio.write(to: library.audioURL(for: recording))
    recording.status = .saved
    recording.duration = 12.5
    recording.byteCount = 4
    try library.save(recording)
    let result = try library.load()
    #expect(result.recordings == [recording])
    #expect(try Data(contentsOf: library.audioURL(for: recording)) == audio)
}

@Test func interruptedRecordingSurvivesNewLibraryInstance() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let record = Recording(trigger: "shortcut")
    try RecordingLibrary(root: root).save(record)
    let reopened = try RecordingLibrary(root: root).load()
    #expect(reopened.recordings.first?.status == .recording)
    #expect(reopened.recordings.first?.id == record.id)
}

@Test func corruptManifestDoesNotHideOrOverwriteOtherRecordings() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let library = RecordingLibrary(root: root)
    let recording = Recording(trigger: "button")
    try library.save(recording)
    let invalid = root.appendingPathComponent("damaged.json")
    let original = Data("not-json".utf8)
    try original.write(to: invalid)
    let result = try library.load()
    #expect(result.recordings.count == 1)
    #expect(result.unreadable == ["damaged.json"])
    #expect(try Data(contentsOf: invalid) == original)
}

@Test func mismatchedIdentityIsNotAccepted() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let library = RecordingLibrary(root: root)
    let recording = Recording(trigger: "button")
    try library.save(recording)
    let fake = root.appendingPathComponent("\(UUID().uuidString).json")
    try JSONEncoder().encode(recording).write(to: fake)
    let result = try library.load()
    #expect(result.recordings.count == 1)
    #expect(result.unreadable.count == 1)
}

@Test func failedMetadataValidationPreservesLastSavedState() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let library = RecordingLibrary(root: root)
    var record = Recording(trigger: "button")
    try library.save(record)
    record.duration = -1
    #expect(throws: RecordingLibrary.LibraryError.self) { try library.save(record) }
    #expect(try library.load().recordings.first?.duration == 0)
}

@Test func overlappingAndDuplicateTriggersCannotToggleTwice() {
    var gate = TriggerGate()
    let start = Date(timeIntervalSince1970: 100)
    let first = gate.begin(at: start)
    let overlap = gate.begin(at: start.addingTimeInterval(5))
    #expect(first)
    #expect(!overlap)
    gate.complete(at: start.addingTimeInterval(5))
    let duplicate = gate.begin(at: start.addingTimeInterval(5.5))
    let subsequent = gate.begin(at: start.addingTimeInterval(7))
    #expect(!duplicate)
    #expect(subsequent)
}
