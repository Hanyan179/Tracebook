import SwiftUI

enum PhoneStyle {
    static let ink = Color.primary
    static let accent = Color.primary
    static let canvas = AppPalette.canvas
    static let surface = AppPalette.surface
    static let line = AppPalette.line
}

struct PrimaryAction: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorScheme) private var scheme
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.semibold)).frame(maxWidth: .infinity, minHeight: 54)
            .foregroundStyle(AppPalette.actionText(scheme))
            .background(AppPalette.actionFill(scheme), in: Capsule())
            .opacity(configuration.isPressed ? 0.75 : 1)
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.98 : 1)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.16), value: configuration.isPressed)
    }
}

struct MethodIcon: View {
    let method: CaptureMethod
    var selected = false
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        Image(systemName: method.symbol).font(.system(size: 23, weight: .regular))
            .frame(width: 48, height: 48)
            .foregroundStyle(selected ? AppPalette.actionText(scheme) : PhoneStyle.ink)
            .background(selected ? AppPalette.actionFill(scheme) : Color.primary.opacity(0.045),
                        in: RoundedRectangle(cornerRadius: 15))
            .accessibilityHidden(true)
    }
}

struct RecordControl: View {
    @EnvironmentObject private var recorder: CaptureRecorder
    let action: () -> Void
    var diameter: CGFloat = 112
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        Button(action: action) {
            ZStack {
                Circle().strokeBorder(PhoneStyle.line, lineWidth: 1).frame(width: diameter + 24, height: diameter + 24)
                Circle().fill(recorder.capturing ? Color.red : AppPalette.actionFill(scheme)).frame(width: diameter, height: diameter)
                Image(systemName: recorder.active == nil ? "mic.fill" : "stop.fill")
                    .font(.system(size: diameter * 0.31, weight: .medium))
                    .foregroundStyle(recorder.capturing ? .white : AppPalette.actionText(scheme))
            }
        }.buttonStyle(.plain)
            .disabled(recorder.busy)
            .accessibilityLabel(recorder.active == nil ? "开始录音" : "停止并保存")
            .accessibilityIdentifier("phone.record.toggle")
    }
}

extension TimeInterval {
    var timeLabel: String {
        let seconds = max(0, Int(self))
        return seconds >= 3600
            ? String(format: "%d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60)
            : String(format: "%02d:%02d", seconds / 60, seconds % 60)
    }
}
