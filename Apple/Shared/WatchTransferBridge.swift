import Combine
import Foundation
import WatchConnectivity

@MainActor
final class WatchTransferBridge: NSObject, ObservableObject, WCSessionDelegate {
    static let shared = WatchTransferBridge()
    @Published private(set) var paired = false
    @Published private(set) var installed = false
    @Published private(set) var jobs: [TransferJob] = []
    @Published private(set) var problem: String?
    @Published private(set) var preferenceProblem: String?
    private let journal: TransferJournal
    private var subscriptions = Set<AnyCancellable>()
    private var preparing = Set<UUID>()
    private var processingIncoming = Set<URL>()
    private var timer: Timer?
    private nonisolated static var incomingRoot: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("WatchIncoming")
    }
    private var outgoingRoot: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("WatchOutgoing")
    }
    override init() {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        journal = TransferJournal(root: support.appendingPathComponent("WatchTransfers"))
        super.init()
        do { jobs = try journal.load() } catch { problem = "传输队列暂时无法读取，原音已保留。" }
        if WCSession.isSupported() {
            WCSession.default.delegate = self
            WCSession.default.activate()
        }
        #if os(watchOS)
        CaptureRecorder.shared.$recordings.debounce(for: .milliseconds(500), scheduler: RunLoop.main)
            .sink { [weak self] _ in self?.refresh() }.store(in: &subscriptions)
        timer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        #else
        recoverIncoming()
        #endif
    }
    func refresh() {
        guard WCSession.isSupported(), WCSession.default.activationState == .activated else { return }
        #if os(iOS)
        paired = WCSession.default.isPaired
        installed = WCSession.default.isWatchAppInstalled
        if paired && installed { sendLanguagePreference() }
        recoverIncoming()
        #else
        installed = WCSession.default.isCompanionAppInstalled
        paired = installed
        applyLanguagePreference(WCSession.default.receivedApplicationContext["appLanguage"] as? String)
        enqueueSaved()
        #endif
    }
    #if os(iOS)
    private func sendLanguagePreference() {
        let language = AppLanguage.current.rawValue
        guard WCSession.default.applicationContext["appLanguage"] as? String != language else { return }
        do {
            try WCSession.default.updateApplicationContext(["appLanguage": language])
            preferenceProblem = nil
        } catch {
            preferenceProblem = "语言设置尚未发送到手表，连接恢复后会重试。"
        }
    }
    #else
    private func applyLanguagePreference(_ value: String?) {
        guard let value, AppLanguage(rawValue: value) != nil,
              UserDefaults.standard.string(forKey: "app.language") != value else { return }
        UserDefaults.standard.set(value, forKey: "app.language")
    }
    #endif
    func label(for id: UUID) -> String {
        guard let job = jobs.first(where: { $0.recordingID == id }) else { return "保存在手表" }
        switch job.state {
        case .queued: return "等待传到手机"
        case .sending: return "传输已排队"
        case .awaitingReceipt: return "等待手机确认"
        case .delivered: return "手机已接收"
        case .failed: return "等待重试"
        }
    }
    private func persist(_ job: TransferJob) throws {
        try journal.save(job)
        jobs.removeAll { $0.id == job.id }; jobs.append(job)
    }
    #if os(watchOS)
    private func enqueueSaved() {
        guard installed else { return }
        let activeTransfers = Set(WCSession.default.outstandingFileTransfers.compactMap { $0.file.metadata?["recordingID"] as? String })
        for record in CaptureRecorder.shared.recordings where record.isPlayable && record.trashedAt == nil {
            var job = jobs.first { $0.recordingID == record.id } ?? TransferJob(recordingID: record.id, target: "paired-phone")
            guard job.state != .delivered, !preparing.contains(record.id), !activeTransfers.contains(record.id.uuidString) else { continue }
            if let next = job.nextAttemptAt, next > .now { continue }
            if job.state == .awaitingReceipt && Date.now.timeIntervalSince(job.updatedAt) < 120 { continue }
            preparing.insert(record.id)
            let library = CaptureRecorder.shared.library
            let destination = outgoingRoot.appendingPathComponent(job.id)
            Task {
                defer { preparing.remove(record.id) }
                do {
                    let output = try await Task.detached {
                        if FileManager.default.fileExists(atPath: destination.appendingPathComponent("\(record.id.uuidString).json").path) {
                            return RecordingExport(audioURL: destination.appendingPathComponent(record.audioFilename),
                                                   manifestURL: destination.appendingPathComponent("\(record.id.uuidString).json"))
                        }
                        return try RecordingExport.prepare(id: record.id, from: library, to: destination)
                    }.value
                    let data = try Data(contentsOf: output.manifestURL)
                    let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
                    let manifest = try decoder.decode(RecordingExportManifest.self, from: data)
                    job.audioSHA256 = manifest.audioSHA256; job.state = .sending
                    job.attempts += 1; job.updatedAt = .now; job.message = nil
                    try persist(job)
                    WCSession.default.transferFile(output.audioURL, metadata: ["recordingID": record.id.uuidString, "manifest": data])
                } catch {
                    job.fail(error.localizedDescription)
                    try? persist(job); problem = error.localizedDescription
                }
            }
        }
    }
    func drainBackgroundEvents() async {
        refresh()
        repeat {
            try? await Task.sleep(for: .milliseconds(500))
            if WCSession.default.activationState == .activated && !WCSession.default.hasContentPending { return }
        } while !Task.isCancelled
    }
    #else
    private func recoverIncoming() {
        guard let folders = try? FileManager.default.contentsOfDirectory(at: Self.incomingRoot, includingPropertiesForKeys: nil) else { return }
        for folder in folders where FileManager.default.fileExists(atPath: folder.appendingPathComponent("manifest.json").path) {
            processIncoming(folder)
        }
    }
    private func processIncoming(_ folder: URL) {
        guard processingIncoming.insert(folder).inserted else { return }
        let library = CaptureRecorder.shared.library
        Task {
            defer { processingIncoming.remove(folder) }
            do {
                let receipt = try await Task.detached {
                    try RecordingIngress.accept(audio: folder.appendingPathComponent("audio.caf"),
                                                manifestData: Data(contentsOf: folder.appendingPathComponent("manifest.json")), into: library)
                }.value
                CaptureRecorder.shared.reload()
                WCSession.default.transferUserInfo(["receipt": try JSONEncoder().encode(receipt)])
                try? FileManager.default.removeItem(at: folder)
            } catch { problem = error.localizedDescription }
        }
    }
    #endif
    nonisolated func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        Task { @MainActor in self.refresh() }
    }
    nonisolated func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]) {
        #if os(watchOS)
        let language = applicationContext["appLanguage"] as? String
        Task { @MainActor in self.applyLanguagePreference(language) }
        #endif
    }
    nonisolated func session(_ session: WCSession, didFinish fileTransfer: WCSessionFileTransfer, error: Error?) {
        let id = fileTransfer.file.metadata?["recordingID"] as? String
        let reason = error?.localizedDescription
        Task { @MainActor in
            guard let id, let index = self.jobs.firstIndex(where: { $0.recordingID.uuidString == id }) else { return }
            var job = self.jobs[index]
            guard job.state != .delivered else { return }
            if let reason { job.fail(reason) }
            else { job.state = .awaitingReceipt; job.updatedAt = .now; job.nextAttemptAt = nil }
            try? self.persist(job)
        }
    }
    nonisolated func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any]) {
        guard let data = userInfo["receipt"] as? Data,
              let receipt = try? JSONDecoder().decode(TransferReceipt.self, from: data) else { return }
        Task { @MainActor in
            guard var job = self.jobs.first(where: { $0.recordingID == receipt.recordingID }) else { return }
            do {
                try job.acknowledge(receipt); try self.persist(job)
                try? FileManager.default.removeItem(at: self.outgoingRoot.appendingPathComponent(job.id))
            } catch { self.problem = error.localizedDescription }
        }
    }
    nonisolated func session(_ session: WCSession, didReceive file: WCSessionFile) {
        #if os(iOS)
        guard let data = file.metadata?["manifest"] as? Data else { return }
        let folder = Self.incomingRoot.appendingPathComponent(UUID().uuidString)
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            // Watch Connectivity deletes its temporary file when this callback returns.
            try FileManager.default.copyItem(at: file.fileURL, to: folder.appendingPathComponent("audio.caf"))
            try data.write(to: folder.appendingPathComponent("manifest.json"), options: .atomic)
            Task { @MainActor in self.processIncoming(folder) }
        } catch { Task { @MainActor in self.problem = "未能保存手表传来的录音，发送端原音仍保留。" } }
        #endif
    }
    #if os(iOS)
    nonisolated func sessionWatchStateDidChange(_ session: WCSession) { Task { @MainActor in self.refresh() } }
    nonisolated func sessionDidBecomeInactive(_ session: WCSession) {}
    nonisolated func sessionDidDeactivate(_ session: WCSession) { session.activate() }
    #endif
}
#if os(iOS)
typealias WatchAvailability = WatchTransferBridge
#endif
