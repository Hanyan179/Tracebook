import XCTest
@testable import VoiceMemoryCore

final class SourceQuoteMatchesTests: XCTestCase {
    func testRepeatedQuoteKeepsDistinctSpeakersAndScalarPositions() {
        let quote = "我每周五回顾工作。"
        let first = "同事说：\n" + quote + String(repeating: "背景\n", count: 100)
        let prefix = first + "本人 👩🏽‍💻 cafe\u{301}：\n"
        let result = SourceQuoteMatches(text: prefix + quote + "\n本人确认。", quote: quote)
        XCTAssertEqual(result.items.map(\.start), [5, prefix.unicodeScalars.count])
        XCTAssertFalse(result.hasMore)
        XCTAssertTrue(result.items[1].before.contains("本人 👩🏽‍💻"))
        XCTAssertEqual(result.items[1].after, "\n本人确认。")
        XCTAssertNotEqual(prefix.count, result.items[1].start)
    }

    func testMatchingDoesNotNormalizeAccentsOrWhitespace() {
        let body = "café\nthen cafe\u{301}\nline  two"
        XCTAssertEqual(SourceQuoteMatches(text: body, quote: "cafe\u{301}").items.map(\.start), [10])
        XCTAssertTrue(SourceQuoteMatches(text: body, quote: "line two").items.isEmpty)
        XCTAssertTrue(SourceQuoteMatches(text: "café", quote: "cafe\u{301}").items.isEmpty)
    }

    func testOverlappingAndEmojiMatches() {
        XCTAssertEqual(SourceQuoteMatches(text: "aaaa", quote: "aa").items.map(\.start), [0, 1, 2])
        XCTAssertEqual(SourceQuoteMatches(text: "👩🏽‍💻👩🏽‍💻", quote: "👩🏽‍💻").items.map(\.start), [0, 4])
    }

    func testResultsAreBoundedAndExplainTruncation() {
        let many = SourceQuoteMatches(text: String(repeating: "x ", count: 10000), quote: "x")
        XCTAssertEqual(many.items.count, 20)
        XCTAssertTrue(many.hasMore)
        XCTAssertEqual(many.items.last?.start, 38)
        XCTAssertTrue(many.items.allSatisfy { $0.before.count <= 80 && $0.after.count <= 80 })
        XCTAssertFalse(SourceQuoteMatches(text: String(repeating: "x ", count: 20), quote: "x").hasMore)
    }

    func testEmptyAndOversizedQuotesCannotBeSelected() {
        for quote in ["", " \n", String(repeating: "👩🏽‍💻", count: 1001)] {
            XCTAssertTrue(SourceQuoteMatches(text: quote, quote: quote).items.isEmpty)
        }
        let quote = String(repeating: "👩🏽‍💻", count: 1000)
        XCTAssertEqual(SourceQuoteMatches(text: quote, quote: quote).items.count, 1)
    }

    func testInitialExcerptRespectsScalarLimitAndWholeCharacters() {
        let text = String(repeating: "x", count: 3999) + "👩🏽‍💻tail"
        XCTAssertEqual(SourceQuoteMatches.initialQuote(in: text), String(repeating: "x", count: 3999))
        XCTAssertEqual(SourceQuoteMatches.initialQuote(in: String(repeating: "👩🏽‍💻", count: 1100)).unicodeScalars.count, 4000)
        XCTAssertEqual(SourceQuoteMatches.initialQuote(in: "cafe\u{301}"), "cafe\u{301}")
    }
}
