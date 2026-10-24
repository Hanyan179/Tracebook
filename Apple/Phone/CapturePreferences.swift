import Combine
import Foundation
import WatchConnectivity

enum CaptureMethod: String, CaseIterable, Identifiable {
    case phone, actionButton, backTap, watch, siri, file
    var id: String { rawValue }
    var title: String {
        switch self {
        case .phone: "手机录音"
        case .actionButton: "操作按钮"
        case .backTap: "轻点背面"
        case .watch: "手表录音"
        case .siri: "语音唤起"
        case .file: "导入音频"
        }
    }
    var subtitle: String {
        switch self {
        case .phone: "打开就能说"
        case .actionButton: "按住侧键，开始记录"
        case .backTap: "轻点手机背面两下"
        case .watch: "抬腕记录，手势可选"
        case .siri: "用一句话开始"
        case .file: "整理已有的声音"
        }
    }
    var symbol: String {
        switch self {
        case .phone: "mic"
        case .actionButton: "button.programmable"
        case .backTap: "hand.tap"
        case .watch: "applewatch"
        case .siri: "quote.bubble"
        case .file: "arrow.down.document"
        }
    }
    var needsShortcut: Bool { [.actionButton, .backTap, .siri].contains(self) }
    var steps: [String] {
        switch self {
        case .phone: ["允许麦克风访问", "点击录音，再点一次保存"]
        case .actionButton: ["打开快捷指令，添加“切换手机录音”", "在系统设置的“操作按钮”中选择这个快捷指令", "按住操作按钮试录一次"]
        case .backTap: ["打开快捷指令，添加“切换手机录音”", "前往系统设置 → 辅助功能 → 触控 → 轻点背面", "将轻点两下绑定到这个快捷指令"]
        case .watch: ["在手表打开迹库，允许麦克风访问", "点击开始录音，再点一次停止并保存", "支持互点两下的手表，录音按钮可见时也可用手势操作", "也可以在手表运行“切换手表录音”快捷指令"]
        case .siri: ["建立“切换手机录音”快捷指令，命名为“声记录音”", "对语音助手说“运行声记录音”"]
        case .file: ["从“文件”选择一段音频", "导入后可在记录中回听"]
        }
    }
}

@MainActor
final class CapturePreferences: ObservableObject {
    @Published var preferred: CaptureMethod {
        didSet { defaults.set(preferred.rawValue, forKey: "capture.preferred") }
    }
    @Published var onboarded: Bool {
        didSet { defaults.set(onboarded, forKey: "capture.onboarded") }
    }
    @Published private(set) var configured: Set<String>
    private let defaults: UserDefaults
    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        preferred = CaptureMethod(rawValue: defaults.string(forKey: "capture.preferred") ?? "") ?? .phone
        onboarded = defaults.bool(forKey: "capture.onboarded")
        configured = Set(defaults.stringArray(forKey: "capture.configured") ?? [])
    }
    func confirmSetup(_ method: CaptureMethod) {
        configured.insert(method.rawValue)
        defaults.set(configured.sorted(), forKey: "capture.configured")
    }
}
