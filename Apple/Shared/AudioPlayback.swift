import AVFoundation
import Combine
import Foundation

@MainActor
final class AudioPlayback: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published private(set) var recordingID: UUID?
    @Published private(set) var isPlaying = false
    @Published private(set) var position: TimeInterval = 0
    @Published private(set) var duration: TimeInterval = 0
    @Published private(set) var rate: Float = 1
    @Published var problem: String?
    private var player: AVAudioPlayer?
    private var timer: Timer?

    func toggle(_ recording: Recording, url: URL) {
        guard recording.isPlayable, recording.trashedAt == nil else { return }
        do {
            if recordingID != recording.id {
                stop()
                let audio = try AVAudioPlayer(contentsOf: url)
                audio.delegate = self
                audio.enableRate = true
                audio.rate = rate
                player = audio
                recordingID = recording.id
                duration = audio.duration
            }
            guard let player else { return }
            if isPlaying {
                player.pause()
                position = player.currentTime
                isPlaying = false
                timer?.invalidate()
                timer = nil
                deactivateSession()
            } else {
                if position >= duration {
                    player.currentTime = 0
                    position = 0
                }
                #if !os(macOS)
                try AVAudioSession.sharedInstance().setCategory(.playback, mode: .default)
                try AVAudioSession.sharedInstance().setActive(true)
                #endif
                guard player.play() else { throw PlaybackError.unavailable }
                isPlaying = true
                timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in
                    Task { @MainActor in
                        guard let self, self.isPlaying else { return }
                        self.position = self.player?.currentTime ?? 0
                    }
                }
            }
        } catch {
            stop()
            problem = error.localizedDescription
        }
    }

    func seek(to time: TimeInterval) {
        guard time.isFinite, let player else { return }
        if time >= duration {
            // AVAudioPlayer can wrap an end-position seek back to the start.
            // Keep the displayed endpoint and wait for an explicit replay.
            player.pause()
            player.currentTime = 0
            timer?.invalidate()
            timer = nil
            isPlaying = false
            position = duration
            deactivateSession()
            return
        }
        player.currentTime = min(max(0, time), duration)
        position = player.currentTime
    }

    func skip(by seconds: TimeInterval) { seek(to: position + seconds) }
    func setRate(_ newRate: Float) {
        guard [Float(1), 1.25, 1.5, 2].contains(newRate) else { return }
        rate = newRate
        player?.rate = rate
    }

    func stop() {
        let hadPlayer = player != nil
        timer?.invalidate()
        timer = nil
        player?.delegate = nil
        player?.stop()
        player = nil
        recordingID = nil
        isPlaying = false
        position = 0
        duration = 0
        if hadPlayer { deactivateSession() }
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        let identity = ObjectIdentifier(player)
        Task { @MainActor in
            guard self.player.map(ObjectIdentifier.init) == identity else { return }
            self.timer?.invalidate()
            self.timer = nil
            self.isPlaying = false
            self.position = 0
            self.player?.currentTime = 0
            self.deactivateSession()
            if !flag { self.problem = "回放被中断，请重试。" }
        }
    }

    private func deactivateSession() {
        #if !os(macOS)
        try? AVAudioSession.sharedInstance().setActive(false)
        #endif
    }
    private enum PlaybackError: Error, LocalizedError {
        case unavailable
        var errorDescription: String? { "暂时无法播放这条录音。" }
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        let identity = ObjectIdentifier(player)
        Task { @MainActor in
            guard self.player.map(ObjectIdentifier.init) == identity else { return }
            self.stop()
            self.problem = "这段音频暂时无法播放，原文件已保留。"
        }
    }
}
