import AVFoundation
import Foundation
import Testing
@testable import VoiceMemoryCore

@Test func importedAudioIsReadableAndOriginalIsUnchanged() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("synthetic-test.caf")
    try writeSyntheticAudio(to: source)
    let original = try Data(contentsOf: source)
    let library = RecordingLibrary(root: root.appendingPathComponent("library"))
    let record = try AudioFileImporter.importFile(source, into: library)
    let decoded = try AVAudioFile(forReading: library.audioURL(for: record))
    #expect(record.status == .saved)
    #expect(record.trigger == "import")
    #expect(record.duration == 2)
    #expect(decoded.length == 32_000)
    #expect(try Data(contentsOf: source) == original)
    #expect(try library.load().recordings.first?.title == "synthetic-test")
}

@Test func invalidAudioNeverBecomesASavedRecording() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let source = root.appendingPathComponent("broken.wav")
    let original = Data("this is not an audio file".utf8)
    try original.write(to: source)
    let library = RecordingLibrary(root: root.appendingPathComponent("library"))
    #expect(throws: (any Error).self) { try AudioFileImporter.importFile(source, into: library) }
    let result = try library.load()
    #expect(result.recordings.count == 1)
    #expect(result.recordings.first?.status == .failed)
    #expect(try Data(contentsOf: source) == original)
}

@Test func preexistingManifestWithoutTitleStillLoads() throws {
    var object = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(Recording(trigger: "button"))) as? [String: Any])
    object.removeValue(forKey: "title")
    let record = try JSONDecoder().decode(Recording.self, from: JSONSerialization.data(withJSONObject: object))
    #expect(record.title == nil)
}

private func writeSyntheticAudio(to url: URL) throws {
    let format = try #require(AVAudioFormat(standardFormatWithSampleRate: 16_000, channels: 1))
    let buffer = try #require(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 32_000))
    buffer.frameLength = 32_000
    let channel = try #require(buffer.floatChannelData?[0])
    for frame in 0..<32_000 { channel[frame] = Float(sin(Double(frame) * .pi * 2 * 440 / 16_000)) * 0.1 }
    let output = try AVAudioFile(forWriting: url, settings: format.settings)
    try output.write(from: buffer)
}
