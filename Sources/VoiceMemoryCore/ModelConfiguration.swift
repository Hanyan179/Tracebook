import Foundation

public struct ModelConfiguration: Codable, Equatable, Sendable {
    public enum Mode: String, Codable, CaseIterable, Sendable { case unconfigured, apiKey, managed }
    public var mode: Mode = .apiKey
    public var endpoint = ""
    public var model = ""
    public init() {}
    public func validated() throws -> Self {
        var value = self
        value.endpoint = endpoint.trimmingCharacters(in: .whitespacesAndNewlines)
        value.model = model.trimmingCharacters(in: .whitespacesAndNewlines)
        guard mode == .apiKey else {
            value.endpoint = ""; value.model = ""
            return value
        }
        guard !value.model.isEmpty, value.model.count <= 200 else { throw ValidationError.model }
        guard let url = URL(string: value.endpoint), let host = url.host?.lowercased(),
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              ["http", "https"].contains(url.scheme?.lowercased() ?? "") else { throw ValidationError.endpoint }
        guard !host.isEmpty, url.scheme?.lowercased() == "https" else { throw ValidationError.transport }
        value.endpoint = value.endpoint.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        return value
    }
    public enum ValidationError: Error, LocalizedError {
        case model, endpoint, transport
        public var errorDescription: String? {
            switch self {
            case .model: "请填写模型名称。"
            case .endpoint: "请填写有效的服务地址，地址中不能包含密钥。"
            case .transport: "模型服务请使用 HTTPS 地址。"
            }
        }
    }
}
