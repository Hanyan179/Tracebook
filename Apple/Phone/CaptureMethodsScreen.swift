import AppIntents
import SwiftUI

struct CaptureMethodsScreen: View {
    @EnvironmentObject private var preferences: CapturePreferences
    @EnvironmentObject private var recorder: CaptureRecorder
    @EnvironmentObject private var watch: WatchAvailability
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("用顺手的方式，\n留下声音。")
                    .font(.system(size: 30, weight: .semibold)).fixedSize(horizontal: false, vertical: true)
                Text("随时切换，也可以同时使用。").font(.subheadline).foregroundStyle(.secondary)
                VStack(spacing: 0) {
                    ForEach(CaptureMethod.allCases) { method in
                        NavigationLink { MethodSetupScreen(method: method) } label: {
                            HStack(spacing: 15) {
                                MethodIcon(method: method, selected: preferences.preferred == method)
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(LocalizedStringKey(method.title)).font(.body.weight(.medium))
                                    Text(L(status(method))).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer(minLength: 4)
                                Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
                            }.padding(.vertical, 17)
                        }.buttonStyle(.plain)
                        if method != CaptureMethod.allCases.last { Divider() }
                    }
                }
            }.padding(26)
        }.background(PhoneStyle.canvas).navigationTitle("记录入口").navigationBarTitleDisplayMode(.inline)
    }
    private func status(_ method: CaptureMethod) -> String {
        switch method {
        case .phone: return recorder.permissionDenied ? "麦克风权限已关闭" : recorder.ready ? "可直接使用" : "首次使用需允许麦克风"
        case .file: return "从文件导入"
        case .watch: return watch.installed ? "手表应用已安装 · 打开试录" : "需要配对手表并安装应用"
        default: return preferences.configured.contains(method.rawValue) ? "已确认设置" : "需要配置快捷指令"
        }
    }
}

struct MethodSetupScreen: View {
    let method: CaptureMethod
    @EnvironmentObject private var preferences: CapturePreferences
    @EnvironmentObject private var recorder: CaptureRecorder
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 30) {
                MethodIcon(method: method, selected: true)
                VStack(alignment: .leading, spacing: 9) {
                    Text(LocalizedStringKey(method.title)).font(.largeTitle.weight(.semibold))
                    Text(LocalizedStringKey(method.subtitle)).foregroundStyle(.secondary)
                }
                MethodSetupContent(method: method)
                Button(L(preferences.preferred == method ? "当前首选" : "设为首选入口")) {
                    preferences.preferred = method
                }.buttonStyle(PrimaryAction())
                if method.needsShortcut {
                    Button(L(preferences.configured.contains(method.rawValue) ? "已确认设置" : "我已完成系统设置")) {
                        preferences.confirmSetup(method)
                    }.frame(maxWidth: .infinity).font(.subheadline).foregroundStyle(.secondary)
                }
            }.padding(26)
        }.background(PhoneStyle.canvas).navigationBarTitleDisplayMode(.inline)
    }
}

struct MethodSetupContent: View {
    let method: CaptureMethod
    @EnvironmentObject private var recorder: CaptureRecorder
    @EnvironmentObject private var watch: WatchAvailability
    var body: some View {
        VStack(alignment: .leading, spacing: 26) {
            ForEach(Array((method == .phone ? [] : method.steps).enumerated()), id: \.offset) { index, text in
                HStack(alignment: .top, spacing: 15) {
                    Text(String(format: "%02d", index + 1))
                        .font(.caption.monospacedDigit()).foregroundStyle(PhoneStyle.accent)
                        .padding(.top, 4)
                    Text(LocalizedStringKey(text)).font(.body).fixedSize(horizontal: false, vertical: true)
                }
            }
            if method == .phone || method.needsShortcut {
                if recorder.permissionDenied {
                    Link("前往设置允许麦克风", destination: URL(string: UIApplication.openSettingsURLString)!)
                        .buttonStyle(.bordered).controlSize(.large)
                } else if !recorder.ready {
                    Button { Task { await recorder.prepare() } } label: {
                        Label("允许使用麦克风", systemImage: "mic")
                    }.buttonStyle(.bordered).controlSize(.large).disabled(recorder.busy)
                } else {
                    Label("麦克风已准备好", systemImage: "checkmark.circle.fill")
                        .font(.footnote).foregroundStyle(PhoneStyle.accent)
                }
            }
            if method.needsShortcut || method == .watch {
                ShortcutsLink()
            }
            if method == .actionButton {
                Text("适用于配备操作按钮的手机。").font(.footnote).foregroundStyle(.secondary)
            }
            if method == .watch {
                Label(L(watch.installed ? "手表应用已安装" : (watch.paired ? "请先在手表安装声记" : "尚未检测到配对手表")),
                      systemImage: "applewatch")
                    .font(.footnote).foregroundStyle(.secondary)
                Text("原生互点两下需要支持的手表和可见的录音按钮；在表盘上互点不会直接启动迹库录音。辅助触控是另一套系统设置，不能视为已经绑定。")
                    .font(.footnote).foregroundStyle(.secondary)
                Text("手表跟随手机中选择的应用语言，连接后更新。").font(.footnote).foregroundStyle(.secondary)
                if let problem = watch.preferenceProblem { Text(L(problem)).font(.footnote).foregroundStyle(.secondary) }
            }
        }
    }
}
