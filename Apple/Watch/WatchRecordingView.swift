import SwiftUI

struct WatchRecordingView: View {
    @EnvironmentObject private var recorder: CaptureRecorder
    private let accent = Color.white
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 12) {
                    HStack {
                        Circle().fill(recorder.capturing ? .red : accent).frame(width: 6, height: 6)
                        Text(L(recorder.paused ? "已暂停" : (recorder.capturing ? "正在录音" : "声记")))
                            .font(.caption.weight(.medium)).foregroundStyle(.secondary)
                        Spacer()
                        BrandMark(size: 24)
                    }
                    Text(recorder.elapsed.formattedTime)
                        .font(.system(size: 37, weight: .light, design: .rounded))
                        .monospacedDigit().minimumScaleFactor(0.6).lineLimit(1)
                        .accessibilityLabel("录音时长 \(Int(recorder.elapsed)) 秒")
                    HStack(alignment: .center, spacing: 4) {
                        ForEach(0..<19) { index in
                            Capsule().fill(recorder.active == nil ? accent.opacity(0.35) : accent)
                                .frame(width: 3, height: barHeight(index))
                        }
                    }.frame(height: 24).accessibilityHidden(true)
                    if !recorder.ready {
                        Button("允许麦克风") { Task { await recorder.prepare() } }
                            .tint(accent).foregroundStyle(.black).buttonStyle(.borderedProminent)
                    } else {
                        Button(action: recorder.toggleFromButton) {
                            Label(L(recorder.active == nil ? "开始录音" : (recorder.capturing || recorder.paused ? "停止并保存" : "重试保存")),
                                  systemImage: recorder.active == nil ? "mic.fill" : "stop.fill")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent).tint(accent).foregroundStyle(.black)
                        .disabled(recorder.busy)
                        .handGestureShortcut(.primaryAction)
                        .accessibilityIdentifier("watch.record.toggle")
                    }
                    if recorder.capturing || recorder.paused {
                        Button(L(recorder.paused ? "继续录音" : "暂停"), systemImage: recorder.paused ? "play" : "pause") {
                            recorder.togglePause()
                        }.buttonStyle(.bordered).disabled(recorder.busy)
                    }
                    if let message = recorder.message {
                        Text(message).font(.caption2).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        Text(L(recorder.active == nil ? "留下一段声音" : "声音保存在手表"))
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    NavigationLink {
                        WatchLibraryView()
                    } label: {
                        HStack {
                            Text("原音").font(.caption)
                            Spacer()
                            Text("\(recorder.recordings.filter { $0.trashedAt == nil }.count)").font(.caption.monospacedDigit())
                        }
                    }.disabled(recorder.active != nil)
                }.padding(.horizontal, 2)
            }
            .navigationBarHidden(true)
            .containerBackground(.black, for: .navigation)
        }
    }
    private func barHeight(_ index: Int) -> CGFloat {
        guard recorder.capturing else { return CGFloat(4 + (index % 4)) }
        return CGFloat(4 + recorder.level * Float(8 + (index * 7 % 17)))
    }
}

private struct WatchLibraryView: View {
    @EnvironmentObject private var recorder: CaptureRecorder
    @EnvironmentObject private var playback: AudioPlayback
    @EnvironmentObject private var transfer: WatchTransferBridge
    var body: some View {
        List {
            if recorder.recordings.filter({ $0.trashedAt == nil }).isEmpty { Text("还没有录音").foregroundStyle(.secondary) }
            ForEach(recorder.recordings.filter { $0.trashedAt == nil }) { recording in
                VStack(alignment: .leading, spacing: 5) {
                    Text(recording.displayTitle)
                        .font(.caption)
                    HStack {
                        Text(recording.duration.formattedTime).monospacedDigit()
                        Spacer()
                        Text(recording.statusLabel)
                    }.font(.caption2).foregroundStyle(.secondary)
                    if let note = recording.note {
                        Text(note).font(.caption2).foregroundStyle(.secondary)
                    }
                    if recording.isPlayable {
                        Text(L(transfer.label(for: recording.id))).font(.caption2).foregroundStyle(.secondary)
                    }
                    Button(L(playback.recordingID == recording.id && playback.isPlaying ? "暂停回放" : "回听原音"),
                           systemImage: playback.recordingID == recording.id && playback.isPlaying ? "pause.fill" : "play.fill") {
                        recorder.togglePlayback(recording)
                    }.disabled(!recording.isPlayable || recorder.active != nil)
                }.swipeActions {
                    Button("移到最近删除", systemImage: "trash", role: .destructive) {
                        recorder.moveToTrash(recording)
                    }
                }
            }
            NavigationLink("最近删除") {
                List {
                    if recorder.recordings.allSatisfy({ $0.trashedAt == nil }) {
                        Text("这里没有录音").foregroundStyle(.secondary)
                    }
                    ForEach(recorder.recordings.filter { $0.trashedAt != nil }) { record in
                        VStack(alignment: .leading) {
                            Text(record.displayTitle).font(.caption)
                            Button("恢复") { recorder.restore(record) }
                        }
                    }
                }.navigationTitle(L("已删除"))
            }
        }.navigationTitle(L("原音"))
            .onDisappear { playback.stop() }
    }
}

private extension TimeInterval {
    var formattedTime: String {
        let seconds = max(0, Int(self))
        return seconds >= 3600
            ? String(format: "%d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60)
            : String(format: "%02d:%02d", seconds / 60, seconds % 60)
    }
}
