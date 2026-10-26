// Synthetic image originals for real on-device OCR; no personal media.
import AppKit
import ImageIO
let root = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
func picture(_ lines: [String]) -> CGImage {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 900, pixelsHigh: 1200,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
        bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState();NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    NSColor.white.setFill();NSRect(x: 0, y: 0, width: 900, height: 1200).fill()
    for (i, line) in lines.enumerated() {
        (line as NSString).draw(at: NSPoint(x: 45, y: 1020-i*100), withAttributes:
            [.font: NSFont.systemFont(ofSize: 40), .foregroundColor: NSColor.black])
    }
    NSGraphicsContext.restoreGraphicsState();return bitmap.cgImage!
}
func write(_ name: String, _ type: String, _ images: [CGImage], orientation: Int = 1) throws {
    guard let out = CGImageDestinationCreateWithURL(root.appendingPathComponent(name) as CFURL, type as CFString, images.count, nil) else { fatalError("Cannot create synthetic image") }
    for image in images { CGImageDestinationAddImage(out, image, [kCGImagePropertyOrientation: orientation] as CFDictionary) }
    guard CGImageDestinationFinalize(out) else { fatalError("Cannot finalize synthetic image") }
}
let english = picture(["Synthetic orchard review", "I review projects every Friday."])
let chinese = picture(["合成资料核对", "每周五回顾项目。"])
try write("orchard.png", "public.png", [english])
try write("orchard.jpg", "public.jpeg", [english])
try write("chinese.heic", "public.heic", [chinese])
try write("blank.png", "public.png", [picture([])])
let rotated = CGContext(data: nil, width: 1200, height: 900, bitsPerComponent: 8, bytesPerRow: 0,
    space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
rotated.translateBy(x: 1200, y: 0);rotated.rotate(by: .pi/2)
rotated.draw(english, in: CGRect(x: 0, y: 0, width: 900, height: 1200))
try write("rotated.jpg", "public.jpeg", [rotated.makeImage()!], orientation: 6)
try write("two-images.heic", "public.heic", [english, chinese])
