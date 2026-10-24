import Combine
import Foundation
#if os(iOS)
import UIKit
#endif

private struct UploadResult: Sendable {
    let key: String?
    let status: Int
    let data: Data
    let error: String?
}

private final class UploadDelegate: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    let completed: @Sendable (UploadResult, @escaping @Sendable () -> Void) -> Void
    let drained: @Sendable () -> Void
    private let lock = NSLock()
    private var bodies: [Int: Data] = [:]
    private var pending = 0
    private var didDrain = false
    init(completed: @escaping @Sendable (UploadResult, @escaping @Sendable () -> Void) -> Void,
         drained: @escaping @Sendable () -> Void) {
        self.completed = completed; self.drained = drained
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        lock.lock(); defer { lock.unlock() }
        if (bodies[dataTask.taskIdentifier]?.count ?? 0) + data.count <= 65_536 {
            bodies[dataTask.taskIdentifier, default: Data()].append(data)
        }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        lock.lock()
        let data = bodies.removeValue(forKey: task.taskIdentifier) ?? Data()
        pending += 1
        lock.unlock()
        let event = UploadResult(key: task.taskDescription, status: (task.response as? HTTPURLResponse)?.statusCode ?? 0,
                                 data: data, error: error?.localizedDescription)
        completed(event) { [self] in
            lock.lock(); pending -= 1
            let finish = didDrain && pending == 0
            if finish { didDrain = false }
            lock.unlock()
            if finish { drained() }
        }
    }
    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        lock.lock(); didDrain = true
        let finish = pending == 0
        if finish { didDrain = false }
        lock.unlock()
        if finish { drained() }
    }
}

