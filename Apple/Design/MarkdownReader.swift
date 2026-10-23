import SwiftUI
#if !os(watchOS)

struct MarkdownReader: View {
    let text: String
    @Environment(\.openURL) private var openURL
    @ScaledMetric(relativeTo: .body) private var bodySize: CGFloat = 16
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
                    let linked = LibraryMarkdown(block.content)
                    if linked.hasBody {
                        Text(linked.text)
                            .font(font(block.level)).lineSpacing(block.level > 0 ? 3 : 7)
                            .padding(.top, block.level > 0 ? 8 : 0)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    if !linked.references.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(Array(linked.references.enumerated()), id: \.offset) { index, reference in
                                Button { openURL(reference.url) } label: {
                                    Label(reference.label.isEmpty || UUID(uuidString: reference.label) != nil
                                          ? (linked.references.count > 1 ? LF("来源 %d", index + 1) : L("查看来源"))
                                          : reference.label, systemImage: "doc.text.magnifyingglass")
                                        .frame(minHeight: 44, alignment: .leading).contentShape(Rectangle())
                                }.buttonStyle(.plain).font(.callout).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
        }.textSelection(.enabled)
    }
    private func font(_ level: Int) -> Font {
        switch level { case 1: .title2.weight(.semibold); case 2: .title3.weight(.semibold); case 3, 4: .headline; default: .system(size: bodySize) }
    }
}
#endif
