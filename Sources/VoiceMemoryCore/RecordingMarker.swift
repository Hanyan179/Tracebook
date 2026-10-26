import CryptoKit
import Foundation

/// A position in the original audio, excluding time spent paused.
public struct RecordingMarker: Codable, Identifiable, Equatable, Sendable {
    public let id: UUID
    public let offsetMilliseconds: Int64
    public var offset: TimeInterval { Double(offsetMilliseconds) / 1000 }
    public init(id: UUID = UUID(), offsetMilliseconds: Int64) {
        self.id = id
        self.offsetMilliseconds = offsetMilliseconds
    }
    public static let maximumCount = 1000
    public static let maximumOffset: Int64 = 31_536_000_000
    public static func valid(_ markers: [Self]) -> Bool {
        markers.count <= maximumCount && Set(markers.map(\.id)).count == markers.count
            && markers.allSatisfy { (0...maximumOffset).contains($0.offsetMilliseconds) }
            && zip(markers, markers.dropFirst()).allSatisfy { $0.offsetMilliseconds < $1.offsetMilliseconds }
    }
    public static func checksum(_ markers: [Self]) -> String? {
        guard !markers.isEmpty else { return nil }
        let text = markers.map { "\($0.id.uuidString):\($0.offsetMilliseconds)\n" }.joined()
        return SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}

extension Recording {
    public var audioMarkers: [RecordingMarker] { markers ?? [] }
    public var markersSHA256: String? { RecordingMarker.checksum(audioMarkers) }
    /// The caller persists this updated manifest before acknowledging the marker.
    @discardableResult
    public mutating func mark(at offset: TimeInterval) throws -> RecordingMarker {
        guard isInProgress, offset.isFinite, offset >= 0,
              offset * 1000 <= Double(RecordingMarker.maximumOffset), RecordingMarker.valid(audioMarkers) else {
            throw RecordingLibrary.LibraryError.invalidMetadata
        }
        let milliseconds = Int64((offset * 1000).rounded(.down))
        if let last = audioMarkers.last, last.offsetMilliseconds == milliseconds { return last }
        guard audioMarkers.count < RecordingMarker.maximumCount else { throw RecordingLibrary.LibraryError.tooManyMarkers }
        guard audioMarkers.last.map({ $0.offsetMilliseconds < milliseconds }) ?? true else {
            throw RecordingLibrary.LibraryError.invalidMetadata
        }
        let marker = RecordingMarker(offsetMilliseconds: milliseconds)
        markers = audioMarkers + [marker]
        duration = max(duration, offset)
        return marker
    }
}
