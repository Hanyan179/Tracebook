import SwiftUI
import AppKit

struct SettingsPageTitle: View {
    let title: String
    let subtitle: String
    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text(LocalizedStringKey(title)).font(.title2.weight(.semibold))
            Text(LocalizedStringKey(subtitle)).font(.callout).foregroundStyle(.secondary).lineSpacing(3)
        }.padding(.bottom, 6)
    }
}

private enum SettingsSection: String, CaseIterable, Identifiable {
    case general = "通用", models = "模型", prompts = "提示词", daily = "每日整理", data = "数据与隐私", integrations = "外部接入", connection = "高级连接"
    var id: String { rawValue }
    var symbol: String {
        switch self { case .general: "slider.horizontal.3"; case .models: "cpu"; case .prompts: "text.alignleft"; case .daily: "calendar.badge.clock"; case .data: "externaldrive"; case .integrations: "puzzlepiece.extension"; case .connection: "network" }
    }
}

struct MacSettingsView: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @ObservedObject private var local = LocalNodeController.shared
    @AppStorage("appearance") private var appearance = AppAppearance.system.rawValue
    @AppStorage("app.language") private var language = AppLanguage.system.rawValue
    @AppStorage("settings.section") private var sectionValue = SettingsSection.general.rawValue
    private var section: SettingsSection { SettingsSection(rawValue: sectionValue) ?? .general }
    var body: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 8) { BrandMark(size: 28); Text("设置").font(.headline) }.padding(20)
                VStack(spacing: 4) {
                    ForEach(SettingsSection.allCases) { item in
                        Button { sectionValue = item.rawValue } label: {
                            Label(LocalizedStringKey(item.rawValue), systemImage: item.symbol)
                                .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 12).padding(.vertical, 10)
                                .foregroundStyle(.primary)
                                .background(section == item ? AppPalette.line : .clear, in: RoundedRectangle(cornerRadius: 9))
                        }.buttonStyle(QuietPressStyle())
                    }
                }.padding(.horizontal, 10)
                Spacer()
            }.frame(width: 190).background(AppPalette.surface)
            Divider()
            NavigationStack {
                Group {
                    switch section {
                    case .general: general
                    case .models: ModelSettingsView(workspace: workspace)
                    case .prompts: PromptSettingsView(workspace: workspace)
                    case .daily: DailySettingsView(workspace: workspace)
                    case .data: data
                    case .integrations: ExternalAccessSettings(workspace: workspace)
                    case .connection: MacAdvancedSettings(workspace: workspace)
                    }
                }.frame(maxWidth: .infinity, maxHeight: .infinity).libraryDestinations(store: workspace)
            }.id(sectionValue + language)
        }.frame(minWidth: 920, idealWidth: 980, minHeight: 660, idealHeight: 760)
            .background(AppPalette.canvas).tint(.primary)
            .preferredColorScheme(AppAppearance(rawValue: appearance)?.scheme)
            .environment(\.locale, (AppLanguage(rawValue: language) ?? .system).locale)
            .navigationTitle(L("设置"))
    }
    private var general: some View {
        Form {
            Section { SettingsPageTitle(title: "通用", subtitle: "让声记适应你的习惯。") }
            Section("显示") {
                Picker("外观", selection: $appearance) {
                    ForEach(AppAppearance.allCases) { Text(LocalizedStringKey($0.title)).tag($0.rawValue) }
                }.pickerStyle(.segmented)
                Picker("界面语言", selection: $language) {
                    ForEach(AppLanguage.allCases) { Text(LocalizedStringKey($0.title)).tag($0.rawValue) }
                }
                Text("语言切换只改变界面，不翻译你的录音、提示词或记忆。").font(.caption).foregroundStyle(.secondary)
            }
            Section("键盘操作") {
                LabeledContent("打开设置", value: "⌘,")
                LabeledContent("切换栏目", value: "⌘1–7")
                LabeledContent("搜索当前资料", value: "⌘F")
                LabeledContent("导入音频", value: "⇧⌘I")
                LabeledContent("切换列表选中项", value: "↑ ↓")
            }
        }.formStyle(.grouped)
    }
    private var data: some View {
        Form {
            Section { SettingsPageTitle(title: "数据与隐私", subtitle: "资料保存在你选择的位置。") }
            Section("保存与归档") {
                Text(L(workspace.status?.outputDirectory ?? L("尚未连接资料库")))
                    .font(.callout).textSelection(.enabled)
                HStack {
                    Button("选择归档目录") { Task { await local.chooseFolder(workspace: workspace) } }
                    Button("在文件夹中打开") {
                        if let path = workspace.status?.outputDirectory { NSWorkspace.shared.open(URL(fileURLWithPath: path)) }
                    }
                }.disabled(!workspace.connected)
                Text("更换目录会保留原目录中的资料。").font(.caption).foregroundStyle(.secondary)
            }
            DataTransferSection(workspace: workspace)
            Section("密钥与内容") {
                Label("模型密钥保存在系统钥匙串。", systemImage: "key")
                Label("长期记忆在确认后写入文件。", systemImage: "checkmark.shield")
                Label("录音和文字不会因填写模型配置而自动发送。", systemImage: "lock")
            }
            Section("需要核对的版本") {
                if let conflicts = workspace.status?.conflicts, !conflicts.isEmpty {
                    ForEach(conflicts) { conflict in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(verbatim: conflict.path).font(.headline)
                            Text(verbatim: conflict.generatedPath ?? "").font(.caption).textSelection(.enabled)
                        }
                    }
                } else { Text("没有待合并的文件。").foregroundStyle(.secondary) }
            }
            if let error = local.problem ?? workspace.problem { Text(L(error)).font(.caption).foregroundStyle(.secondary) }
        }.formStyle(.grouped)
    }
}

