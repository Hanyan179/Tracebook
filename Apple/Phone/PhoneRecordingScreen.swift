import SwiftUI

struct PhoneRecordingScreen: View {
    @EnvironmentObject private var recorder: CaptureRecorder
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var typeSize
    private var largerText: Bool { typeSize.isAccessibilitySize }
    var body: some View {
        GeometryReader { geometry in
        ScrollView {
        VStack(spacing: largerText ? 16 : 28) {
            if !largerText {
                Capsule().fill(.tertiary).frame(width: 32, height: 4).padding(.top, 12)
                Spacer()
            }
            HStack(spacing: 8) {
                Circle().fill(recorder.capturing ? .red : .orange).frame(width: 6, height: 6)
                Text(L(recorder.paused ? "已暂停" : (recorder.capturing ? "正在录音" : "等待保存")))
                    .font(.subheadline)
            }
            Text(recorder.elapsed.timeLabel)
                .font(.system(size: largerText ? 48 : 64, weight: .light, design: .rounded)).monospacedDigit()
                .minimumScaleFactor(0.6).lineLimit(1)
                .accessibilityLabel(LF("录音时长 %d 秒", Int(recorder.elapsed)))
            if !largerText {
            HStack(spacing: 5) {
                ForEach(0..<25) { index in
                    Capsule().fill(PhoneStyle.ink.opacity(recorder.capturing ? 0.85 : 0.25))
                        .frame(width: 3, height: recorder.capturing ? CGFloat(6 + recorder.level * Float(12 + (index * 13 % 44))) : 4)
                }
            }.frame(height: 70).animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: recorder.level)
                .accessibilityLabel("麦克风音量")
            Spacer()
            }
            VStack(spacing: 8) {
                Button(action: recorder.markMoment) {
                    Label(L(largerText ? "标记" : "标记重点"), systemImage: "flag").frame(minHeight: 44)
                }.buttonStyle(.bordered).tint(PhoneStyle.ink)
                    .disabled(recorder.busy || (!recorder.capturing && !recorder.paused))
                    .accessibilityLabel(L("标记重点"))
                    .accessibilityIdentifier("phone.record.mark")
                if let record = recorder.active, let last = record.audioMarkers.last {
                    Text(LF(largerText ? "已标记 %d · %@" : "已标记 %d 处，最近 %@", record.audioMarkers.count, last.timeLabel))
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
            if !largerText { captureActions }
            if !largerText { Text("声音保存在这台手机").font(.footnote).foregroundStyle(.secondary).padding(.top, 12) }
        }.frame(maxWidth: .infinity, minHeight: largerText ? 0 : max(0, geometry.size.height - 40))
            .padding(.horizontal, 26).padding(.bottom, largerText ? 12 : 40).padding(.top, largerText ? 12 : 0)
        }.safeAreaInset(edge: .bottom) {
            if largerText {
                captureActions.padding(.horizontal, 16).padding(.vertical, 12).background(PhoneStyle.canvas)
            }
        }
        }.background(PhoneStyle.canvas).interactiveDismissDisabled(recorder.active != nil)
            .alert(L("暂时无法完成"), isPresented: Binding(get: { recorder.problem != nil },
                                                     set: { if !$0 { recorder.problem = nil } })) {
                Button(L("知道了")) { recorder.problem = nil }
            } message: { Text(L(recorder.problem ?? "")) }
    }
    private var captureActions: some View {
        HStack(alignment: largerText ? .top : .center, spacing: largerText ? 16 : 44) {
                Button(action: recorder.togglePause) {
                    VStack(spacing: largerText ? 1 : 12) {
                        Image(systemName: recorder.paused ? "play.fill" : "pause.fill")
                            .font(.title2).frame(width: 64, height: 64)
                            .background(PhoneStyle.line.opacity(0.6), in: Circle())
                            .frame(height: largerText ? 100 : 64)
                        Text(L(recorder.paused ? "继续录音" : "暂停")).font(.subheadline)
                    }
                }.buttonStyle(.plain).frame(maxWidth: largerText ? .infinity : nil)
                    .disabled(recorder.busy || (!recorder.capturing && !recorder.paused))
                VStack(spacing: 0) {
                    RecordControl(action: recorder.toggleFromButton, diameter: 76)
                    Text(L(recorder.capturing || recorder.paused ? (largerText ? "保存" : "保存录音") : "重试保存"))
                        .font(.subheadline).padding(.top, 1)
                }.frame(maxWidth: largerText ? .infinity : nil)
            }
    }
}

struct AppSettingsScreen: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var workspace: NodeWorkspaceStore
    var body: some View {
        NavigationStack {
            PhonePreferencesScreen()
                .libraryDestinations(store: workspace)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
        }.appLanguage()
    }
}
