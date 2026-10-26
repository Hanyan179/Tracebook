import Foundation

struct RecordingRepairSource: Codable, Sendable {
    let audioSHA256: String
    let byteCount: Int64
    let markersSHA256: String?
    var snapshot: String?
}

struct RecordingRepairPreview: Decodable, Sendable {
    let recordingID: UUID
    let nodeID: String
    let snapshot: String
    let pendingRequestID: UUID?
    let title: String
    let byteCount: Int64
    let audioState: String
    let manifestState: String
    let keptFiles: Int
    let keptBytes: Int64
    let canRepair: Bool
    let audioSHA256: String
    let markersSHA256: String?
}

struct RecordingRepairStatus: Decodable, Sendable {
    let requestID: UUID
    let recordingID: UUID
    let nodeID: String
    let state: String
    let problem: String?
    let completedAt: String?
    let keptFiles: Int
    let keptBytes: Int64
    let archivePath: String?
}

struct RecordingRepairPlan: Sendable {
    let preview: RecordingRepairPreview
    let source: RecordingRepairSource
    let endpoint: URL
}

extension NodeClient {
    private func repairRead<T: Decodable>(_ path: String, receiver: String, source: RecordingRepairSource? = nil) async throws -> T {
        var req = request(path, method: source == nil ? "GET" : "POST")
        req.setValue(receiver, forHTTPHeaderField: "X-Recording-Node-ID")
        req.timeoutInterval = 120
        if let source {
            req.httpBody = try JSONEncoder().encode(source)
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await URLSession.shared.data(for: req)
        try Self.validate(data, response)
        return try JSONDecoder().decode(T.self, from: data)
    }
    func previewRecordingRepair(_ id: UUID, receiver: String, source: RecordingRepairSource) async throws -> RecordingRepairPreview {
        try await repairRead("v1/recordings/\(id.uuidString)/repair-preview", receiver: receiver, source: source)
    }
    func prepareRecordingRepair(_ id: UUID, requestID: UUID, receiver: String, source: RecordingRepairSource) async throws -> RecordingRepairStatus {
        try await repairRead("v1/recordings/\(id.uuidString)/repair/\(requestID.uuidString)", receiver: receiver, source: source)
    }
    func recordingRepairStatus(_ id: UUID, requestID: UUID, receiver: String) async throws -> RecordingRepairStatus {
        try await repairRead("v1/recordings/\(id.uuidString)/repair/\(requestID.uuidString)", receiver: receiver)
    }
    func recordingRepairUpload(_ id: UUID, requestID: UUID, receiver: String) -> URLRequest {
        var req = request("v1/recordings/\(id.uuidString)/repair/\(requestID.uuidString)/audio", method: "PUT")
        req.setValue(receiver, forHTTPHeaderField: "X-Recording-Node-ID")
        req.setValue("audio/x-caf", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 3600
        return req
    }
}