private struct PromptSettingsView: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @State private var selection = "daily-summary"
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 18) {
                SettingsPageTitle(title: "提示词", subtitle: "修改整理规则，保留你自己的表达方式。")
                Picker("规则", selection: $selection) {
                    Text("每日摘要").tag("daily-summary")
                    Text("记忆提取").tag("memory-extraction")
                }.pickerStyle(.segmented).frame(maxWidth: 360)
            }.padding(28)
            Divider()
            NodeDocumentEditor(store: workspace, path: "v1/prompts/\(selection)",
                title: selection == "daily-summary" ? "每日摘要提示词" : "记忆提取提示词",
                hint: "保存后用于后续整理；历史内容不会自动重写。").id(selection)
        }
    }
}

private struct DailyPreferences: Codable, Equatable, Sendable {
    var enabled = true
    var time = "00:15"
    var timeZone = "Asia/Shanghai"
    var nextRun: String?
    var lastRun: String?
}

private struct DailySettingsView: View {
    @ObservedObject var workspace: NodeWorkspaceStore
    @State private var preferences = DailyPreferences()
    @State private var saved = DailyPreferences()
    @State private var loaded = false
    @State private var saving = false
    @State private var message: String?
    var body: some View {
        Form {
            Section { SettingsPageTitle(title: "每日整理", subtitle: "按所选时区整理前一天的记录，电脑恢复运行后补做。") }
            Section("安排") {
                Toggle("自动整理每日记录", isOn: $preferences.enabled)
                TextField("整理时间（24 小时制）", text: $preferences.time, prompt: Text("00:15"))
                TextField("时区", text: $preferences.timeZone, prompt: Text("Asia/Shanghai"))
                Text("时间使用 HH:mm 格式。电脑需要运行声记；休眠期间不会唤醒电脑。")
                    .font(.caption).foregroundStyle(.secondary)
            }.disabled(!loaded || saving)
            AutomaticAnalysisSection(workspace: workspace)
            Section("当前处理方式") {
                LabeledContent("每日输出") { Text("来源草稿") }
                Text("来源草稿保留原文。模型配置完成后，可额外生成每日分析。")
                    .font(.callout).foregroundStyle(.secondary)
                Text("记忆建议始终需要确认，不自动写成个人事实。").font(.caption).foregroundStyle(.secondary)
            }
            Section("运行状态") {
                if let next = saved.nextRun { LabeledContent("下次检查", value: readable(next)) }
                if let last = saved.lastRun { LabeledContent("上次检查", value: readable(last)) }
                if saved.nextRun == nil { Text(L(saved.enabled ? "等待资料库连接" : "自动整理已关闭")).foregroundStyle(.secondary) }
            }
            Section {
                HStack {
                    if let message { Text(L(message)).font(.caption).foregroundStyle(.secondary) }
                    Spacer()
                    Button("保存安排") { Task { await save() } }.buttonStyle(FilledButtonStyle()).disabled(!loaded || saving || preferences == saved)
                }
            }
        }.formStyle(.grouped).task { await load() }
    }
    private func readable(_ value: String) -> String {
        guard let date = ISO8601DateFormatter().date(from: value) else { return value }
        let f = DateFormatter(); f.dateStyle = .medium; f.timeStyle = .short
        f.locale = AppLanguage.current.locale; f.timeZone = TimeZone(identifier: saved.timeZone)
        return f.string(from: date)
    }
    private func load() async {
        do {
            guard let client = workspace.client else { throw NodeFailure(message: "请先连接资料库。") }
            preferences = try await client.read("v1/daily-settings", as: DailyPreferences.self); saved = preferences; loaded = true
        } catch { message = error.localizedDescription }
    }
    private func save() async {
        guard let client = workspace.client else { return }
        saving = true; defer { saving = false }
        do {
            let result = try await client.sendData("v1/daily-settings", method: "PUT", body: JSONEncoder().encode(preferences))
            preferences = try JSONDecoder().decode(DailyPreferences.self, from: result); saved = preferences
            message = "安排已保存。"; await workspace.refresh()
        } catch { message = error.localizedDescription }
    }
}
