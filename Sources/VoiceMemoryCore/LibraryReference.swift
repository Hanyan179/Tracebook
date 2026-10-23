import Foundation

public enum LibraryReference {
    public static func identity(_ url: URL) -> (kind: String, id: UUID)? {
        let parts = url.path.split(separator: "/")
        if url.scheme == "tracebook", url.host == "source", parts.count == 2,
           ["note", "recording", "input", "document"].contains(String(parts[0])), let id = UUID(uuidString: String(parts[1])) {
            return (String(parts[0]), id)
        }
        guard url.scheme == nil, url.host == nil else { return nil }
        // Exported daily notes may reference the same library from one subdirectory.
        let relative = parts.first == ".." ? Array(parts.dropFirst()) : Array(parts)
        guard let filename = relative.last, filename.hasSuffix(".md"),
              let id = UUID(uuidString: String(filename.dropLast(3))) else { return nil }
        if relative.count == 2, let kind = ["notes": "note", "transcripts": "recording", "documents": "document"][String(relative[0])] {
            return (kind, id)
        }
        if relative.count == 5, relative[0] == "sources", relative[3] == "inputs", UUID(uuidString: String(relative[2])) != nil {
            return ("input", id)
        }
        return nil
    }
}

/// Uses the platform Markdown parser, so escapes and code spans never become source actions.
/// Link labels remain in the sentence; a separate native button provides an accessible target.
public struct LibraryMarkdown {
    public struct Reference {
        public let label: String
        public let url: URL
    }
    public let text: AttributedString
    public let references: [Reference]
    public var hasBody: Bool {
        references.count != 1 || String(text.characters).trimmingCharacters(in: .whitespacesAndNewlines) != references[0].label
    }

    public init(_ source: String) {
        var text = (try? AttributedString(markdown: source,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(source)
        var references: [Reference] = []
        var seen = Set<String>()
        for (url, range) in Array(text.runs[\.link]) {
            guard let url, let reference = LibraryReference.identity(url) else { continue }
            let identity = reference.kind + "/" + reference.id.uuidString
            if seen.insert(identity).inserted, let canonical = URL(string: "tracebook://source/" + identity) {
                references.append(Reference(label: String(text[range].characters), url: canonical))
            }
            text[range].link = nil
        }
        self.text = text
        self.references = references
    }
}
