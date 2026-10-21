import AVFoundation
import Foundation

public enum AudioFileImporter {
    /// Copies decoded audio into an app-owned CAF; never changes the selected source.
    public static func importFile(_ url: URL, into library: RecordingLibrary,
                                  sourceDevice: RecordingDevice? = nil) throws -> Recording {
        var record = Recording(trigger: "import")
        record.sourceDevice = sourceDevice
        record.title = url.deletingPathExtension().lastPathComponent
        try library.save(record)
        do {
            let target = library.audioURL(for: record)
            try convert(url, to: target)
            // Reopen after the writer has closed and committed the container header.
            let audio = try AVAudioFile(forReading: target)
            guard audio.length > 0, audio.processingFormat.sampleRate > 0 else {
                throw ImportError.emptyAudio
            }
            record.duration = Double(audio.length) / audio.processingFormat.sampleRate
            record.byteCount = Int64((try target.resourceValues(forKeys: [.fileSizeKey])).fileSize ?? 0)
            record.status = .saved
            try library.save(record)
            return record
        } catch {
            record.status = .failed
            record.note = "导入未完成"
            try? library.save(record)
            throw error
        }
    }
    private static func convert(_ inputURL: URL, to outputURL: URL) throws {
        let input = try AVAudioFile(forReading: inputURL)
        let format = input.processingFormat
        let output = try AVAudioFile(forWriting: outputURL, settings: format.settings,
                                     commonFormat: format.commonFormat, interleaved: format.isInterleaved)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 16_384) else {
            throw ImportError.unsupportedFormat
        }
        while input.framePosition < input.length {
            try Task.checkCancellation()
            try input.read(into: buffer)
            guard buffer.frameLength > 0 else { throw ImportError.truncatedInput }
            try output.write(from: buffer)
        }
    }
    public enum ImportError: Error, LocalizedError {
        case emptyAudio, unsupportedFormat, truncatedInput
        public var errorDescription: String? {
            switch self {
            case .emptyAudio: "文件中没有有效音频。"
            case .unsupportedFormat: "暂时无法读取这种音频格式。"
            case .truncatedInput: "音频文件不完整，原文件未被修改。"
            }
        }
    }
}
