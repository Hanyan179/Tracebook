import Foundation
import XCTest
@testable import VoiceMemoryCore

final class LibraryMarkdownTests: XCTestCase {
    private let id = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"

    func testSourceLinkLabelRemainsInsideSentenceAndCodeStaysLiteral() {
        let markdown = LibraryMarkdown("我根据 [决定](../notes/\(id).md) 行动。`[示例](tracebook://source/note/\(id))`。")
        XCTAssertEqual(String(markdown.text.characters), "我根据 决定 行动。[示例](tracebook://source/note/\(id))。")
        XCTAssertEqual(markdown.references.map(\.label), ["决定"])
        XCTAssertEqual(markdown.references.first?.url.absoluteString, "tracebook://source/note/" + id)
        XCTAssertTrue(markdown.hasBody)
        XCTAssertTrue(markdown.text.runs.contains { $0.inlinePresentationIntent?.contains(.code) == true })
    }

    func testStyledLabelsEscapesAndRepeatedReferences() {
        let markdown = LibraryMarkdown("[**粗体**来源](notes/\(id).md) 和 [再次引用](tracebook://source/note/\(id.lowercased())) \\[示例](notes/\(id).md) 以及 **重点**")
        XCTAssertEqual(markdown.references.map(\.label), ["粗体来源"])
        XCTAssertTrue(String(markdown.text.characters).contains("粗体来源 和 再次引用 [示例](notes/"))
        XCTAssertTrue(markdown.text.runs.contains { $0.inlinePresentationIntent?.contains(.stronglyEmphasized) == true })
        XCTAssertFalse(markdown.text.runs.contains { $0.link != nil })
    }

    func testAllSupportedSourceKindsAndExternalLinks() {
        let sourceID = "12345678-1234-1234-1234-123456789ABC"
        let markdown = LibraryMarkdown("[录音](transcripts/\(id).md) [资料](documents/\(id).md) [输入](sources/codex/\(sourceID)/inputs/\(id).md) [网站](https://example.com)")
        XCTAssertEqual(markdown.references.compactMap { LibraryReference.identity($0.url)?.kind }, ["recording", "document", "input"])
        XCTAssertEqual(markdown.text.runs.compactMap(\.link).map(\.absoluteString), ["https://example.com"])
    }

    func testUnicodeScalarsSurviveParsingAndLinkExtraction() {
        let phrase = "cafe\u{301} 👩🏽‍💻 👨‍👩‍👧‍👦 🇨🇳 🏳️‍🌈 ❤️"
        let markdown = LibraryMarkdown(phrase + " [依据](notes/\(id).md)")
        XCTAssertEqual(Array(String(markdown.text.characters).unicodeScalars), Array((phrase + " 依据").unicodeScalars))
        XCTAssertEqual(markdown.references.count, 1)
    }

    func testRejectsNonLibraryPathsAndPreservesUnknownLinks() {
        for path in ["../../notes/\(id).md", "//host/notes/\(id).md", "https://host/notes/\(id).md", "file:///notes/\(id).md", "notes/not-an-id.md", "tracebook://source/credential/\(id)"] {
            XCTAssertNil(LibraryReference.identity(URL(string: path)!))
            XCTAssertTrue(LibraryMarkdown("[文字](\(path))").references.isEmpty)
        }
        XCTAssertEqual(LibraryReference.identity(URL(string: "../documents/\(id.lowercased()).md")!)?.kind, "document")
    }

    func testStandaloneSourceUsesOneLabelButSentencesKeepTheirText() {
        XCTAssertFalse(LibraryMarkdown("[来源](notes/\(id).md)").hasBody)
        XCTAssertTrue(LibraryMarkdown("来源：[原始资料](notes/\(id).md)").hasBody)
        XCTAssertTrue(LibraryMarkdown("`[来源](notes/\(id).md)`").hasBody)
        XCTAssertTrue(LibraryMarkdown("[网站](https://example.com)").hasBody)
    }
}
