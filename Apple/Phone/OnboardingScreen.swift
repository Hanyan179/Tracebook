import SwiftUI
import UniformTypeIdentifiers

struct OnboardingScreen: View {
    @EnvironmentObject private var preferences: CapturePreferences
    @EnvironmentObject private var recorder: CaptureRecorder
    @EnvironmentObject private var watch: WatchAvailability
    @EnvironmentObject private var playback: AudioPlayback
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var step = 0
    @State private var method: CaptureMethod = .phone
    @State private var importing = false
    @State private var importBusy = false
    @State private var originalIDs: Set<UUID> = []
    private var newRecording: Recording? {
        recorder.recordings.first { !originalIDs.contains($0.id) && $0.status == .saved && $0.trashedAt == nil }
    }
    private var stepCount: Int { method == .file ? 2 : 3 }
    private var isLastStep: Bool { step == stepCount - 1 }
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                if step > 0 {
                    Button { advance(to: step - 1) } label: { Image(systemName: "arrow.left") }
                        .frame(width: 44, height: 44).accessibilityLabel("上一步")
                        .disabled(recorder.active != nil || importBusy)
                } else { HStack(spacing: 0) { BrandMark(size: 36); Text("声记").font(.headline) } }
                Spacer()
                Button("稍后设置") { preferences.preferred = method; preferences.onboarded = true }
                    .font(.subheadline).foregroundStyle(AppPalette.muted)
                    .disabled(recorder.active != nil)
            }.padding(.horizontal, 26).padding(.top, 12).padding(.bottom, 20)
            ScrollView {
                VStack(alignment: .leading, spacing: 26) {
                    HStack(spacing: 5) {
                        ForEach(0..<stepCount, id: \.self) { index in
                            Capsule().fill(index <= step ? PhoneStyle.accent : PhoneStyle.line)
                                .frame(width: index == step ? 34 : 14, height: 3)
                        }
                        Spacer()
                        Text("\(step + 1) / \(stepCount)").font(.caption.monospacedDigit()).foregroundStyle(AppPalette.muted)
                    }.accessibilityLabel("第 \(step + 1) 步，共 \(stepCount) 步")
                    switch step {
                    case 0: chooseMethod
                    case 1: if method == .file { tryCapture } else { configureMethod }
                    default: tryCapture
                    }
                }.id(step).transition(.opacity.combined(with: .offset(y: reduceMotion ? 0 : 10)))
                    .padding(.horizontal, 26).padding(.bottom, 20)
            }
            VStack(spacing: 12) {
                Button(action: next) {
                    HStack {
                        Text(L(isLastStep ? "进入声记" : (step == 1 && method.needsShortcut ? "已设置，继续" : "继续")))
                        Spacer()
                        Image(systemName: "arrow.right")
                    }.padding(.horizontal, 20)
                }.buttonStyle(PrimaryAction())
                    .disabled(!canContinue)
                    .opacity(canContinue ? 1 : 0.35)
                    .accessibilityIdentifier("onboarding.continue")
                if step == 0 {
                    Text("先选一个，其他方式随时添加。").font(.caption).foregroundStyle(AppPalette.muted)
                }
            }.padding(.horizontal, 26).padding(.top, 12).padding(.bottom, 20)
                .background(PhoneStyle.canvas)
        }.background(PhoneStyle.canvas).tint(PhoneStyle.ink)
            .interactiveDismissDisabled()
            .onAppear { method = preferences.preferred; originalIDs = Set(recorder.recordings.map(\.id)) }
            .onChange(of: recorder.active?.id, initial: true) { _, id in
                if id != nil {
                    if method == .file || method == .watch { method = .phone }
                    step = 2
                }
            }
            .fileImporter(isPresented: $importing, allowedContentTypes: [.audio]) { result in
                if case .success(let url) = result {
                    importBusy = true
                    Task { await recorder.importAudio(from: url); importBusy = false }
                } else if case .failure(let error) = result { recorder.problem = error.localizedDescription }
            }
            .alert("暂时无法完成", isPresented: Binding(get: { recorder.problem != nil },
                                                     set: { if !$0 { recorder.problem = nil } })) {
                Button("知道了") { recorder.problem = nil }
            } message: { Text(recorder.problem ?? "") }
    }

    private var chooseMethod: some View {
        VStack(alignment: .leading, spacing: 24) {
            VStack(alignment: .leading, spacing: 12) {
                Text("让记录，\n顺手发生。")
                    .font(.system(.largeTitle, design: .default, weight: .semibold)).tracking(-1)
                    .fixedSize(horizontal: false, vertical: true)
                Text("你想怎样开始？").font(.body).foregroundStyle(AppPalette.muted)
            }
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 12),
                                    count: typeSize.isAccessibilitySize ? 1 : 2), spacing: 12) {
                ForEach(CaptureMethod.allCases) { item in
                    Button {
                        method = item
                    } label: {
                        VStack(alignment: .leading, spacing: 18) {
                            HStack {
                                Image(systemName: item.symbol).font(.system(size: 24, weight: .regular))
                                Spacer()
                                Image(systemName: method == item ? "checkmark.circle.fill" : "circle")
                                    .font(.system(size: 16)).foregroundStyle(method == item ? PhoneStyle.accent : .secondary)
                            }
                            VStack(alignment: .leading, spacing: 6) {
                                Text(LocalizedStringKey(item.title)).font(.body.weight(.semibold))
                                Text(LocalizedStringKey(item.subtitle)).font(.caption).foregroundStyle(AppPalette.muted)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }.frame(maxWidth: .infinity, alignment: .leading)
                            .padding(18)
                            .background(method == item ? PhoneStyle.accent.opacity(0.065) : PhoneStyle.surface,
                                        in: RoundedRectangle(cornerRadius: 20))
                            .overlay {
                                RoundedRectangle(cornerRadius: 20)
                                    .strokeBorder(method == item ? PhoneStyle.accent : PhoneStyle.line,
                                                  lineWidth: method == item ? 1.5 : 1)
                            }
                    }.buttonStyle(.plain)
                        .accessibilityLabel("\(item.title)，\(item.subtitle)")
                        .accessibilityAddTraits(method == item ? [.isSelected] : [])
                        .accessibilityIdentifier("capture.method.\(item.rawValue)")
                }
            }
        }
    }
    private var configureMethod: some View {
        VStack(alignment: .leading, spacing: 32) {
            MethodIcon(method: method, selected: true)
            VStack(alignment: .leading, spacing: 10) {
                Text(LocalizedStringKey(method.title)).font(.largeTitle.weight(.semibold))
                Text(L(method == .phone ? "允许麦克风，就可以开始。" : "一次设置，以后更顺手。"))
                    .foregroundStyle(AppPalette.muted)
            }
            MethodSetupContent(method: method)
        }
    }
    private var tryCapture: some View {
        VStack(alignment: .leading, spacing: 28) {
            Text(L(newRecording == nil ? "留下第一段声音。" : "第一段声音，\n已经留下。"))
                .font(.largeTitle.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
            if method == .watch {
                Text("回到手表表盘，试着捏合两下。录完后在手表回听。")
                    .foregroundStyle(AppPalette.muted)
                Image(systemName: "applewatch").font(.system(size: 76, weight: .ultraLight))
                    .frame(maxWidth: .infinity).padding(.vertical, 40)
            } else if method == .file {
                Button { importing = true } label: {
                    Label(L(importBusy ? "正在导入" : "选择一段音频"), systemImage: "arrow.down.document")
                }.buttonStyle(PrimaryAction()).disabled(importBusy)
            } else {
                Text(L(method.needsShortcut ? "也可以用刚设置的入口开始。" : "说点什么，再点一次保存。"))
                    .foregroundStyle(AppPalette.muted)
                VStack(spacing: 24) {
                    Text(recorder.active != nil ? recorder.elapsed.timeLabel : (newRecording?.duration.timeLabel ?? "00:00"))
                        .font(.system(size: 48, weight: .light, design: .rounded)).monospacedDigit()
                    RecordControl {
                        Task {
                            if !recorder.ready { await recorder.prepare() }
                            if recorder.ready { recorder.toggleFromButton() }
                        }
                    }
                    Text(L(recorder.active == nil ? "点击开始" : "点击保存"))
                        .font(.subheadline).foregroundStyle(AppPalette.muted)
                }.frame(maxWidth: .infinity).padding(.vertical, 28)
            }
            if let recording = newRecording {
                Button {
                    recorder.togglePlayback(recording)
                } label: {
                    Label(L(playback.recordingID == recording.id && playback.isPlaying ? "暂停回放" : "听听刚才的录音"),
                          systemImage: playback.recordingID == recording.id && playback.isPlaying ? "pause.fill" : "play.fill")
                }.buttonStyle(.bordered).controlSize(.large).disabled(recorder.active != nil)
            }
        }
    }
    private var canContinue: Bool {
        if isLastStep { return recorder.active == nil && !importBusy }
        if step == 1 && (method == .phone || method.needsShortcut) { return recorder.ready }
        return true
    }
    private func next() {
        preferences.preferred = method
        if step == 1 && method.needsShortcut { preferences.confirmSetup(method) }
        if !isLastStep { advance(to: step + 1) }
        else { playback.stop(); preferences.onboarded = true }
    }
    private func advance(to newStep: Int) {
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.22)) { step = newStep }
    }
}
