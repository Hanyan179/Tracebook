import SwiftUI
#if !os(watchOS)

struct MarkdownReader: View {
    let text: String
    @Environment(\.openURL) private var openURL
    private struct Block: Identifiable {
        let id: Int
        let content: String
        let level: Int
    }
    private var blocks: [Block] {
        var result: [Block] = []
        var paragraph: [String] = []
        var code: [String]? = nil
        func flush() {
            if !paragraph.isEmpty {
                result.append(Block(id: result.count, content: paragraph.joined(separator: "\n"), level: 0))
                paragraph = []
            }
        }
        for line in text.components(separatedBy: .newlines) {
            if line.hasPrefix("```") {
                flush()
                if let value = code {
                    result.append(Block(id: result.count, content: value.joined(separator: "\n"), level: -1)); code = nil
                } else { code = [] }
            } else if code != nil { code?.append(line) }
            else if line.trimmingCharacters(in: .whitespaces).isEmpty { flush() }
            else if line.hasPrefix("#") {
                flush()
                let count = line.prefix(while: { $0 == "#" }).count
                if line.dropFirst(count).hasPrefix(" ") {
                    result.append(Block(id: result.count, content: String(line.dropFirst(count + 1)), level: min(count, 4)))
                } else { paragraph.append(line) }
            } else { paragraph.append(line) }
        }
        flush()
        if let code { result.append(Block(id: result.count, content: code.joined(separator: "\n"), level: -1)) }
        return result
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            ForEach(blocks) { block in
                if block.level == -1 {
                    Text(verbatim: block.content).font(.system(.body, design: .monospaced))
                        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
                        .background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 10))
                } else {
                    let linked = linkedContent(block.content)
                    Text((try? AttributedString(markdown: linked.text,
                        options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(block.content))
                        .font(font(block.level)).lineSpacing(block.level > 0 ? 3 : 7)
                        .padding(.top, block.level > 0 ? 8 : 0)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if !linked.references.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(Array(linked.references.enumerated()), id: \.offset) { index, reference in
                                Button { openURL(reference.url) } label: {
                                    Label(linked.references.count > 1 && reference.label == L("查看来源")
                                          ? LF("来源 %d", index + 1) : reference.label, systemImage: "doc.text.magnifyingglass")
                                }.buttonStyle(.plain).font(.callout).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
        }.textSelection(.enabled)
    }
    private func linkedContent(_ content: String) -> (text: String, references: [(label: String, url: URL)]) {
        let content = readableLinks(content)
        guard let regex = try? NSRegularExpression(pattern: #"(?<!\\)\[([^\]]+)\]\((tracebook://source/(?:note|recording)/[0-9A-F-]{36})\)"#) else { return (content, []) }
        var body = content
        var references: [(label: String, url: URL)] = []
        for match in regex.matches(in: content, range: NSRange(content.startIndex..., in: content)).reversed() {
            guard let full = Range(match.range, in: body),
                  let label = Range(match.range(at: 1), in: content),
                  let address = Range(match.range(at: 2), in: content),
                  let url = URL(string: String(content[address])) else { continue }
            references.insert((String(content[label]), url), at: 0)
            body.removeSubrange(full)
        }
        if !references.isEmpty {
            body = body.components(separatedBy: .newlines).filter {
                let trimmed = $0.trimmingCharacters(in: .whitespaces)
                return !trimmed.hasPrefix("来源：") || !trimmed.dropFirst(3).trimmingCharacters(in: CharacterSet(charactersIn: " ·、")).isEmpty
            }.joined(separator: "\n")
        }
        return (body, references)
    }
    private func font(_ level: Int) -> Font {
        switch level { case 1: .title2.weight(.semibold); case 2: .title3.weight(.semibold); case 3, 4: .headline; default: .system(size: 16) }
    }
    private func readableLinks(_ content: String) -> String {
        guard let regex = try? NSRegularExpression(pattern: #"(?<!\\)\[([^\]]+)\]\((?:\.\./)?(notes|transcripts)/([0-9a-fA-F-]{36})\.md\)"#) else { return content }
        var value = content
        for match in regex.matches(in: content, range: NSRange(content.startIndex..., in: content)).reversed() {
            guard let full = Range(match.range, in: value),
                  let labelRange = Range(match.range(at: 1), in: content),
                  let folderRange = Range(match.range(at: 2), in: content),
                  let idRange = Range(match.range(at: 3), in: content),
                  let id = UUID(uuidString: String(content[idRange])) else { continue }
            let label = UUID(uuidString: String(content[labelRange])) == nil ? String(content[labelRange]) : L("查看来源")
            let kind = content[folderRange] == "notes" ? "note" : "recording"
            value.replaceSubrange(full, with: "[\(label)](tracebook://source/\(kind)/\(id.uuidString))")
        }
        return value
    }
}
#endif
