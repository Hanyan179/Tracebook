import Combine
import Foundation

struct NodeRecord: Codable, Identifiable, Sendable {
    let id: UUID
    let title: String
    let startedAt: String
    let duration: Double
    let byteCount: Int64
    let sourceDevice: RecordingDevice?
    let audioSHA256: String
    let textState: String
    var markers: [RecordingMarker]?
    var organization: LibraryOrganization?
    var day: String?
    var excerpt: String?
    var date: Date { ISO8601DateFormatter().date(from: startedAt) ?? .distantPast }
    var localRecord: Recording {
        var value = Recording(id: id, startedAt: date, trigger: "node")
        value.title = title; value.duration = duration; value.byteCount = byteCount
        value.status = .saved; value.sourceDevice = sourceDevice
        value.markers = markers
        return value
    }
}
struct NodeDocument: Codable, Sendable {
    var text: String
    let etag: String
    let path: String
}
struct NodeDaily: Codable, Identifiable, Sendable {
    let id: String
    let state: String
    let generatedPath: String?
    var recordingCount: Int?
    var noteCount: Int?
    var sourceCount: Int { (recordingCount ?? 0) + (noteCount ?? 0) }
}
struct NodeCandidate: Codable, Identifiable, Sendable {
    let id: UUID
    let text: String
    let sources: [UUID]
    let state: String
    var memoryRevision: Int?
    var evidence: [MemoryEvidence]?
    var proposal: MemoryModelProposal?
    var relations: [MemoryRelation]?
    var relationsTruncated: Bool?
    var modelReviews: [MemoryReviewReference]?
    var modelReviewsTruncated: Bool?
    var organization: LibraryOrganization?
    var day: String?
    var sourceCount: Int { evidence?.count ?? sources.count }
    var stateTitle: String {
        switch state { case "confirmed": "已确认"; case "inactive": "已停用"; case "rejected": "已忽略"; default: "待确认" }
    }
}
struct NodeConflict: Codable, Identifiable, Sendable, Equatable {
    let path: String
    let state: String
    let generatedPath: String?
    var id: String { path }
}
struct NodeStatus: Codable, Sendable {
    let nodeID: String
    let timeZone: String
    let outputDirectory: String
    let recognitionEnabled: Bool
    let summaryMode: String
    var analysisConfigured: Bool?
    var answerConfigured: Bool?
    var transcriptionConfigured: Bool?
    var archiveCopySupported: Bool?
    var memoryDelivery: MemoryFileDelivery?
    var outputIssue: String?
    let conflicts: [NodeConflict]
}