@MainActor
final class NodeSyncController: ObservableObject {
    static let shared = NodeSyncController()
    static let sessionIdentifier = "voice-memory.node-uploads.v1"
    @Published private(set) var jobs: [TransferJob] = []
    @Published private(set) var problem: String?
    @Published var foreground = false
    let library: RecordingLibrary
    private let journal: TransferJournal
    private let root: URL
    private var subscriptions = Set<AnyCancellable>()
    private var preparing = false
    private var restoring = true
    private var timer: Timer?
    private var backgroundCompletion: (() -> Void)?
    private var backgroundDrained = false
    private lazy var proxy = UploadDelegate(completed: { [weak self] result, done in
        Task { @MainActor in self?.complete(result); done() }
    }, drained: { [weak self] in
        Task { @MainActor in self?.finishBackgroundEvents() }
    })
    private lazy var session: URLSession = {
        #if os(iOS)
        let config = URLSessionConfiguration.background(withIdentifier: Self.sessionIdentifier)
        config.sessionSendsLaunchEvents = true
        #else
        let config = URLSessionConfiguration.default
        #endif
        config.isDiscretionary = false
        config.waitsForConnectivity = true
        config.timeoutIntervalForResource = 24 * 60 * 60
        let queue = OperationQueue(); queue.maxConcurrentOperationCount = 1
        return URLSession(configuration: config, delegate: proxy, delegateQueue: queue)
    }()
    private var endpoint: String { UserDefaults.standard.string(forKey: "node.endpoint") ?? "" }
    private var client: NodeClient? {
        guard let url = try? NodeEndpoint.validate(endpoint), let token = ConnectionSecret.read() else { return nil }
        return NodeClient(endpoint: url, token: token)
    }
    private init() {
        let defaultSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("VoiceMemory")
        #if DEBUG
        let support = ProcessInfo.processInfo.environment["VOICE_MEMORY_CLIENT_DATA"].map { URL(fileURLWithPath: $0) } ?? defaultSupport
        #else
        let support = defaultSupport
        #endif
        root = support.appendingPathComponent("NodeOutgoing")
        #if os(iOS)
        library = CaptureRecorder.shared.library
        #else
        library = RecordingLibrary(root: support.appendingPathComponent("MacImports"))
        foreground = true
        #endif
        journal = TransferJournal(root: root.appendingPathComponent("journal"))
        do { jobs = try journal.load() } catch { problem = "同步队列暂时无法读取，原音仍在手机。" }
        #if os(iOS)
        CaptureRecorder.shared.$recordings.debounce(for: .milliseconds(500), scheduler: RunLoop.main)
            .sink { [weak self] _ in self?.refresh() }.store(in: &subscriptions)
        #endif
        NotificationCenter.default.publisher(for: .nodeConfigurationChanged).sink { [weak self] _ in
            self?.configurationChanged()
        }.store(in: &subscriptions)
        NotificationCenter.default.publisher(for: .nodeConnectionRestored).sink { [weak self] _ in
            self?.resumePending(force: false)
        }.store(in: &subscriptions)
        timer = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { [weak self] _ in
            Task { @MainActor in if self?.foreground == true { self?.refresh() } }
        }
        session.getAllTasks { tasks in
            let running = Set(tasks.compactMap { $0.taskDescription?.split(separator: "/").first.map(String.init) })
            Task { @MainActor in
                for var job in self.jobs where job.state == .sending && !running.contains(job.id) {
                    job.state = .queued; try? self.persist(job)
                }
                self.restoring = false; self.refresh()
            }
        }
    }
    func handleBackgroundEvents(completion: @escaping () -> Void) {
        _ = session
        if backgroundDrained { backgroundDrained = false; completion() }
        else { backgroundCompletion = completion }
    }
    private func finishBackgroundEvents() {
        if let completion = backgroundCompletion { backgroundCompletion = nil; completion() }
        else { backgroundDrained = true }
    }
    private func configurationChanged() {
        let target = endpoint
        #if os(iOS)
        let enabled = SyncRoute.current == .direct
        #else
        let enabled = true
        #endif
        session.getAllTasks { tasks in
            for task in tasks where !enabled || task.originalRequest?.url?.absoluteString.hasPrefix(target + "/") != true {
                task.cancel()
            }
        }
        refresh()
    }
    private func persist(_ job: TransferJob) throws {
        try journal.save(job)
        jobs.removeAll { $0.id == job.id }; jobs.append(job)
    }
    func refresh() {
        #if os(macOS)
        guard LocalNodeController.shared.receivingAllowed else { return }
        #endif
        #if os(iOS)
        guard SyncRoute.current == .direct else { return }
        #endif
        guard !restoring, let client else { return }
        let records: [Recording]
        do { records = try library.load().recordings }
        catch { problem = error.localizedDescription; return }
        do {
            for record in records where record.isPlayable && record.trashedAt == nil {
                let proposed = TransferJob(recordingID: record.id, target: endpoint)
                if !jobs.contains(where: { $0.id == proposed.id }) { try persist(proposed) }
            }
        } catch { problem = error.localizedDescription; return }
        guard !preparing, !jobs.contains(where: { $0.target == endpoint && $0.state == .sending }) else { return }
        guard let index = jobs.firstIndex(where: { job in
            job.target == endpoint && (job.state == .queued || job.state == .failed)
                && (job.nextAttemptAt.map { $0 <= .now } ?? true)
                && records.contains(where: { r in r.id == job.recordingID && r.trashedAt == nil && r.isPlayable })
        }) else { return }
        var job = jobs[index]
        preparing = true
        let library = self.library
        let folder = root.appendingPathComponent(job.id)
        let recordingID = job.recordingID
        Task {
            defer { preparing = false }
            do {
                let output = try await Task.detached {
                    let manifest = folder.appendingPathComponent("\(recordingID.uuidString).json")
                    if FileManager.default.fileExists(atPath: manifest.path) {
                        return RecordingExport(audioURL: folder.appendingPathComponent("\(recordingID.uuidString).caf"), manifestURL: manifest)
                    }
                    return try RecordingExport.prepare(id: recordingID, from: library, to: folder)
                }.value
                guard job.target == endpoint, self.client != nil else { return }
                #if os(macOS)
                guard LocalNodeController.shared.receivingAllowed else { return }
                #endif
                #if os(iOS)
                guard SyncRoute.current == .direct else { return }
                #endif
                let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
                let manifest = try decoder.decode(RecordingExportManifest.self, from: Data(contentsOf: output.manifestURL))
                job.audioSHA256 = manifest.audioSHA256; job.attempts += 1; job.state = .sending
                job.message = nil; job.updatedAt = .now; job.nextAttemptAt = nil
                try persist(job)
                let task = session.uploadTask(with: try client.uploadRequest(output), fromFile: output.audioURL)
                task.taskDescription = "\(job.id)/\(job.attempts)"
                task.resume()
            } catch { job.fail(error.localizedDescription); try? persist(job); problem = error.localizedDescription }
        }
    }
    #if os(macOS)
    func prepareForLibrarySwitch() throws {
        // Receipts at the same address belong to the previous library. Cached
        // audio must obtain a receipt from the selected copy before it is done.
        let retained = Set(try library.load().recordings.filter { $0.isPlayable && $0.trashedAt == nil }.map(\.id))
        for var job in jobs where job.target == endpoint && retained.contains(job.recordingID) {
            job.state = .queued; job.nextAttemptAt = nil; job.message = nil; job.updatedAt = .now
            try persist(job)
        }
    }
    func suspendForLibraryMaintenance() async throws {
        for _ in 0..<300 {
            let tasks = await session.allTasks
            tasks.forEach { $0.cancel() }
            if tasks.isEmpty && !preparing { return }
            try await Task.sleep(for: .milliseconds(100))
        }
        throw NodeFailure(message: "原音传输尚未结束，未切换资料库。请稍后重试。")
    }
    #endif
    private func complete(_ result: UploadResult) {
        guard let description = result.key else { return }
        let parts = description.split(separator: "/")
        guard let key = parts.first.map(String.init),
              var job = jobs.first(where: { $0.id == key }), job.state == .sending else { return }
        let attempt = parts.count > 1 ? Int(parts[1]) : 1
        guard attempt == job.attempts else { return }
        do {
            if let error = result.error { throw NodeFailure(message: error) }
            guard 200..<300 ~= result.status else {
                struct ErrorBody: Decodable { let error: String }
                throw NodeFailure(message: (try? JSONDecoder().decode(ErrorBody.self, from: result.data).error) ?? "节点未确认接收，将自动重试。")
            }
            let receipt = try JSONDecoder().decode(TransferReceipt.self, from: result.data)
            try job.acknowledge(receipt)
            try persist(job)
            try? FileManager.default.removeItem(at: root.appendingPathComponent(job.id))
            problem = nil
        } catch { job.fail(error.localizedDescription); try? persist(job); problem = error.localizedDescription }
        refresh()
    }
    func retry() {
        resumePending(force: true)
    }
    private func resumePending(force: Bool) {
        for var job in jobs where job.target == endpoint && job.state == .failed {
            job.state = .queued; job.nextAttemptAt = nil; try? persist(job)
        }
        session.getAllTasks { tasks in
            let stalled = tasks.filter {
                force || $0.countOfBytesSent == 0
            }.compactMap { task -> (URLSessionTask, String)? in
                guard let key = task.taskDescription?.split(separator: "/").first.map(String.init) else { return nil }
                return (task, key)
            }
            Task { @MainActor in
                for (task, key) in stalled {
                    guard var job = self.jobs.first(where: { $0.id == key }),
                          job.target == self.endpoint, job.state == .sending,
                          force || Date.now.timeIntervalSince(job.updatedAt) > 20 else { continue }
                    job.state = .queued; job.nextAttemptAt = nil
                    do { try self.persist(job); task.cancel() }
                    catch { self.problem = error.localizedDescription }
                }
                self.refresh()
            }
        }
    }
    func label(for id: UUID) -> String {
        guard !endpoint.isEmpty else { return "保存在手机" }
        guard let job = jobs.first(where: { $0.recordingID == id && $0.target == endpoint }) else { return "等待同步" }
        switch job.state {
        case .queued: return "等待同步"
        case .sending, .awaitingReceipt: return "正在同步"
        case .delivered: return "节点已接收"
        case .failed: return "等待重试"
        }
    }
    var pendingCount: Int { jobs.filter { $0.target == endpoint && $0.state != .delivered }.count }
}

#if os(iOS)
final class PhoneAppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication, handleEventsForBackgroundURLSession identifier: String,
                     completionHandler: @escaping () -> Void) {
        guard identifier == NodeSyncController.sessionIdentifier else { completionHandler(); return }
        NodeSyncController.shared.handleBackgroundEvents(completion: completionHandler)
    }
}

#endif
