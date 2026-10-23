import Combine
import Foundation

enum SyncRoute: String, CaseIterable, Identifiable {
    case local, folder, direct
    var id: String { rawValue }
    var title: String { switch self { case .local: "仅保存在手机"; case .folder: "资料夹同步"; case .direct: "电脑直连" } }
    static var current: Self {
        if let raw = UserDefaults.standard.string(forKey: "sync.route"), let route = Self(rawValue: raw) { return route }
        return UserDefaults.standard.string(forKey: "node.endpoint")?.isEmpty == false ? .direct : .local
    }
}

@MainActor
final class FolderSyncController: ObservableObject {
    static let shared = FolderSyncController()
    @Published private(set) var folderName = ""
    @Published private(set) var busy = false
    @Published private(set) var jobs: [TransferJob] = []
    @Published private(set) var lastChecked: Date?
    @Published var problem: String?
    var active = true
    var configured: Bool { selection != nil }
    var statusTitle: String {
        if !configured { return "待选择资料夹" }
        if problem != nil { return "同步需要处理" }
        if busy { return "正在检查资料夹" }
        return lastChecked == nil ? "等待检查资料夹" : "资料夹已检查"
    }
    private struct Selection: Codable {
        let bookmark: Data
        let name: String
        let id: String
    }
    private var selection: Selection?
    private let selectionFile: URL?
    private var folderID: String { selection?.id ?? "" }
    private let journal: TransferJournal
    private var timer: Timer?
    private var subscriptions = Set<AnyCancellable>()
    private init() {
        let defaultSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("VoiceMemory")
        #if DEBUG
        let isolated = ProcessInfo.processInfo.environment["VOICE_MEMORY_CLIENT_DATA"].map { URL(fileURLWithPath: $0) }
        #else
        let isolated: URL? = nil
        #endif
        selectionFile = isolated?.appendingPathComponent("folder-selection.json")
        journal = TransferJournal(root: (isolated ?? defaultSupport).appendingPathComponent("FolderTransfers"))
        do {
            if let selectionFile {
                if FileManager.default.fileExists(atPath: selectionFile.path) {
                    selection = try JSONDecoder().decode(Selection.self, from: Data(contentsOf: selectionFile))
                }
            } else if let bookmark = UserDefaults.standard.data(forKey: "folder.bookmark") {
                selection = Selection(bookmark: bookmark, name: UserDefaults.standard.string(forKey: "folder.name") ?? "",
                                      id: UserDefaults.standard.string(forKey: "folder.id") ?? "")
            }
            folderName = selection?.name ?? ""
        } catch { problem = "资料夹授权暂时无法读取，请重新选择资料夹。原音仍保留。" }
        do { jobs = try journal.load() }
        catch { problem = "资料夹同步队列无法读取，现有记录与原音均已保留。" }
        timer = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { [weak self] _ in
            Task { @MainActor in if self?.active == true { await self?.refresh() } }
        }
        #if os(iOS)
        CaptureRecorder.shared.$recordings.debounce(for: .seconds(1), scheduler: RunLoop.main)
            .sink { [weak self] _ in Task { await self?.refresh() } }.store(in: &subscriptions)
        #endif
        #if DEBUG
        if let path = ProcessInfo.processInfo.environment["VOICE_MEMORY_QA_FOLDER"] {
            do { try select(URL(fileURLWithPath: path)) }
            catch { problem = error.localizedDescription }
        }
        #endif
    }
    func select(_ url: URL) throws {
        guard !busy else { throw NodeFailure(message: "资料正在同步，请完成后再更换资料夹。") }
        let access = url.startAccessingSecurityScopedResource()
        defer { if access { url.stopAccessingSecurityScopedResource() } }
        #if os(macOS)
        let options: URL.BookmarkCreationOptions = .withSecurityScope
        #else
        let options: URL.BookmarkCreationOptions = .minimalBookmark
        #endif
        let data = try url.bookmarkData(options: options, includingResourceValuesForKeys: nil, relativeTo: nil)
        let previous = try? resolve(allowStale: true).standardizedFileURL.resolvingSymlinksInPath()
        let sameFolder = previous == url.standardizedFileURL.resolvingSymlinksInPath() && !folderID.isEmpty
        let next = Selection(bookmark: data, name: url.lastPathComponent, id: sameFolder ? folderID : UUID().uuidString)
        if let selectionFile {
            try FileManager.default.createDirectory(at: selectionFile.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONEncoder().encode(next).write(to: selectionFile, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: selectionFile.path)
        } else {
            UserDefaults.standard.set(next.bookmark, forKey: "folder.bookmark")
            UserDefaults.standard.set(next.name, forKey: "folder.name")
            UserDefaults.standard.set(next.id, forKey: "folder.id")
        }
        selection = next; folderName = next.name; problem = nil; lastChecked = nil
        #if os(iOS)
        UserDefaults.standard.set(SyncRoute.folder.rawValue, forKey: "sync.route")
        NotificationCenter.default.post(name: .nodeConfigurationChanged, object: nil)
        #endif
        Task { await refresh() }
    }
    func refresh() async {
        guard !busy, configured else { return }
        #if os(iOS)
        guard SyncRoute.current == .folder else { return }
        #endif
        busy = true
        defer { busy = false }
        do {
            let url = try resolve()
            let target = "folder:" + folderID
            let journal = self.journal
            #if os(iOS)
            let library = CaptureRecorder.shared.library
            #else
            let library = NodeSyncController.shared.library
            #endif
            let result = try await Task.detached(priority: .utility) {
                let accessed = url.startAccessingSecurityScopedResource()
                defer { if accessed { url.stopAccessingSecurityScopedResource() } }
                let exchange = FolderExchange(root: url)
                var current = try journal.load()
                var firstError: String?
                func persist(_ job: TransferJob) throws {
                    try journal.save(job)
                    current.removeAll { $0.id == job.id }; current.append(job)
                }
                #if os(iOS)
                for record in try library.load().recordings where record.isPlayable && record.trashedAt == nil {
                    var job = current.first { $0.target == target && $0.recordingID == record.id }
                        ?? TransferJob(recordingID: record.id, target: target)
                    guard job.state != .delivered else { continue }
                    do {
                        // A valid receipt is sufficient even if the drive has since removed the package.
                        // Without it, check for missing files again instead of waiting forever.
                        let existingReceipt = try Self.coordinated(url, writing: false) { try FolderExchange(root: $0).receipt(record.id) }
                        if let existingReceipt, job.audioSHA256 != nil {
                            try job.acknowledge(existingReceipt); try persist(job); continue
                        }
                        let available = try job.audioSHA256.map { hash in
                            try Self.coordinated(url, writing: false) { try FolderExchange(root: $0).hasPublication(record.id, audioSHA256: hash) }
                        } ?? false
                        if !available {
                            let temp = FileManager.default.temporaryDirectory.appendingPathComponent("folder-export-" + UUID().uuidString)
                            defer { try? FileManager.default.removeItem(at: temp) }
                            let output = try RecordingExport.prepare(id: record.id, from: library, to: temp)
                            job.attempts += 1
                            job.audioSHA256 = try Self.coordinated(url, writing: true) { root in
                                try FolderExchange(root: root).publish(output)
                            }
                            job.state = .awaitingReceipt; job.updatedAt = .now
                            try persist(job)
                        }
                        let receipt = try Self.coordinated(url, writing: false) { root in
                            try FolderExchange(root: root).receipt(record.id)
                        }
                        if let receipt { try job.acknowledge(receipt) }
                        else { job.state = .awaitingReceipt; job.message = nil }
                        try persist(job)
                    } catch {
                        job.fail(error.localizedDescription); try? persist(job)
                        if firstError == nil { firstError = error.localizedDescription }
                    }
                }
                #else
                let published = try Self.coordinated(url, writing: false) { try FolderExchange(root: $0).packageIDs() }
                let received = current.filter { $0.target == target && $0.audioSHA256 != nil }.map(\.recordingID)
                let ids = Set(published).union(received).sorted { $0.uuidString < $1.uuidString }
                for id in ids {
                    var job = current.first { $0.target == target && $0.recordingID == id }
                        ?? TransferJob(recordingID: id, target: target)
                    do {
                        if let hash = job.audioSHA256 {
                            let receipt = TransferReceipt(recordingID: id, audioSHA256: hash, nodeID: "desktop-library", durable: true)
                            let repaired = try Self.coordinated(url, writing: true) {
                                try FolderExchange(root: $0).restoreReceipt(receipt, from: library)
                            }
                            if repaired || job.state != .delivered {
                                try job.acknowledge(receipt); try persist(job)
                            }
                            continue
                        }
                        let package = try exchange.package(id)
                        let manifest = try Self.coordinated(package.appendingPathComponent("manifest.json"), writing: false) { try Data(contentsOf: $0) }
                        let temp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".caf")
                        defer { try? FileManager.default.removeItem(at: temp) }
                        try Self.coordinated(package.appendingPathComponent(id.uuidString + ".caf"), writing: false) {
                            try FileManager.default.copyItem(at: $0, to: temp)
                        }
                        let accepted = try RecordingIngress.accept(audio: temp, manifestData: manifest, into: library)
                        let receipt = TransferReceipt(recordingID: id, audioSHA256: accepted.audioSHA256, nodeID: "desktop-library", durable: true)
                        _ = try Self.coordinated(url, writing: true) { try FolderExchange(root: $0).restoreReceipt(receipt, from: library) }
                        job.audioSHA256 = receipt.audioSHA256; job.attempts += 1
                        try job.acknowledge(receipt); try persist(job)
                    } catch {
                        job.fail(error.localizedDescription); try? persist(job)
                        if firstError == nil { firstError = error.localizedDescription }
                    }
                }
                #endif
                return (current, firstError)
            }.value
            jobs = result.0; problem = result.1; lastChecked = .now
            #if os(macOS)
            NodeSyncController.shared.refresh()
            #endif
        } catch { problem = LF("资料夹暂不可用，原音仍保留。 %@", error.localizedDescription) }
    }
    func label(for id: UUID) -> String {
        guard let job = jobs.first(where: { $0.target == "folder:" + folderID && $0.recordingID == id }) else { return "保存在手机" }
        switch job.state {
        case .delivered: return "电脑已接收"
        case .awaitingReceipt: return "等待电脑接收"
        case .failed: return "需要处理"
        default: return "等待同步"
        }
    }
    var delivered: Int { jobs.filter { $0.target == "folder:" + folderID && $0.state == .delivered }.count }
    var waiting: Int { jobs.filter { $0.target == "folder:" + folderID && $0.state != .delivered }.count }
    var failedJobs: [TransferJob] {
        jobs.filter { $0.target == "folder:" + folderID && $0.state == .failed }.sorted { $0.updatedAt > $1.updatedAt }
    }
    func title(for id: UUID) -> String {
        #if os(iOS)
        let record = CaptureRecorder.shared.recordings.first { $0.id == id }
        return record?.displayTitle ?? LF("录音 %@", String(id.uuidString.prefix(8)))
        #else
        let record = try? NodeSyncController.shared.library.recording(id: id)
        return record?.title ?? LF("录音 %@", String(id.uuidString.prefix(8)))
        #endif
    }
    #if os(iOS)
    struct RecoveryPlan: Sendable {
        let target: String
        let folderName: String
        let snapshot: FolderExchange.RecoverySnapshot
    }
    func previewRecovery(_ job: TransferJob) async throws -> RecoveryPlan {
        guard !busy, job.target == "folder:" + folderID, SyncRoute.current == .folder else {
            throw FolderExchange.RecoveryError.changed
        }
        busy = true; defer { busy = false }
        let url = try resolve(), library = CaptureRecorder.shared.library
        let snapshot = try await Task.detached(priority: .utility) {
            let access = url.startAccessingSecurityScopedResource()
            defer { if access { url.stopAccessingSecurityScopedResource() } }
            let temp = FileManager.default.temporaryDirectory.appendingPathComponent("folder-recovery-" + UUID().uuidString)
            defer { try? FileManager.default.removeItem(at: temp) }
            let output = try RecordingExport.prepare(id: job.recordingID, from: library, to: temp)
            return try Self.coordinated(url, writing: false) { try FolderExchange(root: $0).previewRecovery(output) }
        }.value
        return RecoveryPlan(target: job.target, folderName: folderName, snapshot: snapshot)
    }
    func recover(_ plan: RecoveryPlan) async throws {
        guard !busy, plan.target == "folder:" + folderID, SyncRoute.current == .folder else {
            throw FolderExchange.RecoveryError.changed
        }
        busy = true; defer { busy = false }
        let url = try resolve(), library = CaptureRecorder.shared.library, journal = self.journal
        let updated = try await Task.detached(priority: .utility) {
            let access = url.startAccessingSecurityScopedResource()
            defer { if access { url.stopAccessingSecurityScopedResource() } }
            let temp = FileManager.default.temporaryDirectory.appendingPathComponent("folder-recovery-" + UUID().uuidString)
            defer { try? FileManager.default.removeItem(at: temp) }
            let output = try RecordingExport.prepare(id: plan.snapshot.recordingID, from: library, to: temp)
            var job = try journal.load().first { $0.target == plan.target && $0.recordingID == plan.snapshot.recordingID }
                ?? TransferJob(recordingID: plan.snapshot.recordingID, target: plan.target)
            guard job.state != .delivered else { throw FolderExchange.RecoveryError.receiptExists }
            _ = try Self.coordinated(url, writing: true) { try FolderExchange(root: $0).recover(output, expected: plan.snapshot) }
            job.audioSHA256 = plan.snapshot.originalSHA256; job.state = .awaitingReceipt
            job.message = nil; job.nextAttemptAt = nil; job.attempts += 1; job.updatedAt = .now
            try journal.save(job)
            return try journal.load()
        }.value
        jobs = updated; problem = failedJobs.first?.message; lastChecked = .now
    }
    #endif
    private func resolve(allowStale: Bool = false) throws -> URL {
        guard let data = selection?.bookmark else { throw NodeFailure(message: "请先选择资料夹。") }
        var stale = false
        #if os(macOS)
        let options: URL.BookmarkResolutionOptions = [.withSecurityScope, .withoutUI]
        #else
        let options: URL.BookmarkResolutionOptions = [.withoutUI]
        #endif
        let url = try URL(resolvingBookmarkData: data, options: options, relativeTo: nil, bookmarkDataIsStale: &stale)
        if stale && !allowStale { throw NodeFailure(message: "资料夹权限已变化，请重新选择原来的资料夹。") }
        return url
    }
    nonisolated private static func coordinated<T>(_ url: URL, writing: Bool, work: (URL) throws -> T) throws -> T {
        var coordinationError: NSError?
        var result: Result<T, Error>?
        let coordinator = NSFileCoordinator()
        if writing {
            coordinator.coordinate(writingItemAt: url, options: .forMerging, error: &coordinationError) { value in
                result = Result { try work(value) }
            }
        } else {
            coordinator.coordinate(readingItemAt: url, options: [], error: &coordinationError) { value in
                result = Result { try work(value) }
            }
        }
        if let coordinationError { throw coordinationError }
        guard let result else { throw NodeFailure(message: "资料夹尚未准备好，请稍后重试。") }
        return try result.get()
    }
}
