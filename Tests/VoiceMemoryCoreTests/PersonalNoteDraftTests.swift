import XCTest
@testable import VoiceMemoryCore

final class PersonalNoteDraftTests: XCTestCase {
    func testRestartRetainsIdentityBaselineAndUncertainSubmission() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("draft.json")
        var draft = PersonalNoteDraft(title: "Original", text: "Original body", etag: "old")
        draft.title = " Revised "; draft.text = "修订 👩🏽‍💻 cafe\u{301}"
        draft.prepareSubmission(); try draft.write(to: url)
        var restored = try XCTUnwrap(PersonalNoteDraft.read(from: url))
        XCTAssertEqual(restored.id, draft.id)
        XCTAssertEqual(restored.originalText, "Original body")
        XCTAssertTrue(restored.needsRecovery)
        let permissions = try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? NSNumber
        XCTAssertEqual(permissions?.intValue, 0o600)
        // Further local edits survive acknowledgement of the earlier attempt.
        restored.text += "\nMore local edits"
        XCTAssertTrue(restored.acknowledge(title: "Revised", text: draft.text, etag: "saved"))
        XCTAssertTrue(restored.hasChanges)
        XCTAssertEqual(restored.originalText, draft.text)
        XCTAssertTrue(restored.text.hasSuffix("More local edits"))
        XCTAssertEqual(restored.etag, "saved")
        XCTAssertNil(restored.submittedText)
        restored.prepareSubmission()
        XCTAssertTrue(restored.acknowledge(title: "Revised", text: restored.text, etag: "next"))
        XCTAssertFalse(restored.needsRecovery)
    }

    func testAcknowledgementRequiresExactSubmittedBytes() {
        var draft = PersonalNoteDraft()
        draft.title = "New"; draft.text = "cafe\u{301}"; draft.prepareSubmission()
        var normalized = draft; normalized.text = "café"
        XCTAssertNotEqual(draft, normalized)
        XCTAssertFalse(draft.acknowledge(title: "New", text: "café", etag: "wrong"))
        XCTAssertFalse(draft.acknowledge(title: "Other", text: draft.text, etag: "wrong"))
        XCTAssertTrue(draft.etag.isEmpty)
        XCTAssertNotNil(draft.submittedText)
        XCTAssertTrue(draft.acknowledge(title: "New", text: draft.text, etag: "right"))
        XCTAssertFalse(draft.needsRecovery)
    }

    func testUnreadableAndLinkedDraftsArePreserved() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let url = root.appendingPathComponent("draft.json")
        XCTAssertNil(try PersonalNoteDraft.read(from: url))
        let invalid = Data("unfinished-json".utf8)
        try invalid.write(to: url)
        XCTAssertThrowsError(try PersonalNoteDraft.read(from: url))
        XCTAssertEqual(try Data(contentsOf: url), invalid)
        let link = root.appendingPathComponent("link.json")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: url)
        XCTAssertThrowsError(try PersonalNoteDraft.read(from: link))
        XCTAssertEqual(try Data(contentsOf: url), invalid)
        try Data(repeating: 0, count: 4 * 1024 * 1024 + 1).write(to: url)
        XCTAssertThrowsError(try PersonalNoteDraft.read(from: url))
    }
}
