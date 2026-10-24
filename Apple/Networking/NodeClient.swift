import Foundation
import Security

struct NodeFailure: Error, LocalizedError {
    let message: String
    var statusCode: Int? = nil
    var errorDescription: String? { L(message) }
}

enum NodeEndpoint {
    static func validate(_ text: String) throws -> URL {
        guard let url = URL(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = url.scheme?.lowercased(), let host = url.host?.lowercased(),
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil else {
            throw NodeFailure(message: "请填写完整的节点地址。")
        }
        let parts = host.split(separator: ".").compactMap { Int($0) }
        let localIPv4 = parts.count == 4 && parts.allSatisfy { 0...255 ~= $0 }
            && (parts[0] == 127 || parts[0] == 10 || (parts[0] == 192 && parts[1] == 168)
                || (parts[0] == 172 && 16...31 ~= parts[1]))
        let local = localIPv4 || host == "localhost" || host.hasSuffix(".local")
            || host == "[::1]" || host == "::1" || host.hasPrefix("[fd") || host.hasPrefix("[fc")
        guard scheme == "https" || (scheme == "http" && local) else {
            throw NodeFailure(message: "远程节点请使用 HTTPS；本机和局域网地址可以使用 HTTP。")
        }
        return url
    }
}

enum ConnectionSecret {
    private static var service: String { (Bundle.main.bundleIdentifier ?? "VoiceMemory") + ".connection" }
    static func read() -> String? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                  kSecAttrService as String: service, kSecAttrAccount as String: "node",
                                  kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
    static func save(_ value: String) throws {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                  kSecAttrService as String: service, kSecAttrAccount as String: "node"]
        let data = Data(value.utf8)
        let result = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if result == errSecItemNotFound {
            var create = query
            create[kSecValueData as String] = data
            create[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            guard SecItemAdd(create as CFDictionary, nil) == errSecSuccess else {
                throw NodeFailure(message: "无法安全保存连接口令。")
            }
        } else if result != errSecSuccess { throw NodeFailure(message: "无法更新连接口令。") }
    }
    static func remove() {
        SecItemDelete([kSecClass as String: kSecClassGenericPassword,
                       kSecAttrService as String: service, kSecAttrAccount as String: "node"] as CFDictionary)
    }
}

struct NodeClient: Sendable {
    let endpoint: URL
    let token: String
    func request(_ path: String, method: String = "GET", query: [String: String] = [:]) -> URLRequest {
        var components = URLComponents(url: endpoint.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { components.queryItems = query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) } }
        var request = URLRequest(url: components.url!)
        request.httpMethod = method
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        request.timeoutInterval = 45
        return request
    }
    func read<T: Decodable & Sendable>(_ path: String, query: [String: String] = [:], timeout: TimeInterval = 45, as type: T.Type) async throws -> T {
        var readRequest = request(path, query: query)
        readRequest.timeoutInterval = timeout
        let (data, response) = try await URLSession.shared.data(for: readRequest)
        try Self.validate(data, response)
        return try JSONDecoder().decode(type, from: data)
    }
    @discardableResult
    func send(_ path: String, method: String = "PUT", body: [String: String]) async throws -> Data {
        try await sendData(path, method: method, body: JSONEncoder().encode(body))
    }
    @discardableResult
    func sendData(_ path: String, method: String, body: Data) async throws -> Data {
        var req = request(path, method: method)
        req.httpBody = body
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await URLSession.shared.data(for: req)
        try Self.validate(data, response)
        return data
    }
    func uploadRequest(_ output: RecordingExport) throws -> URLRequest {
        let data = try Data(contentsOf: output.manifestURL)
        let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
        let manifest = try decoder.decode(RecordingExportManifest.self, from: data)
        var req = request("v1/recordings/\(manifest.recording.id.uuidString)", method: "PUT")
        req.setValue(data.base64EncodedString(), forHTTPHeaderField: "X-Recording-Manifest")
        req.setValue("audio/x-caf", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 3600
        return req
    }
    func upload(_ output: RecordingExport) async throws -> TransferReceipt {
        let (data, response) = try await URLSession.shared.upload(for: uploadRequest(output), fromFile: output.audioURL)
        try Self.validate(data, response)
        return try JSONDecoder().decode(TransferReceipt.self, from: data)
    }
    func download(_ id: UUID, checksum: String) async throws -> URL {
        let (file, response) = try await URLSession.shared.download(for: request("v1/recordings/\(id.uuidString)/audio"))
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw NodeFailure(message: "暂时无法取得原音。")
        }
        let target = FileManager.default.temporaryDirectory.appendingPathComponent("node-\(UUID().uuidString).caf")
        try FileManager.default.moveItem(at: file, to: target)
        do {
            guard try AudioDigest.sha256(target) == checksum else { throw RecordingIngress.IngressError.checksum }
            return target
        } catch { try? FileManager.default.removeItem(at: target); throw error }
    }
    static func validate(_ data: Data, _ response: URLResponse) throws {
        guard let response = response as? HTTPURLResponse, 200..<300 ~= response.statusCode else {
            let message = (try? JSONDecoder().decode(ServerError.self, from: data).error) ?? "节点暂时无法完成操作。"
            throw NodeFailure(message: message, statusCode: (response as? HTTPURLResponse)?.statusCode)
        }
    }
    private struct ServerError: Decodable { let error: String }
}

extension Notification.Name {
    static let nodeConfigurationChanged = Notification.Name("VoiceMemory.nodeConfigurationChanged")
    static let nodeConnectionRestored = Notification.Name("VoiceMemory.nodeConnectionRestored")
}
