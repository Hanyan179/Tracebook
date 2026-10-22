import Combine
import Foundation

enum LibraryReference {
    static func identity(_ url: URL) -> (kind: String, id: UUID)? {
        let parts = url.path.split(separator: "/")
        if url.scheme == "tracebook", url.host == "source", parts.count == 2,
           ["note", "recording", "input", "document"].contains(String(parts[0])), let id = UUID(uuidString: String(parts[1])) {
            return (String(parts[0]), id)
        }
        guard url.scheme == nil, let filename = parts.last, filename.hasSuffix(".md"),
              let id = UUID(uuidString: String(filename.dropLast(3))) else { return nil }
        if parts.count == 2, let kind = ["notes": "note", "transcripts": "recording", "documents": "document"][String(parts[0])] {
            return (kind, id)
        }
        if parts.count == 5, parts[0] == "sources", parts[3] == "inputs", UUID(uuidString: String(parts[2])) != nil {
            return ("input", id)
        }
        return nil
    }
}

struct KnowledgeSource: Codable, Identifiable, Sendable {
    let id: String
    let kind: String
    let title: String
    let date: String
    let contentVersion: String
    let textAvailable: Bool
    var excerpt: String?
    var excerptTruncated: Bool?
    var text: String?
    var nextOffset: Int?
    var totalCharacters: Int?
    var evidenceType: String?
    var analysisFreshness: String?
    var identity: String { kind + "/" + id }
    var symbol: String { switch kind { case "document": "doc.text"; case "input": "keyboard"; case "note": "note.text"; case "analysis": "sparkles"; case "daily": "calendar"; case "memory": "bookmark"; default: "waveform" } }
    var kindTitle: String { switch kind { case "document": "导入资料"; case "input": "本地输入"; case "note": "笔记"; case "analysis": "AI 每日分析"; case "daily": "每日记录"; case "memory": "长期记忆"; default: "原音文字" } }
    var analysisNotice: String? {
        guard kind == "analysis" else { return nil }
        switch analysisFreshness {
        case "current": return "模型生成内容，请查看原始来源核对。"
        case "stale": return "来源已变化，此分析需要重新核对。"
        default: return "分析的来源版本未确认，请核对原文。"
        }
    }
}
struct KnowledgeResult: Codable, Sendable {
    let mode: String
    let generatedAnswer: Bool
    let sources: [KnowledgeSource]
    let coverage: Coverage
    struct Coverage: Codable, Sendable {
        let scopes: [String]
        let dateFrom: String?
        let dateTo: String?
        let returned: Int
        let hasMore: Bool
    }
}
struct Conversation: Codable, Identifiable, Sendable {
    let id: String
    let title: String
}
struct ConversationTurn: Codable, Identifiable, Sendable {
    let id: String
    let sequence: Int
    let question: String
    let response: KnowledgeResult
}
struct ConversationPage: Codable, Sendable {
    let items: [ConversationTurn]
    let nextBefore: Int?
}

@MainActor
final class ConversationStore: ObservableObject {
    @Published var conversations: [Conversation] = []
    @Published var turns: [ConversationTurn] = []
    @Published var current: String?
    @Published var loading = false
    @Published var sending = false
    @Published var moreHistory = false
    @Published var error: String?
    @Published var nextBefore: Int?
    private var generation = UUID()
    private var pending: (question: String, scope: String, id: String)?
    private var endpoint = ""
    func load(client: NodeClient?) async {
        guard let client else { return }
        if endpoint != client.endpoint.absoluteString { newConversation(); endpoint = client.endpoint.absoluteString; conversations = [] }
        do {
            let items = try await client.read("v1/conversations", as: [Conversation].self)
            guard endpoint == client.endpoint.absoluteString else { return }
            conversations = items; moreHistory = items.count == 50
            if current == nil, turns.isEmpty,
               let id = UserDefaults.standard.string(forKey: "chat.last." + endpoint), conversations.contains(where: { $0.id == id }) {
                await select(id, client: client)
            }
        } catch { self.error = error.localizedDescription }
    }
    func earlierConversations(client: NodeClient?) async {
        guard let client, let last = conversations.last, !loading else { return }
        loading = true; defer { loading = false }
        do {
            let items = try await client.read("v1/conversations", query: ["before": last.id], as: [Conversation].self)
            guard endpoint == client.endpoint.absoluteString else { return }
            let existing = Set(conversations.map(\.id))
            conversations += items.filter { !existing.contains($0.id) }; moreHistory = items.count == 50
        } catch { self.error = error.localizedDescription }
    }
    func newConversation() {
        generation = UUID(); current = nil; turns = []; nextBefore = nil; error = nil; pending = nil; loading = false
        UserDefaults.standard.removeObject(forKey: "chat.last." + endpoint)
    }
    func select(_ id: String, client: NodeClient?) async {
        guard let client, !sending else { return }
        newConversation(); current = id; loading = true
        let version = generation
        defer { if version == generation { loading = false } }
        do {
            let page = try await client.read("v1/conversations/" + id, as: ConversationPage.self)
            guard version == generation else { return }
            turns = page.items; nextBefore = page.nextBefore
            UserDefaults.standard.set(id, forKey: "chat.last." + endpoint)
        } catch { if version == generation { self.error = error.localizedDescription } }
    }
    func earlier(client: NodeClient?) async {
        guard let client, let current, let nextBefore, !loading else { return }
        loading = true; let version = generation
        defer { if version == generation { loading = false } }
        do {
            let page = try await client.read("v1/conversations/" + current, query: ["before": String(nextBefore)], as: ConversationPage.self)
            guard version == generation else { return }
            turns.insert(contentsOf: page.items, at: 0); self.nextBefore = page.nextBefore
        } catch { if version == generation { self.error = error.localizedDescription } }
    }
    func send(_ question: String, scope: String, client: NodeClient?) async -> Bool {
        guard let client, !sending, !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return false }
        sending = true; error = nil
        let version = generation
        defer { sending = false }
        do {
            if current == nil {
                let data = try await client.send("v1/conversations", method: "POST", body: [:])
                guard version == generation else { return false }
                current = try JSONDecoder().decode(Conversation.self, from: data).id
            }
            guard let current else { return false }
            if pending?.question != question || pending?.scope != scope { pending = (question, scope, UUID().uuidString) }
            struct Question: Encodable { let query: String; let requestID: String; let language: String; let scopes: [String] }
            let scopes = scope.isEmpty ? ["notes", "transcripts", "daily", "confirmedMemory", "analyses", "localInputs", "documents"] : [scope]
            let data = try await client.sendData("v1/conversations/" + current, method: "POST",
                body: JSONEncoder().encode(Question(query: question, requestID: pending!.id,
                    language: AppLanguage.current.identifier == "en" ? "en" : "zh", scopes: scopes)))
            guard version == generation else { return false }
            let turn = try JSONDecoder().decode(ConversationTurn.self, from: data)
            if !turns.contains(where: { $0.id == turn.id }) { turns.append(turn) }
            pending = nil
            UserDefaults.standard.set(current, forKey: "chat.last." + endpoint)
            conversations = (try? await client.read("v1/conversations", as: [Conversation].self)) ?? conversations
            return true
        } catch { if version == generation { self.error = error.localizedDescription }; return false }
    }
}

struct PersonalNote: Codable, Identifiable, Sendable {
    let id: UUID
    let title: String
    var excerpt: String?
    var organization: LibraryOrganization?
    var day: String?
    var text: String?
    var etag: String?
    var preview: String {
        (excerpt ?? "").replacingOccurrences(of: #"(?m)^#{1,6}\s+"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
    }
}
