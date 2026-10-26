import Foundation
import PDFKit
import Vision
import Darwin

// Application-owned, bounded helper. Optional OCR runs on device through Vision.
// It never presents a PDF view, follows links, runs document actions or uses the network.
DispatchQueue.global().async {
    var byte: UInt8 = 0
    _ = read(STDIN_FILENO, &byte, 1)
    _exit(3)
}
let recognizeScans = CommandLine.arguments.count == 4 && CommandLine.arguments[3] == "--recognize-scans"
var cpu = rlimit(rlim_cur: recognizeScans ? 90 : 8, rlim_max: recognizeScans ? 90 : 8)
_ = setrlimit(RLIMIT_CPU, &cpu)
// Vision may compile its system model cache. The supervisor separately caps
// result.json at 8 MB; retaining the text-only 8 MB process limit kills OCR.
let fileLimit: rlim_t = recognizeScans ? 256 * 1024 * 1024 : 8 * 1024 * 1024
var outputSize = rlimit(rlim_cur: fileLimit, rlim_max: fileLimit)
_ = setrlimit(RLIMIT_FSIZE, &outputSize)

func extract(_ path: String) -> [String: Any] {
    guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
          !data.isEmpty, data.count <= 2 * 1024 * 1024,
          let document = PDFDocument(data: data) else { return ["error": "invalid_pdf"] }
    guard !document.isEncrypted && !document.isLocked else { return ["error": "encrypted"] }
    guard (1...200).contains(document.pageCount) else { return ["error": "page_limit"] }
    var pages: [[String: Any]] = []
    var bytes = 0
    var attempts = 0
    let deadline = ProcessInfo.processInfo.systemUptime + 45
    for index in 0..<document.pageCount {
        guard let page = document.page(at: index) else {
            pages.append(["number": index + 1, "status": "unreadable", "text": ""]);continue
        }
        var text = (page.string ?? "").replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        if text.contains("\0") {
            pages.append(["number": index + 1, "status": "unreadable", "text": ""]);continue
        }
        var result: [String: Any] = ["number": index + 1]
        if recognizeScans && text.isEmpty {
            if attempts >= 10 || ProcessInfo.processInfo.systemUptime >= deadline {
                result["ocrStatus"] = "limit"
            } else {
                attempts += 1
                let recognized = autoreleasepool { recognize(page) }
                text = recognized.text
                result["ocrStatus"] = recognized.status
                if !text.isEmpty { result["method"] = "ocr" }
            }
        }
        bytes += text.utf8.count
        if bytes > 2 * 1024 * 1024 { return ["error": "text_limit"] }
        result["status"] = result["ocrStatus"] as? String == "failed" ? "unreadable" : (text.isEmpty ? "empty" : "text")
        result["text"] = text
        pages.append(result)
    }
    var result: [String: Any] = ["pages": pages, "pageCount": document.pageCount, "parserWarnings": false, "parser": "apple-pdfkit"]
    if recognizeScans { result["ocrRequested"] = true; result["ocrEngine"] = "apple-vision-revision-3" }
    return result
}

func recognize(_ page: PDFPage) -> (text: String, status: String) {
    let bounds = page.bounds(for: .cropBox)
    guard bounds.width.isFinite, bounds.height.isFinite, bounds.width > 0, bounds.height > 0 else { return ("", "failed") }
    let scale = 1600 / max(bounds.width, bounds.height)
    let thumbnail = page.thumbnail(of: NSSize(width: max(1, bounds.width * scale), height: max(1, bounds.height * scale)), for: .cropBox)
    guard let image = thumbnail.cgImage(forProposedRect: nil, context: nil, hints: nil),
          image.width <= 3200, image.height <= 3200 else { return ("", "failed") }
    let request = VNRecognizeTextRequest()
    request.revision = VNRecognizeTextRequestRevision3
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    do {
        let supported = try request.supportedRecognitionLanguages()
        request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"].filter { supported.contains($0) }
        guard !request.recognitionLanguages.isEmpty else { return ("", "failed") }
        try VNImageRequestHandler(cgImage: image).perform([request])
        // Keep Vision's reading order. Never infer columns, tables, speakers or authors.
        let text = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
            .joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.contains("\0") else { return ("", "failed") }
        return (text, text.isEmpty ? "no_text" : "recognized")
    } catch { return ("", "failed") }
}

guard CommandLine.arguments.count == 3 || recognizeScans else { exit(2) }
let result = extract(CommandLine.arguments[1])
do {
    let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys, .withoutEscapingSlashes])
    try data.write(to: URL(fileURLWithPath: CommandLine.arguments[2]), options: .atomic)
} catch { exit(2) }
