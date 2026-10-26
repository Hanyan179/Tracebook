import SwiftUI

struct WatchRecordingView: View {
    @EnvironmentObject private var recorder: CaptureRecorder
    @AppStorage("app.language") private var language = AppLanguage.system.rawValue
    private let accent = Color.white
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: recorder.active == nil ? 12 : 6) {
                    HStack {
                        Circle().fill(recorder.capturing ? .red : accent).frame(width: 6, height: 6)
                        Text(L(recorder.paused ? "已暂停" : (recorder.capturing ? "正在录音" : "声记")))
                            .font(.caption.weight(.medium)).foregroundStyle(.secondary)
                        Spacer()
                        BrandMark(size: 24)
                    }
                    Text(recorder.elapsed.formattedTime)
                        .font(.system(size: recorder.active == nil ? 37 : 32, weight: .light, design: .rounded))
                        .monospacedDigit().minimumScaleFactor(0.6).lineLimit(1)
                        .accessibilityLabel(LF("录音时长 %d 秒", Int(recorder.elapsed)))
                    HStack(alignment: .center, spacing: 4) {
                        ForEach(0..<19) { index in
                            Capsule().fill(recorder.active == nil ? accent.opacity(0.35) : accent)
                                .frame(width: 3, height: barHeight(index))
                        }
                    }.frame(height: recorder.active == nil ? 24 : 12).accessibilityHidden(true)
                    if !recorder.ready {
                        Button("允许麦克风") { Task { await recorder.prepare() } }
                            .tint(accent).foregroundStyle(.black).buttonStyle(.borderedProminent)
                            .disabled(recorder.busy)
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
                        HStack(spacing: 6) {
                            Button(L(recorder.paused ? "继续" : "暂停"), systemImage: recorder.paused ? "play" : "pause") {
                                recorder.togglePause()
                            }.frame(maxWidth: .infinity)
                            Button(action: recorder.markMoment) {
                                Label(LF("标记 %d", recorder.active?.audioMarkers.count ?? 0), systemImage: "flag")
                            }.frame(maxWidth: .infinity)
                                .accessibilityLabel(LF("标记重点，已有 %d 处", recorder.active?.audioMarkers.count ?? 0))
                                .accessibilityIdentifier("watch.record.mark")
                        }
                        .font(.caption2).buttonStyle(.bordered).disabled(recorder.busy)
                    }
                    if let message = recorder.message {
                        Text(L(message)).font(.caption2).foregroundStyle(.secondary)
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
        }.environment(\.locale, (AppLanguage(rawValue: language) ?? .system).locale)
            .alert(L("暂时无法完成"), isPresented: Binding(get: { recorder.problem != nil },
                                                     set: { if !$0 { recorder.problem = nil } })) {
                Button(L("知道了")) { recorder.problem = nil }
            } message: { Text(L(recorder.problem ?? "")) }
    }
    private func barHeight(_ index: Int) -> CGFloat {
        guard recorder.capturing else { return CGFloat(4 + (index % 4)) }
        return CGFloat(4 + recorder.level * Float(8 + (index * 7 % 17)))
    }
}

private struct WatchLibraryView: View {
    @AppStorage("app.language") private var language = AppLanguage.system.rawValue
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
                        Text(playback.recordingID == recording.id
                             ? playback.position.formattedTime + " / " + recording.duration.formattedTime
                             : recording.duration.formattedTime).monospacedDigit()
                        Spacer()
                        Text(recording.statusLabel)
                    }.font(.caption2).foregroundStyle(.secondary)
                    if let note = recording.note {
                        Text(L(note)).font(.caption2).foregroundStyle(.secondary)
                    }
                    if recording.isPlayable {
                        Text(L(transfer.label(for: recording.id))).font(.caption2).foregroundStyle(.secondary)
                    }
                    Button {
                        recorder.togglePlayback(recording)
                    } label: {
                        Label(L(playback.recordingID == recording.id && playback.isPlaying ? "暂停回放" : "回听原音"),
                              systemImage: playback.recordingID == recording.id && playback.isPlaying ? "pause.fill" : "play.fill")
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    }.disabled(!recording.isPlayable || recorder.active != nil)
                    RecordingMarkersView(markers: recording.audioMarkers, duration: recording.duration,
                                         disabled: !recording.isPlayable || recorder.active != nil) { marker in
                        recorder.togglePlayback(recording, at: marker.offset)
                    }
                }.buttonStyle(.plain).swipeActions {
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
            .environment(\.locale, (AppLanguage(rawValue: language) ?? .system).locale)
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
