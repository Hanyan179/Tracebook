import Foundation
import PDFKit
import Vision
import ImageIO
import Darwin

// Application-owned, bounded helper. Optional OCR runs on device through Vision.
// It never presents a PDF view, follows links, runs document actions or uses the network.
DispatchQueue.global().async {
    var byte: UInt8 = 0
    _ = read(STDIN_FILENO, &byte, 1)
    _exit(3)
}
let recognizeScans = CommandLine.arguments.count == 4 && CommandLine.arguments[3] == "--recognize-scans"
let recognizeImage = CommandLine.arguments.count == 4 && CommandLine.arguments[3] == "--recognize-image"
let usesVision = recognizeScans || recognizeImage
var cpu = rlimit(rlim_cur: usesVision ? 90 : 8, rlim_max: usesVision ? 90 : 8)
_ = setrlimit(RLIMIT_CPU, &cpu)
// Vision may compile its system model cache. The supervisor separately caps
// result.json at 8 MB; retaining the text-only 8 MB process limit kills OCR.
let fileLimit: rlim_t = usesVision ? 256 * 1024 * 1024 : 8 * 1024 * 1024
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
    guard let lines = recognizeLines(image) else { return ("", "failed") }
    let text = lines.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
    return (text, text.isEmpty ? "no_text" : "recognized")
}

func recognizeLines(_ image: CGImage) -> [String]? {
    let request = VNRecognizeTextRequest()
    request.revision = VNRecognizeTextRequestRevision3
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    do {
        let supported = try request.supportedRecognitionLanguages()
        request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"].filter { supported.contains($0) }
        guard !request.recognitionLanguages.isEmpty else { return nil }
        try VNImageRequestHandler(cgImage: image).perform([request])
        // Preserve Vision's line order; do not infer tables, authors or speakers.
        let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
        guard lines.allSatisfy({ !$0.contains("\0") }) else { return nil }
        return lines
    } catch { return nil }
}

func extractImage(_ path: String) -> [String: Any] {
    guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
          !data.isEmpty, data.count <= 8 * 1024 * 1024,
          let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
          CGImageSourceGetStatus(source) == .statusComplete,
          let type = CGImageSourceGetType(source) as String?,
          ["public.png", "public.jpeg", "public.heic", "public.heif"].contains(type)
    else { return ["error": "invalid_image"] }
    guard CGImageSourceGetCount(source) == 1 else { return ["error": "image_frames"] }
    guard let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let width = properties[kCGImagePropertyPixelWidth] as? Int,
          let height = properties[kCGImagePropertyPixelHeight] as? Int,
          width > 0, height > 0, width <= 16000, height <= 16000,
          width <= 64_000_000 / height else { return ["error": "image_dimensions"] }
    let orientation = properties[kCGImagePropertyOrientation] as? Int ?? 1
    guard (1...8).contains(orientation) else { return ["error": "invalid_image"] }
    let options: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceCreateThumbnailWithTransform: true, kCGImageSourceThumbnailMaxPixelSize: 2400,
        kCGImageSourceShouldCacheImmediately: true]
    guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary),
          image.width <= 2400, image.height <= 2400 else { return ["error": "invalid_image"] }
    guard let recognized = recognizeLines(image) else { return ["error": "image_ocr_failed"] }
    let lines = recognized.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
    guard lines.count <= 2000, lines.joined(separator: "\n").utf8.count <= 2 * 1024 * 1024 else { return ["error": "text_limit"] }
    return ["format": "image", "parser": "apple-vision-revision-3", "containerType": type,
            "width": width, "height": height, "orientation": orientation,
            "recognizedWidth": image.width, "recognizedHeight": image.height,
            "lines": lines, "status": lines.isEmpty ? "no_text" : "recognized"]
}

guard CommandLine.arguments.count == 3 || recognizeScans || recognizeImage else { exit(2) }
let result = recognizeImage ? extractImage(CommandLine.arguments[1]) : extract(CommandLine.arguments[1])
do {
    let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys, .withoutEscapingSlashes])
    try data.write(to: URL(fileURLWithPath: CommandLine.arguments[2]), options: .atomic)
} catch { exit(2) }
