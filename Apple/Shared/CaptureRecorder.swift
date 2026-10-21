import AVFoundation
import Combine
import Foundation
import OSLog
#if os(watchOS)
import WatchKit
#else
import UIKit
#endif

@MainActor
final class CaptureRecorder: NSObject, ObservableObject, AVAudioRecorderDelegate {
    static let shared = CaptureRecorder()
    @Published private(set) var active: Recording?
    @Published private(set) var recordings: [Recording] = []
    @Published private(set) var elapsed: TimeInterval = 0
    @Published private(set) var level: Float = 0
    @Published private(set) var busy = false
    @Published private(set) var capturing = false
    @Published private(set) var paused = false
    @Published private(set) var ready = false
    @Published var message: String?
    @Published var problem: String?
    @Published private(set) var libraryProblem: String?
    @Published var foreground = false
    let library: RecordingLibrary
    let playback = AudioPlayback()
    private var recorder: AVAudioRecorder?
    private var timer: Timer?
    private var gate = TriggerGate()
    private var subscriptions = Set<AnyCancellable>()
    private let logger = Logger(subsystem: Bundle.main.bundleIdentifier ?? "VoiceMemory", category: "Capture")
    var permissionDenied: Bool { AVAudioApplication.shared.recordPermission == .denied }

    override init() {
        let base = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        library = RecordingLibrary(root: base.appendingPathComponent("Recordings"))
        super.init()
        ready = AVAudioApplication.shared.recordPermission == .granted
        recover()
        playback.$problem.compactMap { $0 }.sink { [weak self] error in
            self?.problem = error
        }.store(in: &subscriptions)
        NotificationCenter.default.publisher(for: AVAudioSession.interruptionNotification)
            .receive(on: RunLoop.main).sink { [weak self] event in
                guard let type = event.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                      type == AVAudioSession.InterruptionType.began.rawValue else { return }
                self?.playback.stop()
                self?.interrupt("系统中断了录音，已保留可读取的原音。")
            }.store(in: &subscriptions)
        NotificationCenter.default.publisher(for: AVAudioSession.mediaServicesWereResetNotification)
            .receive(on: RunLoop.main).sink { [weak self] _ in
                self?.interrupt("音频服务已重置，请回听已保存内容。")
            }.store(in: &subscriptions)
    }

    func prepare() async {
        ready = await AVAudioApplication.requestRecordPermission()
        message = ready ? nil : "请在系统设置中允许麦克风访问。"
        if !ready { problem = message }
    }

    func toggleFromShortcut() async throws {
        // App Intents may arrive before the scene receives its active transition.
        for _ in 0..<80 {
            if foreground { break }
            try await Task.sleep(for: .milliseconds(100))
        }
        guard foreground else { throw RecordingIntentError.notForeground }
        guard ready else { throw RecordingIntentError.notPrepared }
        try toggle(origin: "shortcut")
    }

    func toggleFromButton() {
        do { try toggle(origin: "button") }
        catch { message = error.localizedDescription; problem = message }
    }

    func togglePause() {
        guard let recorder, var recording = active, gate.begin() else { return }
        busy = true
        defer { busy = false; gate.complete() }
        do {
            if paused {
                try AVAudioSession.sharedInstance().setActive(true)
                guard recorder.record() else {
                    throw RecordingIntentError.captureFailed("暂时无法继续录音，可以先保存已录内容。")
                }
                recording.status = .recording
                paused = false
                capturing = true
            } else {
                elapsed = recorder.currentTime
                recorder.pause()
                recording.status = .paused
                paused = true
                capturing = false
                level = 0
            }
            recording.duration = elapsed
            active = recording
            try library.save(recording)
        } catch { problem = error.localizedDescription }
    }

    func rename(_ recording: Recording, to title: String) {
        do { try library.rename(id: recording.id, title: title); reload() }
        catch { problem = error.localizedDescription }
    }

    func moveToTrash(_ recording: Recording) {
        do {
            try library.moveToTrash(id: recording.id)
            if playback.recordingID == recording.id { playback.stop() }
            reload()
        } catch { problem = error.localizedDescription }
    }

    func restore(_ recording: Recording) {
        do { try library.restore(id: recording.id); reload() }
        catch { problem = error.localizedDescription }
    }

    func togglePlayback(_ recording: Recording) {
        guard active == nil else { return }
        playback.toggle(recording, url: library.audioURL(for: recording))
    }

    private func toggle(origin: String) throws {
        guard gate.begin() else { throw RecordingIntentError.busy }
        busy = true
        defer { busy = false; gate.complete() }
        do {
            if active != nil { try finish(status: .saved) }
            else { try start(origin: origin) }
        } catch {
            message = error.localizedDescription
            problem = message
            if error is RecordingIntentError { throw error }
            throw RecordingIntentError.captureFailed(error.localizedDescription)
        }
    }

