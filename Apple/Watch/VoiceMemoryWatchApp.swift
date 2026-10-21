import SwiftUI

@main
struct VoiceMemoryWatchApp: App {
    @StateObject private var recorder = CaptureRecorder.shared
    @StateObject private var transfer = WatchTransferBridge.shared
    @Environment(\.scenePhase) private var phase
    var body: some Scene {
        WindowGroup {
            WatchRecordingView()
                .environmentObject(recorder)
                .environmentObject(recorder.playback)
                .environmentObject(transfer)
                .onChange(of: phase, initial: true) { _, value in
                    recorder.foreground = value == .active
                    if value == .active { recorder.reload(); transfer.refresh() }
                }
        }
        .backgroundTask(.watchConnectivity) { await WatchTransferBridge.shared.drainBackgroundEvents() }
    }
}