@MainActor
final class NodeWorkspaceStore: ObservableObject {
    @Published private(set) var endpoint: String
    @Published private(set) var connected = false
    @Published private(set) var records: [NodeRecord] = []
    @Published private(set) var days: [NodeDaily] = []
    @Published private(set) var candidates: [NodeCandidate] = []
    @Published private(set) var status: NodeStatus?
    @Published private(set) var overview: LibraryOverview?
    @Published private(set) var libraryRevision = UUID()
    @Published var problem: String?
    @Published var memoryNotice: String?
    @Published private(set) var loading = false
    @Published private(set) var downloadingID: UUID?
    @Published private(set) var unfinishedEditors = Set<UUID>()
    var hasUnfinishedEdits: Bool { !unfinishedEditors.isEmpty }
    var connectionRevision: UUID { configurationRevision }
    func trackEditor(_ identifier: UUID, unfinished: Bool) {
        guard unfinishedEditors.contains(identifier) != unfinished else { return }
        if unfinished { unfinishedEditors.insert(identifier) }
        else { unfinishedEditors.remove(identifier) }
    }
    let playback: AudioPlayback
    var canPlay: () -> Bool = { true }
    private var audioCache: [UUID: URL] = [:]
    private var configurationRevision = UUID()
    private var refreshTimer: Timer?
    var active = true
    var configured: Bool { !endpoint.isEmpty && ConnectionSecret.read() != nil }
    var client: NodeClient? {
        guard let url = try? NodeEndpoint.validate(endpoint), let token = ConnectionSecret.read(), !token.isEmpty else { return nil }
        return NodeClient(endpoint: url, token: token)
    }
    init(playback: AudioPlayback) {
        self.playback = playback
        endpoint = UserDefaults.standard.string(forKey: "node.endpoint") ?? ""
        #if DEBUG
        if let endpoint = ProcessInfo.processInfo.environment["VOICE_MEMORY_QA_ENDPOINT"],
           let token = ProcessInfo.processInfo.environment["VOICE_MEMORY_QA_TOKEN"] {
            do { try configure(endpoint: endpoint, token: token) }
            catch { problem = error.localizedDescription }
        }
        #endif
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            Task { @MainActor in if self?.active == true { await self?.refresh() } }
        }
    }
    func configure(endpoint: String, token: String) throws {
        guard !hasUnfinishedEdits else {
            throw NodeFailure(message: "还有未完成的编辑。请先保存或复制内容，并关闭编辑页面，再切换或备份资料库。")
        }
        let url = try NodeEndpoint.validate(endpoint)
        if !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            try ConnectionSecret.save(token.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        guard ConnectionSecret.read() != nil else { throw NodeFailure(message: "请填写电脑或节点提供的连接口令。") }
        self.endpoint = url.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        configurationRevision = UUID(); loading = false
        UserDefaults.standard.set(self.endpoint, forKey: "node.endpoint")
        records = []; days = []; candidates = []; status = nil; overview = nil; connected = false
        for url in audioCache.values { try? FileManager.default.removeItem(at: url) }
        audioCache = [:]; playback.stop()
        NotificationCenter.default.post(name: .nodeConfigurationChanged, object: nil)
    }
    /// A failed local startup must not leave another library displayed as ready.
    func prepareForLocalStartup() {
        configurationRevision = UUID(); loading = false
        endpoint = ""; connected = false; records = []; days = []; candidates = []
        status = nil; overview = nil; problem = nil; memoryNotice = nil
        playback.stop(); libraryRevision = UUID()
    }
    /// Invalidate in-flight reads without discarding an editor's current contents.
    func localProcessStopped(_ message: String) {
        configurationRevision = UUID(); loading = false; connected = false
        problem = L(message)
    }
    func disconnect() {
        ConnectionSecret.remove(); endpoint = ""; UserDefaults.standard.removeObject(forKey: "node.endpoint")
        configurationRevision = UUID(); loading = false
        connected = false; records = []; days = []; candidates = []; status = nil; overview = nil
        playback.stop()
        NotificationCenter.default.post(name: .nodeConfigurationChanged, object: nil)
    }
    func refresh() async {
        guard !loading, let client else { return }
        let revision = configurationRevision
        loading = true
        defer { if configurationRevision == revision { loading = false } }
        do {
            let status = try await client.read("v1/status", as: NodeStatus.self)
            guard revision == configurationRevision else { return }
            self.status = status
            if let issue = status.outputIssue {
                connected = false; problem = L(issue)
                return
            }
            async let records = client.read("v1/library/recording", as: LibraryPage<NodeRecord>.self)
            async let days = client.read("v1/library/daily", as: LibraryPage<NodeDaily>.self)
            async let candidates = client.read("v1/library/memory", as: LibraryPage<NodeCandidate>.self)
            async let overview = client.read("v1/library/overview", as: LibraryOverview.self)
            let result = try await (status, records, days, candidates, overview)
            guard revision == configurationRevision else { return }
            let before = self.candidates.map { "\($0.id):\($0.memoryRevision ?? 0):\($0.state):\($0.text)" }.sorted()
            let after = result.3.items.map { "\($0.id):\($0.memoryRevision ?? 0):\($0.state):\($0.text)" }.sorted()
            let oldText = self.records.map { "\($0.id):\($0.textState):\($0.excerpt ?? "")" }.sorted()
            let newText = result.1.items.map { "\($0.id):\($0.textState):\($0.excerpt ?? "")" }.sorted()
            self.status = result.0; self.records = result.1.items; self.days = result.2.items
            self.candidates = result.3.items; self.overview = result.4
            if before != after || oldText != newText { libraryRevision = UUID() }
            let wasConnected = connected
            connected = true; problem = nil
            if !wasConnected {
                NotificationCenter.default.post(name: .nodeConnectionRestored, object: endpoint)
            }
        } catch {
            guard revision == configurationRevision else { return }
            connected = false; problem = LF("暂时无法连接资料库：%@", error.localizedDescription)
        }
    }
    func document(_ path: String) async throws -> NodeDocument {
        guard let client else { throw NodeFailure(message: "请先连接电脑或处理节点。") }
        return try await client.read(path, as: NodeDocument.self)
    }
    func saveDocument(_ path: String, text: String, etag: String) async throws -> NodeDocument {
        guard let client else { throw NodeFailure(message: "请先连接电脑或处理节点。") }
        let data = try await client.send(path, body: ["text": text, "etag": etag])
        let document = try JSONDecoder().decode(NodeDocument.self, from: data)
        await refresh()
        libraryRevision = UUID()
        return document
    }
    func buildDay(_ date: String) async {
        guard let client else { return }
        do { try await client.send("v1/daily/\(date)", method: "POST", body: [:]); await refresh(); libraryRevision = UUID() }
        catch { problem = error.localizedDescription }
    }
    func decide(_ candidate: NodeCandidate, confirmed: Bool) async {
        guard let client else { return }
        do {
            if candidate.memoryRevision != nil {
                _ = try await updateMemory(candidate, state: confirmed ? "confirmed" : "rejected")
                return
            }
            try await client.send("v1/candidates/\(candidate.id.uuidString)", body: ["state": confirmed ? "confirmed" : "rejected"])
            await refresh()
            libraryRevision = UUID()
        } catch { problem = error.localizedDescription }
    }
    func updateMemory(_ candidate: NodeCandidate, state: String? = nil, text: String? = nil, requestID: UUID = UUID()) async throws -> NodeCandidate {
        guard let client, let revision = candidate.memoryRevision else { throw NodeFailure(message: "请重新读取记忆后再操作。") }
        struct Body: Encodable { let state: String?; let text: String?; let memoryRevision: Int; let requestID: UUID }
        let data: Data
        do {
            data = try await client.sendData("v1/candidates/\(candidate.id.uuidString)", method: "PUT",
                                            body: JSONEncoder().encode(Body(state: state, text: text, memoryRevision: revision, requestID: requestID)))
        } catch {
            if let code = (error as? NodeFailure)?.statusCode, code >= 500 { await refresh() }
            throw error
        }
        let updated = try JSONDecoder().decode(NodeCandidate.self, from: data)
        memoryNotice = text != nil ? "记忆修订已保存。" : updated.state == "inactive" ? "记忆已停用，不再用于新的检索。" : updated.state == "confirmed" ? "记忆已确认。" : updated.state == "pending" ? "记忆已回到待确认。" : "记忆已忽略。"
        await refresh(); libraryRevision = UUID()
        return updated
    }
    func libraryChanged() async {
        libraryRevision = UUID()
        await refresh()
    }
    func play(_ record: NodeRecord, at position: TimeInterval? = nil) async {
        guard canPlay(), let client else { return }
        if let cached = audioCache[record.id] {
            playback.toggle(record.localRecord, url: cached, startingAt: position); return
        }
        let revision = configurationRevision
        downloadingID = record.id
        defer { downloadingID = nil }
        do {
            let file = try await client.download(record.id, checksum: record.audioSHA256)
            guard revision == configurationRevision else { try? FileManager.default.removeItem(at: file); return }
            audioCache[record.id] = file
            if canPlay() { playback.toggle(record.localRecord, url: file, startingAt: position) }
        } catch { problem = error.localizedDescription }
    }
}
