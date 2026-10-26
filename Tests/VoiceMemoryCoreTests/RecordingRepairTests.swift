import Foundation
import Testing
@testable import VoiceMemoryCore

@Test func repairIntentSurvivesRestartAndOnlyMatchingReceiptCompletesIt() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let journal = TransferJournal(root: root)
    var job = TransferJob(recordingID: UUID(), target: "http://localhost:8765")
    job.bindReceiver("approved-library")
    job.audioSHA256 = String(repeating: "a", count: 64)
    job.repairRequestID = UUID(); job.repairSnapshot = String(repeating: "b", count: 64)
    let operation = job.repairRequestID
    try journal.save(job)
    var restarted = try #require(journal.load().first)
    #expect(restarted.repairRequestID == operation && restarted.repairSnapshot == job.repairSnapshot)
    let wrong = TransferReceipt(recordingID: job.recordingID, audioSHA256: job.audioSHA256!, nodeID: "other-library", durable: true, repairRequestID: operation)
    #expect(throws: RecordingIngress.IngressError.self) { try restarted.acknowledge(wrong) }
    #expect(restarted.repairRequestID == operation)
    let correct = TransferReceipt(recordingID: job.recordingID, audioSHA256: job.audioSHA256!, nodeID: "approved-library", durable: true, repairRequestID: operation)
    try restarted.acknowledge(correct);try journal.save(restarted)
    #expect(restarted.repairRequestID == nil && restarted.repairSnapshot == nil)
    #expect(try journal.load().first?.lastRepairRequestID == operation)
}

@Test func receiverSwitchNeverReplaysAnotherLibraryRepairAndNormalReceiptDoesNotInventHistory() throws {
    var job = TransferJob(recordingID: UUID(), target: "http://localhost:8765")
    job.bindReceiver("first")
    job.audioSHA256 = String(repeating: "a", count: 64)
    job.repairRequestID = UUID();job.repairSnapshot = String(repeating: "b", count: 64)
    job.bindReceiver("second")
    #expect(job.repairRequestID == nil && job.repairSnapshot == nil && job.lastRepairRequestID == nil)
    job.repairRequestID = UUID();job.repairSnapshot = String(repeating: "b", count: 64)
    let alreadyHealthy = TransferReceipt(recordingID: job.recordingID, audioSHA256: job.audioSHA256!, nodeID: "second", durable: true)
    try job.acknowledge(alreadyHealthy)
    #expect(job.state == .delivered && job.repairRequestID == nil && job.lastRepairRequestID == nil)
}
