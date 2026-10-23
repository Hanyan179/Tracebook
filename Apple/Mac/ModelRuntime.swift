import Foundation

struct ModelRuntimeState: Decodable, Sendable {
    let configured: Bool
    let model: String
    let endpoint: String
    let automatic: Bool
}

@MainActor
enum ModelRuntime {
    static func activate(workspace: NodeWorkspaceStore) async throws {
        guard let client = workspace.client, ["localhost", "127.0.0.1", "::1", "[::1]"].contains(client.endpoint.host ?? "") else {
            throw NodeFailure(message: "请在本机资料库配置文字模型。")
        }
        let preferences = ModelPreferences()
        for role in ModelRole.allCases {
            let path = role == .summary ? "v1/model-session" : "v1/transcription-session"
            let profile = preferences.profile(role)
            if profile.mode == .apiKey, let secret = preferences.secret(role, profile), !secret.isEmpty {
                let validated = try profile.validated()
                try await client.send(path, method: "POST", body: ["endpoint": validated.endpoint, "model": validated.model, "key": secret])
            } else {
                try await client.sendData(path, method: "POST", body: Data(#"{"clear":true}"#.utf8))
            }
        }
        await workspace.refresh()
    }
}
