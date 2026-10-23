/// A bounded source excerpt using the node's Unicode scalar (Python string) offsets.
/// Matching is exact: repeated text, normalization and grapheme clusters cannot move a quote.
public struct SourceQuoteContext: Equatable, Sendable {
    public let before: String
    public let quote: String
    public let after: String
    public let hasEarlierText: Bool
    public let hasLaterText: Bool

    public struct Window: Equatable, Sendable {
        public let offset: Int
        public let limit: Int
    }

    public static func window(quote: String, quoteStart: Int) -> Window? {
        let count = quote.unicodeScalars.count
        guard quoteStart >= 0, count > 0, count <= 4000,
              quoteStart <= Int.max - count - 160 else { return nil }
        let offset = max(0, quoteStart - 160)
        return Window(offset: offset, limit: quoteStart - offset + count + 160)
    }

    public init?(text: String, offset: Int, totalCharacters: Int, quote: String, quoteStart: Int) {
        let scalars = Array(text.unicodeScalars)
        let expected = Array(quote.unicodeScalars)
        guard let window = Self.window(quote: quote, quoteStart: quoteStart),
              offset == window.offset, totalCharacters >= offset,
              scalars.count == min(window.limit, totalCharacters - offset),
              quoteStart - offset <= scalars.count,
              expected.count <= scalars.count - (quoteStart - offset) else { return nil }
        let start = quoteStart - offset
        let end = start + expected.count
        guard scalars[start..<end].elementsEqual(expected) else { return nil }
        before = String(String.UnicodeScalarView(scalars[..<start]))
        self.quote = quote
        after = String(String.UnicodeScalarView(scalars[end...]))
        hasEarlierText = offset > 0
        hasLaterText = scalars.count < totalCharacters - offset
    }
}
