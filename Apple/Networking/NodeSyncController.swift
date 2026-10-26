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
    struct Receiver: Codable, Equatable, Identifiable {
        let endpoint: String
        let nodeID: String
        let outputDirectory: String
        var id: String { endpoint + "/" + nodeID }
    }
    @Published private(set) var receiverReview: Receiver?
    @Published private(set) var verifiedReceiver: Receiver?
    @Published private(set) var checkingReceiver = false
    @Published private var activeRecordingIDs = Set<UUID>()
    @Published var foreground = false
    private var receivers: [String: Receiver] = [:]
    private var receiverStorageFailed = false
    private var revision = UUID()
    private var checkedReceipts = Set<UUID>()
    private var refreshAgain = false
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
        let selection = root.appendingPathComponent("receivers.json")
        if FileManager.default.fileExists(atPath: selection.path) {
            do { receivers = try JSONDecoder().decode([String: Receiver].self, from: Data(contentsOf: selection)) }
            catch { receiverStorageFailed = true; problem = "接收资料库记录无法读取，原音仍保留。请恢复本机连接记录后重试。" }
        }
        do { jobs = try journal.load() } catch { problem = "同步队列暂时无法读取，原音仍在手机。" }
        #if os(iOS)
        CaptureRecorder.shared.$recordings.debounce(for: .milliseconds(500), scheduler: RunLoop.main)
            .sink { [weak self] _ in self?.refresh() }.store(in: &subscriptions)
        #endif
        NotificationCenter.default.publisher(for: .nodeConfigurationChanged).sink { [weak self] _ in
            self?.configurationChanged()
        }.store(in: &subscriptions)
        NotificationCenter.default.publisher(for: .nodeConnectionRestored).sink { [weak self] notice in
            guard let self, notice.object as? String == self.endpoint else { return }
            self.checkedReceipts = []; self.verifiedReceiver = nil
            self.resumePending(force: false)
        }.store(in: &subscriptions)
        NotificationCenter.default.publisher(for: .nodeReceiverChanged).sink { [weak self] notice in
            guard let self, notice.object as? String == self.endpoint else { return }
            self.configurationChanged()
        }.store(in: &subscriptions)
        timer = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { [weak self] _ in
            Task { @MainActor in if self?.foreground == true { self?.refresh() } }
        }
        session.getAllTasks { tasks in
            let compatible = tasks.filter { $0.originalRequest?.value(forHTTPHeaderField: "X-Recording-Node-ID") != nil }
            tasks.filter { $0.originalRequest?.value(forHTTPHeaderField: "X-Recording-Node-ID") == nil }.forEach { $0.cancel() }
            let running = Set(compatible.compactMap { $0.taskDescription?.split(separator: "/").first.map(String.init) })
            Task { @MainActor in
                for var job in self.jobs where job.state == .sending && !running.contains(job.id) {
                    job.attempts += 1; job.state = .queued; try? self.persist(job)
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
    private var enabled: Bool {
        #if os(macOS)
        return LocalNodeController.shared.receivingAllowed
        #else
        return SyncRoute.current == .direct
        #endif
    }
    private func current(_ client: NodeClient, revision expected: UUID) -> Bool {
        enabled && revision == expected && endpoint == client.endpoint.absoluteString
            && self.client?.token == client.token
    }
    private func configurationChanged() {
        revision = UUID(); verifiedReceiver = nil; receiverReview = nil; checkedReceipts = []
        session.getAllTasks { tasks in tasks.forEach { $0.cancel() } }
        for var job in jobs where job.state == .sending {
            job.attempts += 1; job.state = .queued; try? persist(job)
        }
        refresh()
    }
    private func persist(_ job: TransferJob) throws {
        try journal.save(job)
        jobs.removeAll { $0.id == job.id }; jobs.append(job)
    }
    private func remember(_ receiver: Receiver) throws {
        var updated = receivers; updated[receiver.endpoint] = receiver
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try JSONEncoder().encode(updated).write(to: root.appendingPathComponent("receivers.json"), options: .atomic)
        receivers = updated
    }
    /// Explicit in-app confirmation binds the location shown in the review, never a later replacement.
    func approveReceiver(_ receiver: Receiver) async {
        guard !preparing, receiverReview == receiver, let client, receiver.endpoint == endpoint else { return }
        let expected = revision
        checkingReceiver = true; preparing = true
        defer { checkingReceiver = false; preparing = false; refresh() }
        do {
            let status = try await client.read("v1/status", timeout: 10, as: NodeStatus.self)
            guard current(client, revision: expected), status.nodeID == receiver.nodeID,
                  status.outputDirectory == receiver.outputDirectory, status.recordingIdentitySupported == true else {
                throw RecordingIngress.IngressError.receiverChanged
            }
            try remember(receiver)
            receiverReview = nil; verifiedReceiver = nil; checkedReceipts = []; problem = nil
        } catch { problem = error.localizedDescription }
    }
    func refresh() {
        guard enabled, !restoring, let client else { return }
        let records: [Recording]
        do {
            records = try library.load().recordings.filter { $0.isPlayable && $0.trashedAt == nil }
            activeRecordingIDs = Set(records.map(\.id))
        } catch { problem = error.localizedDescription; return }
        guard !receiverStorageFailed else { return }
        guard !preparing else { refreshAgain = true; return }
        let expected = revision
        preparing = true; checkingReceiver = true
        Task {
            defer {
                preparing = false; checkingReceiver = false
                if refreshAgain { refreshAgain = false; refresh() }
            }
            do {
                for record in records {
                    let job = TransferJob(recordingID: record.id, target: endpoint)
                    if !jobs.contains(where: { $0.id == job.id }) { try persist(job) }
                }
                let status = try await client.read("v1/status", timeout: 10, as: NodeStatus.self)
                guard current(client, revision: expected) else { return }
                guard status.recordingIdentitySupported == true, UUID(uuidString: status.nodeID) != nil else {
                    throw NodeFailure(message: "请先更新电脑端以核对接收资料库；原音仍保留在这台设备。")
                }
                let receiver = Receiver(endpoint: endpoint, nodeID: status.nodeID, outputDirectory: status.outputDirectory)
                let known = receivers[endpoint]
                let legacyReceipts = jobs.contains { $0.target == endpoint && ($0.state == .delivered || $0.receiverNodeID != nil) }
                if (known != nil && known?.nodeID != receiver.nodeID) || (known == nil && legacyReceipts) {
                    receiverReview = receiver; verifiedReceiver = nil; checkedReceipts = []
                    problem = nil
                    session.getAllTasks { tasks in tasks.forEach { $0.cancel() } }
                    return
                }
                if known == nil { try remember(receiver) }
                if verifiedReceiver?.nodeID != receiver.nodeID { checkedReceipts = [] }
                verifiedReceiver = receiver; receiverReview = nil
                // Reconcile bounded batches on connection/retry, not by re-uploading old audio.
                let unchecked = records.filter { record in
                    !checkedReceipts.contains(record.id) && !jobs.contains { $0.recordingID == record.id && $0.target == endpoint && $0.state == .sending }
                }
                for record in unchecked.prefix(10) {
                    guard current(client, revision: expected) else { return }
                    guard var job = jobs.first(where: { $0.recordingID == record.id && $0.target == endpoint }) else { continue }
                    let audio = library.audioURL(for: record)
                    let checksum = try await Task.detached { try AudioDigest.sha256(audio) }.value
                    guard current(client, revision: expected) else { return }
                    job.audioSHA256 = checksum; job.markersSHA256 = record.markersSHA256
                    job.receiverNodeID = receiver.nodeID; job.state = .awaitingReceipt
                    job.message = nil; job.nextAttemptAt = nil; job.attempts += 1
                    try persist(job)
                    do {
                        let receipt = try await client.recordingReceipt(record.id, receiver: receiver.nodeID)
                        guard current(client, revision: expected) else { return }
                        try job.acknowledge(receipt)
                    } catch let failure as NodeFailure where failure.statusCode == 404 {
                        job.state = .queued
                    } catch let failure as NodeFailure where failure.statusCode == 409 {
                        job.message = failure.message; problem = failure.message
                    } catch let failure as RecordingIngress.IngressError {
                        job.message = failure.localizedDescription; problem = failure.localizedDescription
                    }
                    guard current(client, revision: expected) else { return }
                    try persist(job); checkedReceipts.insert(record.id)
                }
                refreshAgain = refreshAgain || unchecked.count > 10
                if let index = jobs.firstIndex(where: { job in
                    job.target == endpoint && job.receiverNodeID == receiver.nodeID && checkedReceipts.contains(job.recordingID)
                        && (job.state == .queued || job.state == .failed)
                        && (job.nextAttemptAt.map { $0 <= .now } ?? true)
                        && records.contains { $0.id == job.recordingID }
                }), !jobs.contains(where: { $0.target == endpoint && $0.state == .sending }) {
                    await send(jobs[index], client: client, revision: expected, receiver: receiver.nodeID)
                }
                problem = jobs.first { $0.target == endpoint && $0.message != nil }?.message
            } catch {
                guard current(client, revision: expected) else { return }
                verifiedReceiver = nil; checkedReceipts = []; problem = error.localizedDescription
            }
        }
    }
    private func send(_ pending: TransferJob, client: NodeClient, revision expected: UUID, receiver: String) async {
        var job = pending
        let library = self.library, folder = root.appendingPathComponent(job.id), recordingID = job.recordingID
        do {
            let output = try await Task.detached {
                let manifest = folder.appendingPathComponent("\(recordingID.uuidString).json")
                if FileManager.default.fileExists(atPath: manifest.path) {
                    return RecordingExport(audioURL: folder.appendingPathComponent("\(recordingID.uuidString).caf"), manifestURL: manifest)
                }
                return try RecordingExport.prepare(id: recordingID, from: library, to: folder)
            }.value
            guard current(client, revision: expected), verifiedReceiver?.nodeID == receiver,
                  activeRecordingIDs.contains(recordingID) else { return }
            let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
            let manifest = try decoder.decode(RecordingExportManifest.self, from: Data(contentsOf: output.manifestURL))
            job.audioSHA256 = manifest.audioSHA256; job.markersSHA256 = manifest.recording.markersSHA256
            job.receiverNodeID = receiver; job.attempts += 1; job.state = .sending
            job.message = nil; job.updatedAt = .now; job.nextAttemptAt = nil
            try persist(job)
            let request = try await client.uploadRequest(output, receiver: receiver)
            guard current(client, revision: expected), verifiedReceiver?.nodeID == receiver else { return }
            let task = session.uploadTask(with: request, fromFile: output.audioURL)
            task.taskDescription = "\(job.id)/\(job.attempts)"
            task.resume()
        } catch {
            guard current(client, revision: expected) else { return }
            job.fail(error.localizedDescription); try? persist(job); problem = error.localizedDescription
            verifiedReceiver = nil; checkedReceipts = []
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
        guard let receiver = job.receiverNodeID, receivers[job.target]?.nodeID == receiver else {
            job.state = .awaitingReceipt; try? persist(job); refresh(); return
        }
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
        if receiverStorageFailed {
            do {
                receivers = try JSONDecoder().decode([String: Receiver].self,
                    from: Data(contentsOf: root.appendingPathComponent("receivers.json")))
                receiverStorageFailed = false
            } catch { return }
        }
        checkedReceipts = []; verifiedReceiver = nil
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
        if receiverReview != nil { return "接收资料库待核对" }
        if verifiedReceiver == nil || job.receiverNodeID != verifiedReceiver?.nodeID || !checkedReceipts.contains(id) {
            return "接收状态待核对"
        }
        switch job.state {
        case .awaitingReceipt: return job.message == nil ? "正在核对接收" : "接收文件待检查"
        case .queued: return "等待同步"
        case .sending: return "正在同步"
        case .delivered: return "节点已接收"
        case .failed: return "等待重试"
        }
    }
    var pendingCount: Int { activeRecordingIDs.filter { id in
        guard let job = jobs.first(where: { $0.target == endpoint && $0.recordingID == id }) else { return true }
        return job.state != .delivered || job.receiverNodeID != verifiedReceiver?.nodeID
            || receiverReview != nil || verifiedReceiver == nil || !checkedReceipts.contains(id)
    }.count }
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
