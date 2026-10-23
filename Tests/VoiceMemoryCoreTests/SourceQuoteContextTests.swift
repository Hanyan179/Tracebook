import XCTest
@testable import VoiceMemoryCore

final class SourceQuoteContextTests: XCTestCase {
    private func context(_ body: String, quote: String, start: Int) -> SourceQuoteContext? {
        guard let window = SourceQuoteContext.window(quote: quote, quoteStart: start) else { return nil }
        let scalars = Array(body.unicodeScalars)
        let page = String(String.UnicodeScalarView(scalars.dropFirst(window.offset).prefix(window.limit)))
        return SourceQuoteContext(text: page, offset: window.offset, totalCharacters: scalars.count,
                                  quote: quote, quoteStart: start)
    }

    func testUnicodeScalarOffsetsAndRepeatedQuoteLocateSecondOccurrence() throws {
        let quote = "cafe\u{301} 👩🏽‍💻 每周回顾"
        let prefix = quote + String(repeating: "🧑🏾‍🚀背景\n", count: 4000) + "第二处之前\n"
        let result = try XCTUnwrap(context(prefix + quote + "\n之后", quote: quote, start: prefix.unicodeScalars.count))
        XCTAssertTrue(result.before.hasSuffix("第二处之前\n"))
        XCTAssertEqual(result.quote, quote)
        XCTAssertEqual(result.after, "\n之后")
        XCTAssertTrue(result.hasEarlierText)
        XCTAssertFalse(result.hasLaterText)
        XCTAssertNil(context(prefix + quote, quote: quote, start: prefix.count))
    }

    func testBeginningAndEndKeepWhitespace() throws {
        let atStart = try XCTUnwrap(context("quote\n\n tail ", quote: "quote", start: 0))
        XCTAssertEqual(atStart.before, "")
        XCTAssertEqual(atStart.after, "\n\n tail ")
        XCTAssertFalse(atStart.hasEarlierText)
        let atEnd = try XCTUnwrap(context(" head\nquote", quote: "quote", start: 6))
        XCTAssertEqual(atEnd.before, " head\n")
        XCTAssertEqual(atEnd.after, "")
        XCTAssertFalse(atEnd.hasLaterText)
    }

    func testContextIsBoundedAndMarksOmittedText() throws {
        let result = try XCTUnwrap(context(String(repeating: "前", count: 200) + "引用" + String(repeating: "后", count: 200), quote: "引用", start: 200))
        XCTAssertEqual(result.before, String(repeating: "前", count: 160))
        XCTAssertEqual(result.after, String(repeating: "后", count: 160))
        XCTAssertTrue(result.hasEarlierText)
        XCTAssertTrue(result.hasLaterText)
    }

    func testRejectsChangedOrCanonicallyEquivalentTextAndWrongOccurrence() {
        XCTAssertNil(context("old quote", quote: "new quote", start: 0))
        // Swift String equality normalizes these; evidence must still match exact scalars.
        XCTAssertNil(context("café", quote: "cafe\u{301}", start: 0))
        XCTAssertNil(context("cafe\u{301}", quote: "café", start: 0))
        XCTAssertNil(context("quote later quote", quote: "quote", start: 7))
    }

    func testRejectsInvalidOffsetsOversizedQuotesAndIncompletePages() {
        XCTAssertNil(SourceQuoteContext.window(quote: "", quoteStart: 0))
        XCTAssertNil(SourceQuoteContext.window(quote: "quote", quoteStart: -1))
        XCTAssertNil(SourceQuoteContext.window(quote: "quote", quoteStart: Int.max))
        XCTAssertNil(SourceQuoteContext.window(quote: String(repeating: "a", count: 4001), quoteStart: 0))
        XCTAssertEqual(SourceQuoteContext.window(quote: String(repeating: "a", count: 4000), quoteStart: 200)?.limit, 4320)
        XCTAssertNil(SourceQuoteContext(text: "quote", offset: 1, totalCharacters: 6, quote: "quote", quoteStart: 1))
        XCTAssertNil(SourceQuoteContext(text: "quote", offset: 0, totalCharacters: 100, quote: "quote", quoteStart: 0))
        XCTAssertNil(SourceQuoteContext(text: "quote", offset: 0, totalCharacters: 4, quote: "quote", quoteStart: 0))
        XCTAssertNil(context("short", quote: "q", start: 100))
    }
}
