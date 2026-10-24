import Foundation
import PDFKit
import Darwin

// This command-line helper only extracts text. It never presents a PDF view,
// follows links, runs document actions, performs OCR, or uses the network.
DispatchQueue.global().async {
    var byte: UInt8 = 0
    _ = read(STDIN_FILENO, &byte, 1)
    _exit(3)
}
var cpu = rlimit(rlim_cur: 8, rlim_max: 8)
_ = setrlimit(RLIMIT_CPU, &cpu)
var outputSize = rlimit(rlim_cur: 8 * 1024 * 1024, rlim_max: 8 * 1024 * 1024)
_ = setrlimit(RLIMIT_FSIZE, &outputSize)

func extract(_ path: String) -> [String: Any] {
    guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
          !data.isEmpty, data.count <= 2 * 1024 * 1024,
          let document = PDFDocument(data: data) else { return ["error": "invalid_pdf"] }
    guard !document.isEncrypted && !document.isLocked else { return ["error": "encrypted"] }
    guard (1...200).contains(document.pageCount) else { return ["error": "page_limit"] }
    var pages: [[String: Any]] = []
    var bytes = 0
    for index in 0..<document.pageCount {
        guard let page = document.page(at: index) else {
            pages.append(["number": index + 1, "status": "unreadable", "text": ""]);continue
        }
        let text = (page.string ?? "").replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        if text.contains("\0") {
            pages.append(["number": index + 1, "status": "unreadable", "text": ""]);continue
        }
        bytes += text.utf8.count
        if bytes > 2 * 1024 * 1024 { return ["error": "text_limit"] }
        pages.append(["number": index + 1, "status": text.isEmpty ? "empty" : "text", "text": text])
    }
    return ["pages": pages, "pageCount": document.pageCount, "parserWarnings": false, "parser": "apple-pdfkit"]
}

guard CommandLine.arguments.count == 3 else { exit(2) }
let result = extract(CommandLine.arguments[1])
do {
    let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys, .withoutEscapingSlashes])
    try data.write(to: URL(fileURLWithPath: CommandLine.arguments[2]), options: .atomic)
} catch { exit(2) }