    private func start(origin: String) throws {
        guard foreground, ready else { throw RecordingIntentError.notPrepared }
        playback.stop()
        let capacity = try? library.root.deletingLastPathComponent()
            .resourceValues(forKeys: [.volumeAvailableCapacityKey]).volumeAvailableCapacity
        if let capacity, capacity < 10_000_000 {
            throw RecordingIntentError.captureFailed("设备剩余空间不足，请先腾出一些空间。")
        }
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .default)
        try session.setActive(true)
        var recording = Recording(trigger: origin)
        #if os(watchOS)
        recording.sourceDevice = .watch
        #else
        recording.sourceDevice = .phone
        #endif
        do {
            try library.save(recording) // Persist identity before opening the microphone.
            let capture = try AVAudioRecorder(url: library.audioURL(for: recording), settings: [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVSampleRateKey: 16_000,
                AVNumberOfChannelsKey: 1,
                AVLinearPCMBitDepthKey: 16,
                AVLinearPCMIsFloatKey: false,
                AVLinearPCMIsBigEndianKey: false
            ])
            capture.delegate = self
            capture.isMeteringEnabled = true
            guard capture.prepareToRecord(), capture.record(), capture.isRecording else {
                throw RecordingIntentError.captureFailed("系统未启动麦克风")
            }
            recorder = capture
            logger.debug("Capture started; input available: \(session.isInputAvailable), session sample rate: \(session.sampleRate), input ports: \(session.currentRoute.inputs.map(\.portType.rawValue).joined(separator: ","), privacy: .public)")
            capturing = true
            paused = false
            active = recording
            elapsed = 0
            message = nil
            feedback(start: true)
            timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
                Task { @MainActor in self?.tick() }
            }
            reload()
        } catch {
            recording.status = .failed
            recording.note = error.localizedDescription
            try? library.save(recording)
            try? session.setActive(false)
            throw error
        }
    }

    private func tick() {
        if let recorder, recorder.isRecording {
            elapsed = recorder.currentTime
            recorder.updateMeters()
            level = max(0, min(1, pow(10, recorder.averagePower(forChannel: 0) / 35)))
        }
    }

    private func finish(status: RecordingStatus, note: String? = nil) throws {
        guard var recording = active else { return }
        let duration = recorder?.currentTime ?? elapsed
        recorder?.delegate = nil
        recorder?.stop()
        recorder = nil
        capturing = false
        paused = false
        defer { try? AVAudioSession.sharedInstance().setActive(false) }
        timer?.invalidate()
        timer = nil
        level = 0
        recording.duration = duration
        recording.status = status
        recording.note = note
        let url = library.audioURL(for: recording)
        // Confirm a readable audio file before announcing "saved".
        do {
            let file = try AVAudioFile(forReading: url)
            recording.duration = Double(file.length) / file.processingFormat.sampleRate
            recording.byteCount = (try url.resourceValues(forKeys: [.fileSizeKey])).fileSize.map(Int64.init) ?? 0
            logger.debug("Capture stopped; elapsed: \(duration), readable frames: \(file.length), bytes: \(recording.byteCount)")
            guard recording.duration > 0 else {
                throw RecordingIntentError.captureFailed("这次没有录到有效声音，请重新试录。")
            }
        } catch {
            let failure = "录音已停止，未能确认有效音频。原文件已保留，请重新试录。"
            recording.status = .failed
            recording.duration = 0
            recording.note = failure
            // If this write fails, keep the stopped capture available for a save retry.
            active = recording
            try library.save(recording)
            active = nil
            elapsed = 0
            reload()
            throw RecordingIntentError.captureFailed(failure)
        }
        active = recording // A failed metadata write can be retried without a new capture.
        try library.save(recording)
        active = nil
        elapsed = recording.duration
        message = note ?? "原音已保存"
        feedback(start: false)
        reload()
    }

    private func interrupt(_ reason: String) {
        guard active != nil else { return }
        do { try finish(status: .interrupted, note: reason) }
        catch {
            message = error.localizedDescription
            problem = message
        }
    }

    func reload() {
        ready = AVAudioApplication.shared.recordPermission == .granted
        do {
            let result = try library.load()
            recordings = result.recordings
            libraryProblem = result.unreadable.isEmpty ? nil : "\(result.unreadable.count) 条录音的信息暂时无法读取，原音已保留。"
        } catch { libraryProblem = "暂时无法读取录音，请稍后重试。" }
    }

    private func recover() {
        do {
            for var recording in try library.load().recordings where recording.isInProgress {
                let url = library.audioURL(for: recording)
                if let file = try? AVAudioFile(forReading: url), file.length > 0 {
                    recording.duration = Double(file.length) / file.processingFormat.sampleRate
                    recording.byteCount = Int64((try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0)
                    recording.status = .recovered
                    recording.note = "上次录音未正常结束，已恢复可读取原音。请回听检查。"
                } else {
                    recording.status = .failed
                    recording.note = "未能确认可读取的音频，原文件保留。"
                }
                try library.save(recording)
            }
            reload()
        } catch { message = "恢复尚未完成：\(error.localizedDescription)" }
    }

    private func feedback(start: Bool) {
        #if os(watchOS)
        WKInterfaceDevice.current().play(start ? .start : .stop)
        #else
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        #endif
    }

    nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
        let identity = ObjectIdentifier(recorder)
        Task { @MainActor in
            guard self.recorder.map(ObjectIdentifier.init) == identity else { return }
            self.interrupt("录音写入中断，请检查设备可用空间并回听原音。")
        }
    }
    nonisolated func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        let identity = ObjectIdentifier(recorder)
        Task { @MainActor in
            guard self.recorder.map(ObjectIdentifier.init) == identity else { return }
            self.interrupt("录音已由系统停止，请回听确认。")
        }
    }
}
