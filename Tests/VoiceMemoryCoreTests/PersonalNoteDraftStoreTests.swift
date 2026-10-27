import Darwin
import XCTest
@testable import VoiceMemoryCore

final class PersonalNoteDraftStoreTests: XCTestCase {
    func root() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }
    func testTwoEditorsKeepIndependentRecoverableCopies() throws {
        let root = try root(); defer { try? FileManager.default.removeItem(at: root) }
        let endpoint = "http://127.0.0.1:12345", nodeID = UUID().uuidString
        func location(_ draft: PersonalNoteDraft) -> URL {
            root.appendingPathComponent(PersonalNoteDraftCatalog.filename(endpoint: endpoint, nodeID: nodeID, noteID: draft.storageID))
        }
        var first = PersonalNoteDraft(title: "Original", text: "Original body", etag: "v1")
        var second = first
        first.text = "Window one"; second.text = "Window two"
        let url = location(first)
        try PersonalNoteDraftStore.save(first, to: url, expecting: nil)
        XCTAssertThrowsError(try PersonalNoteDraftStore.save(second, to: url, expecting: nil)) {
            XCTAssertTrue($0 is PersonalNoteDraftStoreError)
        }
        let copy = second.conflictCopy()
        try PersonalNoteDraftStore.save(copy, to: location(copy), expecting: nil)
        let page = try PersonalNoteDraftCatalog.read(directory: root, endpoint: endpoint, nodeID: nodeID)
        XCTAssertEqual(page.items.count, 2)
        XCTAssertEqual(Set(page.items.map(\.draft.text)), Set(["Window one", "Window two"]))
        XCTAssertEqual(Set(page.items.map(\.draft.id)), [first.id])
        XCTAssertEqual(Set(page.items.map(\.draft.storageID)).count, 2)
        XCTAssertEqual(try PersonalNoteDraftStore.read(from: location(copy))?.draft.originalText, "Original body")
        XCTAssertEqual(try PersonalNoteDraftStore.read(from: location(copy))?.draft.etag, "v1")
        let independent = copy.newNoteCopy()
        XCTAssertNotEqual(independent.id, first.id); XCTAssertFalse(independent.isConflictCopy)
        XCTAssertTrue(independent.etag.isEmpty); XCTAssertTrue(independent.originalText.isEmpty)
        XCTAssertTrue(independent.needsRecovery); XCTAssertEqual(independent.text, second.text)
        XCTAssertNil(independent.submittedText)
        XCTAssertEqual(try FileManager.default.attributesOfItem(atPath: location(copy).path)[.posixPermissions] as? Int, 0o600)
    }
    func testLateSaveAndDiscardCannotOverwriteOrDeleteAnotherEditor() throws {
        let root = try root(); defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("draft.json")
        var draft = PersonalNoteDraft(); draft.text = "One"
        let initial = try PersonalNoteDraftStore.save(draft, to: url, expecting: nil)
        let stale = draft
        draft.text = "Two"
        let current = try PersonalNoteDraftStore.save(draft, to: url, expecting: initial)
        XCTAssertFalse(try PersonalNoteDraftStore.remove(from: url, expecting: initial))
        XCTAssertThrowsError(try PersonalNoteDraftStore.save(stale, to: url, expecting: initial))
        XCTAssertEqual(try PersonalNoteDraftStore.read(from: url)?.draft, draft)
        XCTAssertEqual(try PersonalNoteDraftStore.save(draft, to: url, expecting: initial), current)
        XCTAssertTrue(try PersonalNoteDraftStore.remove(from: url, expecting: current))
        XCTAssertThrowsError(try PersonalNoteDraftStore.save(stale, to: url, expecting: initial))
        XCTAssertNil(try PersonalNoteDraftStore.read(from: url))
    }
    func testLegacyFormatAndUnicodeBytesRemainDistinct() throws {
        let root = try root(); defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("draft.json")
        var old = PersonalNoteDraft(); old.text = "cafe\u{301}"; old.prepareSubmission()
        try old.write(to: url) // Earlier app format has no draftID.
        let initial = try XCTUnwrap(PersonalNoteDraftStore.read(from: url))
        XCTAssertFalse(initial.draft.isConflictCopy)
        var changed = old; changed.text = "café"
        let revision = try PersonalNoteDraftStore.save(changed, to: url, expecting: initial.revision)
        XCTAssertNotEqual(revision, initial.revision)
        XCTAssertThrowsError(try PersonalNoteDraftStore.save(old, to: url, expecting: initial.revision))
        let copy = old.conflictCopy()
        XCTAssertEqual(copy.submittedText?.utf8.map { $0 }, old.submittedText?.utf8.map { $0 })
    }
    func testCorruptFilesLinksAndBusyLockStayUntouched() throws {
        let root = try root(); defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("draft.json"), link = root.appendingPathComponent("link.json")
        let broken = Data("{unfinished".utf8); try broken.write(to: url)
        let draft = PersonalNoteDraft()
        XCTAssertThrowsError(try PersonalNoteDraftStore.save(draft, to: url, expecting: nil))
        XCTAssertThrowsError(try PersonalNoteDraftStore.remove(from: url, expecting: nil))
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: url)
        XCTAssertThrowsError(try PersonalNoteDraftStore.read(from: link))
        XCTAssertThrowsError(try PersonalNoteDraftStore.save(draft, to: link, expecting: nil))
        XCTAssertEqual(try Data(contentsOf: url), broken)
        let target = root.appendingPathComponent("fresh.json")
        try FileManager.default.createSymbolicLink(at: target.appendingPathExtension("lock"), withDestinationURL: url)
        XCTAssertThrowsError(try PersonalNoteDraftStore.save(draft, to: target, expecting: nil))
        XCTAssertEqual(try Data(contentsOf: url), broken)
        let busy = root.appendingPathComponent("busy.json")
        let fd = Darwin.open(busy.appendingPathExtension("lock").path, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        XCTAssertGreaterThanOrEqual(fd, 0); defer { flock(fd, LOCK_UN); Darwin.close(fd) }
        XCTAssertEqual(flock(fd, LOCK_EX | LOCK_NB), 0)
        XCTAssertThrowsError(try PersonalNoteDraftStore.save(draft, to: busy, expecting: nil)) {
            guard case PersonalNoteDraftStoreError.busy = $0 else { return XCTFail("Expected a busy lock") }
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: busy.path))
    }
    func testOversizeAndLinkedDirectoryNeverReplaceOriginal() throws {
        let root = try root(); defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("draft.json")
        var draft = PersonalNoteDraft(); draft.text = "Keep me"
        let revision = try PersonalNoteDraftStore.save(draft, to: url, expecting: nil)
        let original = try Data(contentsOf: url)
        draft.text = String(repeating: "x", count: 4 * 1024 * 1024)
        XCTAssertThrowsError(try PersonalNoteDraftStore.save(draft, to: url, expecting: revision))
        XCTAssertEqual(try Data(contentsOf: url), original)
        let directory = root.appendingPathComponent("linked")
        try FileManager.default.createSymbolicLink(at: directory, withDestinationURL: root)
        XCTAssertThrowsError(try PersonalNoteDraftStore.save(PersonalNoteDraft(), to: directory.appendingPathComponent("draft.json"), expecting: revision))
        XCTAssertEqual(try Data(contentsOf: url), original)
    }
    func testConcurrentWritersHaveExactlyOneWinner() async throws {
        let root = try root(); defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("draft.json")
        let outcomes = await withTaskGroup(of: Int.self, returning: [Int].self) { group in
            for n in 0..<24 {
                group.addTask {
                    var draft = PersonalNoteDraft(); draft.text = "Writer \(n)"
                    do { try PersonalNoteDraftStore.save(draft, to: url, expecting: nil); return 1 }
                    catch is PersonalNoteDraftStoreError { return 0 }
                    catch { return -1 }
                }
            }
            var result: [Int] = []; for await value in group { result.append(value) }; return result
        }
        XCTAssertEqual(outcomes.filter { $0 == 1 }.count, 1)
        XCTAssertFalse(outcomes.contains(-1)); XCTAssertEqual(outcomes.count, 24)
        XCTAssertNotNil(try PersonalNoteDraftStore.read(from: url))
    }
}
