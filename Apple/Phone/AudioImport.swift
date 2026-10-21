import Foundation

extension CaptureRecorder {
    func importAudio(from url: URL) async {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let library = library
        do {
            _ = try await Task.detached(priority: .userInitiated) {
                try AudioFileImporter.importFile(url, into: library, sourceDevice: .phone)
            }.value
            reload()
        } catch { problem = "无法导入这段音频：\(error.localizedDescription)" }
    }
}
