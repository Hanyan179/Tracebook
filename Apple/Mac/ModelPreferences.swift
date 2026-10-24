import SwiftUI
import Security
import CryptoKit

enum ModelRole: String, CaseIterable, Identifiable {
    case summary, transcription, answer
    var id: String { rawValue }
    var title: String {
        switch self { case .summary: "文字整理模型"; case .transcription: "语音识别模型"; case .answer: "资料回答模型" }
    }
    var path: String {
        switch self { case .summary: "v1/model-session"; case .transcription: "v1/transcription-session"; case .answer: "v1/answer-session" }
    }
    var symbol: String {
        switch self { case .summary: "text.badge.star"; case .transcription: "waveform"; case .answer: "text.bubble" }
    }
}

@MainActor
final class ModelPreferences: ObservableObject {
    @Published private(set) var profiles: [String: ModelConfiguration] = [:]
    @Published var problem: String?
    private let file: URL
    init() {
        let base = ProcessInfo.processInfo.environment["VOICE_MEMORY_CLIENT_DATA"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("VoiceMemory")
        file = base.appendingPathComponent("model-configuration.json")
        if FileManager.default.fileExists(atPath: file.path) {
            do { profiles = try JSONDecoder().decode([String: ModelConfiguration].self, from: Data(contentsOf: file)) }
            catch { problem = L("模型配置读取失败，原文件已保留。") }
        }
    }
    func profile(_ role: ModelRole) -> ModelConfiguration {
        if let saved = profiles[role.rawValue] { return saved }
        var initial = ModelConfiguration()
        if role != .summary { initial.mode = .unconfigured }
        return initial
    }
    func save(_ profile: ModelConfiguration, role: ModelRole, secret: String) throws {
        let validated = try profile.validated()
        if !secret.isEmpty {
            guard validated.mode == .apiKey else { throw NodeFailure(message: L("只有云端模型需要保存接口密钥。")) }
            try ModelCredential.save(secret, account: account(role, validated))
        }
        var next = profiles; next[role.rawValue] = validated
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(next).write(to: file, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        profiles = next; problem = nil
    }
    func secret(_ role: ModelRole, _ profile: ModelConfiguration) -> String? {
        ModelCredential.read(account: account(role, profile))
    }
    func hasSecret(_ role: ModelRole, _ profile: ModelConfiguration) -> Bool {
        ModelCredential.exists(account: account(role, profile))
    }
    func removeSecret(_ role: ModelRole, _ profile: ModelConfiguration) throws {
        try ModelCredential.remove(account: account(role, profile)); objectWillChange.send()
    }
    private func account(_ role: ModelRole, _ profile: ModelConfiguration) -> String {
        let endpoint = (try? profile.validated().endpoint) ?? profile.endpoint
        let hash = SHA256.hash(data: Data(endpoint.utf8)).map { String(format: "%02x", $0) }.joined()
        return role.rawValue + "." + hash
    }
}

private enum ModelCredential {
    static var service: String { (Bundle.main.bundleIdentifier ?? "VoiceMemory") + ".model-credentials" }
    static func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }
    static func read(account: String) -> String? {
        var q = query(account); q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
    static func exists(account: String) -> Bool {
        var q = query(account); q[kSecReturnAttributes as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        return SecItemCopyMatching(q as CFDictionary, nil) == errSecSuccess
    }
    static func save(_ secret: String, account: String) throws {
        let q = query(account)
        let update = SecItemUpdate(q as CFDictionary, [kSecValueData as String: Data(secret.utf8)] as CFDictionary)
        if update == errSecItemNotFound {
            var add = q; add[kSecValueData as String] = Data(secret.utf8)
            add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            guard SecItemAdd(add as CFDictionary, nil) == errSecSuccess else { throw NodeFailure(message: L("无法安全保存模型密钥。")) }
        } else if update != errSecSuccess { throw NodeFailure(message: L("无法更新模型密钥。")) }
    }
    static func remove(account: String) throws {
        let result = SecItemDelete(query(account) as CFDictionary)
        guard result == errSecSuccess || result == errSecItemNotFound else { throw NodeFailure(message: L("无法移除模型密钥。")) }
    }
}

struct ModelSettingsView: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @StateObject private var preferences = ModelPreferences()
    @AppStorage("model.focusRole") private var focusRole = ""
    var body: some View {
        ScrollViewReader { scroller in
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    SettingsPageTitle(title: "模型", subtitle: "语音识别、文字整理与资料回答分别配置。")
                    if let problem = preferences.problem { Text(problem).font(.callout).foregroundStyle(.secondary) }
                    ModelProfileEditor(workspace: workspace, preferences: preferences, role: .summary).id(ModelRole.summary.rawValue)
                    ModelProfileEditor(workspace: workspace, preferences: preferences, role: .transcription).id(ModelRole.transcription.rawValue)
                    ModelProfileEditor(workspace: workspace, preferences: preferences, role: .answer).id(ModelRole.answer.rawValue)
                    Label("密钥只保存在这台电脑的钥匙串，不进入提示词、记忆或导出文件。", systemImage: "lock")
                        .font(.caption).foregroundStyle(.secondary).lineSpacing(4)
                }.padding(28).frame(maxWidth: 860, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
            }
            .task(id: focusRole) {
                guard ModelRole(rawValue: focusRole) != nil else { return }
                await Task.yield()
                scroller.scrollTo(focusRole, anchor: .top)
                focusRole = ""
            }
        }
    }
}

private struct ModelProfileEditor: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @ObservedObject var preferences: ModelPreferences
    let role: ModelRole
    @State private var draft = ModelConfiguration()
    @State private var secret = ""
    @State private var result: String?
    @State private var remove = false
    @State private var saving = false
    @State private var speech: ModelRuntimeState?
    private var configured: Bool {
        switch role {
        case .summary: workspace.status?.analysisConfigured == true
        case .transcription: workspace.status?.transcriptionConfigured == true
        case .answer: workspace.status?.answerConfigured == true
        }
    }
    private var purpose: String {
        switch role {
        case .summary: "用于每日分析与所选资料的记忆建议。仅在明确生成时发送对应文字；密钥只交给本机处理进程，不写入资料文件。"
        case .transcription: "用于录音转写。保存后可在原音页面选择一条录音发送；自动转写需要另行开启。"
        case .answer: "用于对话中的资料回答。每次先选择并预览发送片段，回答保留原文引用；不自动发送历史对话或写入记忆。"
        }
    }
    private var savedMessage: String {
        guard configured else { return "参数已保存，仍需配置可用的服务与密钥。" }
        switch role {
        case .summary: return "配置已保存。可生成每日分析或所选资料的记忆建议。"
        case .transcription: return "语音配置已保存，尚未进行真实服务验证。可在原音页面转写。"
        case .answer: return "回答配置已保存，尚未进行真实服务验证。可在对话中选择片段并回答。"
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Label(LocalizedStringKey(role.title), systemImage: role.symbol)
                    .font(.headline)
                Spacer()
                Text(L(configured ? "模型已配置" : "等待配置")).font(.caption).foregroundStyle(.secondary)
            }
            Picker("处理方式", selection: $draft.mode) {
                Text("稍后配置").tag(ModelConfiguration.Mode.unconfigured)
                Text("自备密钥").tag(ModelConfiguration.Mode.apiKey)
                Text("官方服务").tag(ModelConfiguration.Mode.managed)
            }.pickerStyle(.segmented)
            if draft.mode == .apiKey {
                if role != .transcription {
                    HStack {
                        Text("快速填写").font(.caption).foregroundStyle(.secondary)
                        Button("DeepSeek") { draft.endpoint = "https://api.deepseek.com"; draft.model = "deepseek-flash" }
                        Button("OpenAI") { draft.endpoint = "https://api.openai.com/v1"; draft.model = "" }
                        Text("模型名称可以自行修改。").font(.caption).foregroundStyle(.secondary)
                    }.controlSize(.small)
                } else {
                    HStack {
                        Text("快速填写").font(.caption).foregroundStyle(.secondary)
                        Button("OpenAI") { draft.endpoint = "https://api.openai.com/v1"; draft.model = "gpt-transcribe" }
                        Text("也可填写兼容的语音服务。").font(.caption).foregroundStyle(.secondary)
                    }.controlSize(.small)
                }
                LabeledContent("服务地址") {
                    TextField("https://…/v1", text: $draft.endpoint)
                        .textFieldStyle(.roundedBorder).frame(maxWidth: 430)
                        .accessibilityIdentifier(role.rawValue + ".endpoint")
                }
                LabeledContent("模型名称") {
                    TextField("填写服务提供的模型名称", text: $draft.model).textFieldStyle(.roundedBorder).frame(maxWidth: 430)
                        .accessibilityIdentifier(role.rawValue + ".model")
                }
                if draft.mode == .apiKey {
                    LabeledContent("接口密钥") {
                        SecureField(preferences.hasSecret(role, draft) ? "已保存，留空保持不变" : "尚未保存", text: $secret)
                            .textFieldStyle(.roundedBorder).frame(maxWidth: 430)
                            .accessibilityIdentifier(role.rawValue + ".key")
                    }
                }
                Text(L(purpose))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if role == .transcription {
                Toggle("自动转写新接收的录音", isOn: Binding(get: { speech?.automatic ?? false }, set: { value in
                    Task {
                        saving = true; defer { saving = false }
                        do {
                            guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
                            let data = try await client.sendData("v1/transcription-settings", method: "PUT", body: JSONEncoder().encode(["automatic": value]))
                            speech = try JSONDecoder().decode(ModelRuntimeState.self, from: data)
                            await workspace.refresh()
                        } catch { result = error.localizedDescription }
                    }
                })).disabled(speech?.configured != true)
                Text("开启后，新接收的原音会发送到已保存的语音服务，可能产生费用。历史录音需逐条选择。关闭会暂停队列，正在发送的一段可能仍会完成。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if draft.mode == .managed {
                Text("官方服务尚未开放。你可以先使用自己的服务配置。").font(.callout).foregroundStyle(.secondary)
            }
            HStack {
                if let result { Text(L(result)).font(.caption).foregroundStyle(.secondary) }
                Spacer()
                if draft.mode == .apiKey && preferences.hasSecret(role, draft) {
                    Button("移除密钥", role: .destructive) { remove = true }
                }
                Button("检查格式") {
                    do { _ = try draft.validated(); result = draft.mode == .managed ? "官方服务尚未开放。" : draft.mode == .unconfigured ? "尚未选择模型。" : "格式有效，尚未进行联网验证。" }
                    catch { result = error.localizedDescription }
                }.accessibilityIdentifier(role.rawValue + ".check")
                Button("保存配置") {
                    saving = true
                    Task {
                        defer { saving = false }
                        do {
                            try preferences.save(draft, role: role, secret: secret); secret = ""
                            try await ModelRuntime.activate(workspace: workspace)
                            await loadSpeech()
                            result = savedMessage
                        } catch { result = error.localizedDescription }
                    }
                }.buttonStyle(FilledButtonStyle()).disabled(saving).accessibilityIdentifier(role.rawValue + ".save")
            }
        }.padding(22).background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 16)).disabled(saving)
            .onAppear { draft = preferences.profile(role) }
            .task { await loadSpeech() }
            .onChange(of: draft.endpoint) { _, _ in secret = ""; result = nil }
            .onChange(of: draft.mode) { _, _ in secret = ""; result = nil }
            .confirmationDialog("移除这项模型密钥？", isPresented: $remove) {
                Button("移除密钥", role: .destructive) {
                    Task {
                        do {
                            try preferences.removeSecret(role, draft)
                            try await ModelRuntime.activate(workspace: workspace)
                            await loadSpeech()
                            result = "密钥已移除。"
                        } catch { result = error.localizedDescription }
                    }
                }
            }
    }
    private func loadSpeech() async {
        guard role == .transcription else { return }
        do { speech = try await workspace.client?.read("v1/transcription-settings", as: ModelRuntimeState.self) }
        catch { result = error.localizedDescription }
    }
}
