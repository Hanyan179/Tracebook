import Foundation
import Testing
@testable import VoiceMemoryCore

@Test func directReceiptRequiresTheSelectedLibraryEvenWhenAudioMatches() throws {
    var job = TransferJob(recordingID: UUID(), target: "http://localhost:8765")
    job.audioSHA256 = String(repeating: "a", count: 64)
    job.receiverNodeID = "selected-library"
    let wrong = TransferReceipt(recordingID: job.recordingID, audioSHA256: job.audioSHA256!, nodeID: "previous-library", durable: true)
    #expect(throws: RecordingIngress.IngressError.self) { try job.acknowledge(wrong) }
    #expect(job.state != .delivered)
    let right = TransferReceipt(recordingID: job.recordingID, audioSHA256: job.audioSHA256!, nodeID: "selected-library", durable: true)
    try job.acknowledge(right)
    #expect(job.state == .delivered)
}

@Test func receiverBindingSurvivesQueueRestartWithoutChangingLegacyJobIdentity() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let journal = TransferJournal(root: root)
    var job = TransferJob(recordingID: UUID(), target: "http://localhost:8765")
    let legacyID = job.id
    try journal.save(job)
    #expect(try journal.load().first?.receiverNodeID == nil)
    job.receiverNodeID = "new-library"
    try journal.save(job)
    let saved = try #require(journal.load().first)
    #expect(saved.id == legacyID)
    #expect(saved.receiverNodeID == "new-library")
    #expect(try journal.load().count == 1)
}
