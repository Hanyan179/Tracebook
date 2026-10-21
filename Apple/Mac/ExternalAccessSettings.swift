import AppKit
import SwiftUI

private struct ReaderGrant: Decodable, Identifiable, Sendable {
    let id: String
    let name: String
    let scopes: [String]
    let expires_at: String
    let revoked_at: String?
    let last_used_at: String?
    var token: String?
    var expired: Bool { (ISO8601DateFormatter().date(from: expires_at) ?? .distantPast) < .now }
}
private let readerScopes = [("notes", "笔记"), ("transcripts", "原音文字"), ("daily", "每日记录"), ("analyses", "AI 每日分析"), ("confirmedMemory", "已确认的记忆"), ("documents", "导入资料")]

struct ExternalAccessSettings: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @State private var grants: [ReaderGrant] = []
    @State private var creating = false
    @State private var revoking: ReaderGrant?
    @State private var busy = false
    @State private var error: String?
    var body: some View {
        Form {
            Section { SettingsPageTitle(title: "外部接入", subtitle: "让其他 AI 在你授权的范围内，读取与查询资料。") }
            Section("连接方式") {
                LabeledContent("资料库地址") { Text(verbatim: workspace.endpoint).textSelection(.enabled) }
                Text("本机地址只供这台电脑使用。远程接入需要你单独配置安全网络，应用不会自动开放公网。")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("导出接入包") { exportPackage() }
                    Button("查看接入说明") {
                        if let url = Bundle.main.url(forResource: "lifto-library", withExtension: nil) {
                            NSWorkspace.shared.open(url.appendingPathComponent("SKILL.md"))
                        }
                    }
                }
            }
            Section {
                HStack {
                    Text("已授权的应用").font(.headline)
                    Spacer()
                    Button("添加接入", systemImage: "plus") { creating = true }.disabled(!workspace.connected || busy)
                }
                if grants.isEmpty { Text("尚未授权任何外部应用。").foregroundStyle(.secondary).padding(.vertical, 8) }
                ForEach(grants) { grant in
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            Text(verbatim: grant.name).font(.headline)
                            Spacer()
                            if grant.revoked_at != nil { Text("已撤销").foregroundStyle(.secondary) }
                            else if grant.expired { Text("已过期").foregroundStyle(.secondary) }
                            else { Button("撤销接入") { revoking = grant }.disabled(busy) }
                        }
                        Text(grant.scopes.map { scope in L(readerScopes.first { $0.0 == scope }?.1 ?? scope) }.joined(separator: " · "))
                            .font(.caption).foregroundStyle(.secondary)
                        LabeledContent("有效期至", value: readable(grant.expires_at)).font(.caption).foregroundStyle(.secondary)
                        if let last = grant.last_used_at { LabeledContent("最近访问", value: readable(last)).font(.caption).foregroundStyle(.secondary) }
                    }.padding(.vertical, 8)
                }
            }
            Section("访问边界") {
                Label("只读接入不能修改资料、读取原音或访问模型密钥。", systemImage: "lock")
                Label("未确认和已归档的记忆不会提供给外部应用。", systemImage: "checkmark.shield")
            }
            if let error { Section { Text(L(error)).font(.caption).foregroundStyle(.secondary); Button("重新读取") { Task { await load() } } } }
        }.formStyle(.grouped).task { await load() }
            .sheet(isPresented: $creating, onDismiss: { Task { await load() } }) { CreateReaderGrant(workspace: workspace).appLanguage() }
            .confirmationDialog("撤销这个接入？", isPresented: Binding(get: { revoking != nil }, set: { if !$0 { revoking = nil } }), titleVisibility: .visible) {
                if let grant = revoking { Button("撤销接入", role: .destructive) { Task { await revoke(grant) } } }
            } message: { Text("该应用的读取凭证将立即失效。") }
    }
    private func readable(_ value: String) -> String {
        guard let date = ISO8601DateFormatter().date(from: value) else { return value }
        return date.formatted(.dateTime.year().month().day().hour().minute().locale(AppLanguage.current.locale))
    }
    private func load() async {
        do {
            guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
            grants = try await client.read("v1/integrations", as: [ReaderGrant].self); error = nil
        } catch { self.error = error.localizedDescription }
    }
    private func revoke(_ grant: ReaderGrant) async {
        guard let client = workspace.client else { return }
        busy = true; defer { busy = false }
        do {
            try await client.sendData("v1/integrations/" + grant.id, method: "PUT", body: Data(#"{"revoked":true}"#.utf8))
            revoking = nil; await load()
        } catch { self.error = error.localizedDescription }
    }
    private func exportPackage() {
        guard let source = Bundle.main.url(forResource: "lifto-library", withExtension: nil) else { error = "接入包不可用。"; return }
        let panel = NSOpenPanel(); panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.canCreateDirectories = true
        panel.prompt = L("导出接入包")
        guard panel.runModal() == .OK, let target = panel.url else { return }
        do { try FileManager.default.copyItem(at: source, to: target.appendingPathComponent("lifto-library")) }
        catch { self.error = error.localizedDescription }
    }
}

private struct CreateReaderGrant: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var selected: Set<String> = ["notes"]
    @State private var days = 7
    @State private var grant: ReaderGrant?
    @State private var busy = false
    @State private var error: String?
    @State private var copied = false
    var body: some View {
        NavigationStack {
            Form {
                if let grant, let token = grant.token {
                    Section("读取凭证已创建") {
                        Text("凭证只显示这一次，请保存在接入工具的密钥设置中。").font(.callout)
                        SecureField("读取凭证", text: .constant(token)).textSelection(.disabled)
                        Button(copied ? L("已复制") : L("复制读取凭证")) {
                            NSPasteboard.general.clearContents(); NSPasteboard.general.setString(token, forType: .string); copied = true
                        }
                        Text("不要把凭证放进对话、提示词或分享的文件。").font(.caption).foregroundStyle(.secondary)
                    }
                } else {
                    Section("应用名称") { TextField("例如：我的 AI 助手", text: $name) }
                    Section("允许读取") {
                        ForEach(readerScopes, id: \.0) { scope in
                            Toggle(L(scope.1), isOn: Binding(get: { selected.contains(scope.0) }, set: { if $0 { selected.insert(scope.0) } else { selected.remove(scope.0) } }))
                        }
                    }
                    if selected.contains("daily") {
                        Text("每日记录可能包含来源原文，授权该项即允许读取整篇内容。").font(.caption).foregroundStyle(.secondary)
                    }
                    Section("有效期") {
                        Picker("有效期", selection: $days) { Text("7 天").tag(7); Text("30 天").tag(30); Text("90 天").tag(90) }.pickerStyle(.segmented).labelsHidden()
                    }
                    Text("所选资料会允许接入工具读取。你可以随时撤销。").font(.caption).foregroundStyle(.secondary)
                }
                if let error { Text(L(error)).font(.caption).foregroundStyle(.secondary) }
            }.formStyle(.grouped).disabled(busy)
                .navigationTitle(L("添加接入"))
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button(grant == nil ? L("取消") : L("完成")) { dismiss() }.disabled(busy) }
                    if grant == nil {
                        ToolbarItem(placement: .confirmationAction) { Button("创建读取凭证") { Task { await create() } }.disabled(busy || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || selected.isEmpty) }
                    }
                }
        }.frame(width: 560, height: 590).interactiveDismissDisabled(busy || grant != nil)
    }
    private func create() async {
        guard let client = workspace.client else { return }
        busy = true; defer { busy = false }
        do {
            struct Body: Encodable { let name: String; let scopes: [String]; let days: Int }
            let data = try await client.sendData("v1/integrations", method: "POST", body: JSONEncoder().encode(Body(name: name, scopes: selected.sorted(), days: days)))
            grant = try JSONDecoder().decode(ReaderGrant.self, from: data); error = nil
        } catch { self.error = error.localizedDescription }
    }
}
