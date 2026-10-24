import AppIntents

/// Opens the watch app automatically; microphone work begins only once foregrounded.
/// Deliberately not a background AudioRecordingIntent: this proof uses the
/// user-approved foreground route, without depending on unavailable watch ActivityKit.
struct ToggleWatchRecording: AppIntent {
    static let title: LocalizedStringResource = "切换手表录音"
    static let description = IntentDescription("在手表本机开始录音；再次运行时停止并保存。请在手表上运行。")
    static let openAppWhenRun: Bool = true
    @available(iOS 26.0, watchOS 26.0, *)
    static var supportedModes: IntentModes { .foreground(.immediate) }

    @MainActor
    func perform() async throws -> some IntentResult {
        try await executeOnWatch()
        return .result()
    }

    @MainActor
    private func executeOnWatch() async throws {
        #if os(watchOS)
        try await CaptureRecorder.shared.toggleFromShortcut()
        #else
        // The phone exposes discoverable metadata but must never record on behalf of
        // a watch gesture. Real-device testing must verify the watch executes this.
        throw RecordingIntentError.watchRequired
        #endif
    }
}

struct VoiceMemoryShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        #if os(iOS)
        AppShortcut(
            intent: TogglePhoneRecording(),
            phrases: ["用\(.applicationName)切换手机录音"],
            shortTitle: "切换手机录音",
            systemImageName: "mic.fill"
        )
        #endif
        AppShortcut(
            intent: ToggleWatchRecording(),
            phrases: ["用\(.applicationName)切换手表录音"],
            shortTitle: "切换手表录音",
            systemImageName: "mic"
        )
    }
}

#if os(iOS)
struct TogglePhoneRecording: AppIntent {
    static let title: LocalizedStringResource = "切换手机录音"
    static let description = IntentDescription("在手机开始录音，再次运行时停止并保存。")
    static let openAppWhenRun = true
    @available(iOS 26.0, *)
    static var supportedModes: IntentModes { .foreground(.immediate) }
    @MainActor func perform() async throws -> some IntentResult {
        try await CaptureRecorder.shared.toggleFromShortcut()
        return .result()
    }
}
#endif

enum RecordingIntentError: Error, LocalizedError, CustomLocalizedStringResourceConvertible {
    case watchRequired, notForeground, notPrepared, busy, captureFailed(String)
    var localizedStringResource: LocalizedStringResource {
        switch self {
        case .watchRequired: "请在手表上运行这个快捷指令，录音不会在手机上开始。"
        case .notForeground: "请解锁当前设备后重试。"
        case .notPrepared: "请先打开迹库，允许使用麦克风。"
        case .busy: "上一个录音操作正在完成，请稍后重试。"
        case .captureFailed(let message): "录音操作未完成：\(message)"
        }
    }
    var errorDescription: String? {
        switch self {
        case .watchRequired: L("请在手表上运行这个快捷指令，录音不会在手机上开始。")
        case .notForeground: L("请解锁当前设备后重试。")
        case .notPrepared: L("请先打开迹库，允许使用麦克风。")
        case .busy: L("上一个录音操作正在完成，请稍后重试。")
        case .captureFailed(let message): LF("录音操作未完成：%@", L(message))
        }
    }
}
