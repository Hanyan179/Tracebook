import SwiftUI

struct RecordingDetailScreen: View {
    let recordingID: UUID
    @EnvironmentObject private var recorder: CaptureRecorder
    @EnvironmentObject private var playback: AudioPlayback
    @EnvironmentObject private var sync: NodeSyncController
    @EnvironmentObject private var folder: FolderSyncController
    @EnvironmentObject private var workspace: NodeWorkspaceStore
    @AppStorage("sync.route") private var route = SyncRoute.current.rawValue
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.colorScheme) private var scheme
    @Environment(\.dismiss) private var dismiss
    @State private var renaming = false
    @State private var title = ""
    @State private var removing = false
    @State private var exporting = false
    @State private var export: ShareFiles?
    private var recording: Recording? { recorder.recordings.first { $0.id == recordingID } }
    private var isCurrent: Bool { playback.recordingID == recordingID }

    var body: some View {
        Group {
            if let record = recording {
                ScrollView {
                    VStack(alignment: .leading, spacing: 30) {
                        VStack(alignment: .leading, spacing: 12) {
                            Text(record.displayTitle).font(.largeTitle.weight(.semibold))
                                .fixedSize(horizontal: false, vertical: true)
                            Text(record.startedAt, format: .dateTime.year().month().day().hour().minute())
                                .font(.subheadline).foregroundStyle(.secondary)
                        }
                        VStack(spacing: 26) {
                            Image(systemName: "waveform").font(.system(size: 54, weight: .ultraLight))
                                .foregroundStyle(PhoneStyle.accent).padding(.top, 22).accessibilityHidden(true)
                            Text(record.duration.timeLabel)
                                .font(.system(size: 52, weight: .light, design: .rounded)).monospacedDigit()
                            VStack(spacing: 8) {
                                Slider(value: Binding(get: { isCurrent ? playback.position : 0 },
                                                      set: { playback.seek(to: $0) }),
                                       in: 0...max(0.1, record.duration))
                                    .disabled(!isCurrent).tint(PhoneStyle.accent)
                                    .accessibilityLabel("回放位置")
                                HStack {
                                    Text((isCurrent ? playback.position : 0).timeLabel)
                                    Spacer()
                                    Text(record.duration.timeLabel)
                                }.font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                            }
                            HStack(spacing: 32) {
                                Button { playback.skip(by: -15) } label: {
                                    Image(systemName: "gobackward.15").font(.system(size: 24)).frame(width: 44, height: 44)
                                }.disabled(!isCurrent).accessibilityLabel("后退十五秒")
                                Button {
                                    recorder.togglePlayback(record)
                                } label: {
                                    Image(systemName: isCurrent && playback.isPlaying ? "pause.fill" : "play.fill")
                                        .font(.system(size: 25)).foregroundStyle(AppPalette.actionText(scheme))
                                        .frame(width: 76, height: 76).background(AppPalette.actionFill(scheme), in: Circle())
                                }.disabled(!record.isPlayable || recorder.active != nil)
                                    .accessibilityLabel(isCurrent && playback.isPlaying ? "暂停回放" : "播放原音")
                                Button { playback.skip(by: 15) } label: {
                                    Image(systemName: "goforward.15").font(.system(size: 24)).frame(width: 44, height: 44)
                                }.disabled(!isCurrent).accessibilityLabel("前进十五秒")
                            }.buttonStyle(.plain)
                            Menu {
                                ForEach([Float(1), 1.25, 1.5, 2], id: \.self) { value in
                                    Button("\(value.formatted()) 倍速") { playback.setRate(value) }
                                }
                            } label: {
                                Text("\(playback.rate.formatted()) ×").font(.subheadline.monospacedDigit())
                                    .padding(.horizontal, 16).padding(.vertical, 9)
                                    .background(PhoneStyle.line.opacity(0.5), in: Capsule())
                            }.accessibilityLabel(LF("回放速度 %@ 倍", playback.rate.formatted()))
                            if let problem = playback.problem {
                                Text(L(problem)).font(.footnote).foregroundStyle(.secondary)
                            }
                        }.frame(maxWidth: .infinity).padding(22)
                            .background(PhoneStyle.surface, in: RoundedRectangle(cornerRadius: 28))
                        VStack(spacing: 14) {
                            detail("状态", value: record.statusLabel)
                            detail("来源", value: record.sourceLabel)
                            detail("大小", value: ByteCountFormatter.string(fromByteCount: record.byteCount, countStyle: .file))
                            if record.isPlayable {
                                detail("同步", value: L(route == SyncRoute.folder.rawValue ? folder.label(for: record.id)
                                    : route == SyncRoute.direct.rawValue ? sync.label(for: record.id).replacingOccurrences(of: "节点", with: "电脑")
                                    : "保存在手机"))
                            }
                        }.font(.subheadline)
                        if record.isPlayable {
                            VStack(alignment: .leading, spacing: 10) {
                                if route == SyncRoute.direct.rawValue && workspace.connected && workspace.records.contains(where: { $0.id == record.id }) {
                                    TranscriptionView(store: workspace, recordingID: record.id)
                                    NavigationLink {
                                        NodeDocumentEditor(store: workspace, path: "v1/recordings/\(record.id.uuidString)/transcript", title: "文字记录", hint: "结合原音校对文字，人工修改会保留。")
                                    } label: {
                                        Label("查看与编辑文字", systemImage: "text.alignleft")
                                    }
                                } else {
                                    Label("原音保存在手机", systemImage: "waveform").font(.subheadline.weight(.medium))
                                    Text("电脑接收后可进行转写。资料夹模式下，请在电脑查看文字与任务状态。")
                                        .font(.footnote).foregroundStyle(.secondary)
                                    NavigationLink("查看同步状态") { PhoneSyncScreen() }.font(.subheadline)
                                }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }
                        if let note = record.note {
                            Label(note, systemImage: "info.circle").font(.footnote).foregroundStyle(.secondary)
                        }
                    }.padding(26)
                }
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Menu {
                            Button("重命名", systemImage: "pencil") { title = record.title ?? ""; renaming = true }
                            ShareLink(item: recorder.library.audioURL(for: record)) {
                                Label("导出原音", systemImage: "square.and.arrow.up")
                            }.disabled(!record.isPlayable)
                            Button("导出录音与信息", systemImage: "doc.on.doc") { exportRecording(record) }
                                .disabled(!record.isPlayable || exporting)
                            Button("移到最近删除", systemImage: "trash", role: .destructive) { removing = true }
                        } label: {
                            if exporting { ProgressView() }
                            else { Image(systemName: "ellipsis").frame(width: 44, height: 44) }
                        }.accessibilityLabel("录音操作")
                    }
                }
                .alert("重命名", isPresented: $renaming) {
                    TextField("录音标题", text: $title)
                    Button("取消", role: .cancel) {}
                    Button("保存") { recorder.rename(record, to: title) }
                }
                .confirmationDialog("移到最近删除？", isPresented: $removing) {
                    Button("移到最近删除", role: .destructive) {
                        recorder.moveToTrash(record)
                        if recorder.recordings.first(where: { $0.id == recordingID })?.trashedAt != nil { dismiss() }
                    }
                } message: { Text("原音会保留，可以随时恢复。") }
            } else {
                ContentUnavailableView("找不到这条录音", systemImage: "waveform")
            }
        }.background(PhoneStyle.canvas).navigationBarTitleDisplayMode(.inline)
            .onDisappear { playback.stop() }
            .sheet(item: $export) { item in RecordingShareSheet(urls: item.urls) }
    }

    private func detail(_ label: String, value: String) -> some View {
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 6))
            : AnyLayout(HStackLayout(alignment: .top, spacing: 16))
        return layout {
            Text(LocalizedStringKey(label)).foregroundStyle(.secondary)
            if !typeSize.isAccessibilitySize { Spacer() }
            Text(value).fixedSize(horizontal: false, vertical: true)
        }
    }
    private func exportRecording(_ record: Recording) {
        exporting = true
        let library = recorder.library
        let destination = FileManager.default.temporaryDirectory.appendingPathComponent("VoiceMemory-Exports")
            .appendingPathComponent(UUID().uuidString)
        Task {
            do {
                let output = try await Task.detached(priority: .userInitiated) {
                    try RecordingExport.prepare(id: record.id, from: library, to: destination)
                }.value
                export = ShareFiles(urls: output.files)
            } catch { recorder.problem = error.localizedDescription }
            exporting = false
        }
    }
}

private struct ShareFiles: Identifiable {
    let id = UUID()
    let urls: [URL]
}

private struct RecordingShareSheet: UIViewControllerRepresentable {
    let urls: [URL]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: urls, applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
