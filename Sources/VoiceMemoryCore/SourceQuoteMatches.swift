import Foundation

/// Literal matches in the text already read by the user. Offsets use Unicode scalars,
/// matching the node contract, rather than UTF-16 or visible-character positions.
public struct SourceQuoteMatches: Sendable {
    public struct Match: Identifiable, Sendable {
        public let start: Int
        public let before: String
        public let after: String
        public var id: Int { start }
    }

    public let items: [Match]
    public let hasMore: Bool

    public init(text: String, quote: String) {
        guard !quote.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              quote.unicodeScalars.count <= 4000 else {
            items = []; hasMore = false; return
        }
        let source = text as NSString
        var found: [Match] = []
        var cursor = 0
        while cursor < source.length {
            let range = source.range(of: quote, options: .literal,
                                     range: NSRange(location: cursor, length: source.length - cursor))
            guard range.location != NSNotFound else { break }
            if found.count == 20 { items = found; hasMore = true; return }
            let before = source.substring(to: range.location)
            let after = source.substring(from: NSMaxRange(range))
            found.append(Match(start: before.unicodeScalars.count,
                               before: String(before.suffix(80)), after: String(after.prefix(80))))
            // Include overlapping occurrences without starting inside a surrogate pair.
            cursor = range.location + (quote.unicodeScalars.first!.value > 0xFFFF ? 2 : 1)
        }
        items = found; hasMore = false
    }

    /// Keep the initial excerpt within the server limit without splitting a visible character.
    public static func initialQuote(in text: String) -> String {
        var count = 0
        return String(text.prefix { character in
            count += character.unicodeScalars.count
            return count <= 4000
        })
    }
}
