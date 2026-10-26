// Synthetic raster pages for on-device OCR acceptance. No external assets.
import AppKit
import PDFKit
let document = PDFDocument()
for lines in [["Synthetic orchard review", "Meet on Friday at 10 AM."], ["合成资料核对", "每周五回顾项目。"], []] {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 900, pixelsHigh: 1200,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
        bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    NSColor.white.setFill(); NSRect(x: 0, y: 0, width: 900, height: 1200).fill()
    for (index, line) in lines.enumerated() {
        (line as NSString).draw(at: NSPoint(x: 60, y: 1000-index*100), withAttributes:
            [.font: NSFont.systemFont(ofSize: 44), .foregroundColor: NSColor.black])
    }
    NSGraphicsContext.restoreGraphicsState()
    let image = NSImage(size: NSSize(width: 900, height: 1200)); image.addRepresentation(bitmap)
    document.insert(PDFPage(image: image)!, at: document.pageCount)
}
try document.dataRepresentation()!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
