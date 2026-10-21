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
        let profile = preferences.profile(.summary)
        guard profile.mode == .apiKey, let secret = preferences.secret(.summary, profile), !secret.isEmpty else {
            try await client.sendData("v1/model-session", method: "POST", body: Data(#"{"clear":true}"#.utf8))
            await workspace.refresh(); return
        }
        let validated = try profile.validated()
        try await client.send("v1/model-session", method: "POST", body: ["endpoint": validated.endpoint, "model": validated.model, "key": secret])
        await workspace.refresh()
    }
}
