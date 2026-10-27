import XCTest
@testable import VoiceMemoryCore

final class PersonalNoteDraftCatalogTests: XCTestCase {
    let endpoint = "http://127.0.0.1:12345"
    let nodeID = "9BB3A1B9-94A1-4882-9D62-131C10D45266"
    func root() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }
    func draft(_ title: String) -> PersonalNoteDraft {
        var value = PersonalNoteDraft(); value.title = title; value.text = "Synthetic local body"; return value
    }
    func url(_ root: URL, _ draft: PersonalNoteDraft, library: String? = nil, legacy: Bool = false) -> URL {
        root.appendingPathComponent(PersonalNoteDraftCatalog.filename(endpoint: endpoint, nodeID: library ?? nodeID, noteID: legacy ? nil : draft.id))
    }
    func testMultipleDraftsAndPriorNewSlotAreScopedAndReadOnly() throws {
        let root = try root(); defer { try? FileManager.default.removeItem(at: root) }
        let a = draft("First"), b = draft("Second"), old = draft("Previous new slot"), foreign = draft("Other library")
        try a.write(to: url(root, a)); try b.write(to: url(root, b)); try old.write(to: url(root, old, legacy: true))
        try foreign.write(to: url(root, foreign, library: UUID().uuidString))
        let original = try Dictionary(uniqueKeysWithValues: FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .map { ($0.lastPathComponent, try Data(contentsOf: $0)) })
        let page = try PersonalNoteDraftCatalog.read(directory: root, endpoint: endpoint, nodeID: nodeID)
        XCTAssertEqual(Set(page.items.map(\.draft.id)), Set([a.id,b.id,old.id]))
        XCTAssertNil(page.next); XCTAssertEqual(page.unreadable, 0)
        for (name, data) in original { XCTAssertEqual(try Data(contentsOf: root.appendingPathComponent(name)), data) }
        XCTAssertTrue(try PersonalNoteDraftCatalog.read(directory: root, endpoint: endpoint + "/other", nodeID: nodeID).items.isEmpty)
    }
    func testBrokenFilesAndSymlinksDoNotDisappearSilentlyOrGetRewritten() throws {
        let root = try root(); defer { try? FileManager.default.removeItem(at: root) }
        let value = draft("Usable")
        try value.write(to: url(root, value))
        let broken = root.appendingPathComponent("broken.json"), link = root.appendingPathComponent("link.json")
        try Data("{broken".utf8).write(to: broken)
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: url(root, value))
        try Data("{\"text\":\"Other editor\",\"etag\":\"v1\"}".utf8).write(to: root.appendingPathComponent("other-editor.json"))
        let page = try PersonalNoteDraftCatalog.read(directory: root, endpoint: endpoint, nodeID: nodeID)
        XCTAssertEqual(page.items.count, 1); XCTAssertEqual(page.unreadable, 2)
        XCTAssertEqual(try Data(contentsOf: broken), Data("{broken".utf8))
        XCTAssertTrue(try link.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink == true)
    }
    func testContinuationIsBoundedAndInvalidatedByInventoryOrLibraryChange() throws {
        let root = try root(); defer { try? FileManager.default.removeItem(at: root) }
        for n in 0..<70 { let value = draft("Draft \(n)"); try value.write(to: url(root, value)) }
        let first = try PersonalNoteDraftCatalog.read(directory: root, endpoint: endpoint, nodeID: nodeID)
        XCTAssertEqual(first.items.count, 64)
        let next = try XCTUnwrap(first.next)
        let last = try PersonalNoteDraftCatalog.read(directory: root, endpoint: endpoint, nodeID: nodeID, after: next)
        XCTAssertEqual(last.items.count, 6); XCTAssertNil(last.next)
        XCTAssertEqual(Set((first.items+last.items).map(\.id)).count, 70)
        XCTAssertThrowsError(try PersonalNoteDraftCatalog.read(directory: root, endpoint: endpoint, nodeID: UUID().uuidString, after: next))
        let new = draft("Created meanwhile"); try new.write(to: url(root, new))
        XCTAssertThrowsError(try PersonalNoteDraftCatalog.read(directory: root, endpoint: endpoint, nodeID: nodeID, after: next))
    }
    func testReadBudgetAndDirectoryLinkAreEnforced() throws {
        let root = try root(); defer { try? FileManager.default.removeItem(at: root) }
        let data = try JSONSerialization.data(withJSONObject: ["text":String(repeating: "x", count: 3 * 1024 * 1024)])
        for n in 0..<3 { try data.write(to: root.appendingPathComponent("large-\(n).json")) }
        let page = try PersonalNoteDraftCatalog.read(directory: root, endpoint: endpoint, nodeID: nodeID)
        XCTAssertEqual(page.next?.offset, 2)
        let link = root.appendingPathComponent("directory-link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: root)
        XCTAssertThrowsError(try PersonalNoteDraftCatalog.read(directory: link, endpoint: endpoint, nodeID: nodeID))
    }
}
