import Combine
import Foundation

struct LibraryOrganization: Codable, Sendable, Equatable {
    var categoryID = ""
    var tags: [String] = []
    var archived = false
    var revision = 0
}
struct LibraryCategory: Codable, Identifiable, Sendable {
    let id: String
    let scope: String
    let name: String
    let count: Int
    let revision: Int
    var displayName: String {
        let defaults = ["note.work": "工作", "note.life": "生活", "note.learning": "学习", "note.ideas": "灵感", "recording.work": "工作", "recording.life": "生活", "recording.learning": "学习", "recording.ideas": "灵感",
                        "memory.profile": "个人资料", "memory.preferences": "偏好习惯", "memory.goals": "目标计划",
                        "memory.projects": "工作项目", "memory.people": "人物关系"]
        return defaults[id] == name ? L(name) : name
    }
}
struct LibraryFacets: Codable, Sendable {
    var categories: [LibraryCategory] = []
    var months: [String] = []
    var tags: [String] = []
}
struct LibraryPage<Item: Decodable & Sendable>: Decodable, Sendable {
    let items: [Item]
    let total: Int
    let nextCursor: String?
}
struct LibraryOverview: Codable, Sendable {
    let recordingCount: Int
    let duration: Double
    let dayCount: Int
    let memoryCount: Int
    var noteCount: Int?
    var pendingMemoryCount: Int?
    let week: [LibraryDayCount]
}
struct LibraryDayCount: Codable, Sendable {
    let day: String
    let count: Int
}

struct LibraryFilter: Equatable {
    var query = ""
    var category = ""
    var month = ""
    var tag = ""
    var state = ""
    var archived = false
    var hasSearchFilters: Bool { !query.isEmpty || !category.isEmpty || !month.isEmpty || !tag.isEmpty }
    var parameters: [String: String] {
        ["q": query, "category": category, "month": month, "tag": tag,
         "state": state, "archived": archived ? "true" : "false", "limit": "40"]
    }
    var signature: String { parameters.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }.joined(separator: "\u{1f}") }
}

@MainActor
final class LibraryLoader<Item: Decodable & Identifiable & Sendable>: ObservableObject {
    @Published private(set) var items: [Item] = []
    @Published private(set) var total = 0
    @Published private(set) var facets = LibraryFacets()
    @Published private(set) var loading = false
    @Published private(set) var nextCursor: String?
    @Published var problem: String?
    private var generation = UUID()
    private var loadedScope = ""
    func reload(client: NodeClient?, kind: String, filter: LibraryFilter) async {
        let request = UUID(); generation = request
        let scope = (client?.endpoint.absoluteString ?? "") + kind + filter.signature
        if loadedScope != scope { items = []; total = 0; nextCursor = nil }
        loadedScope = scope; loading = true; problem = nil
        defer { if generation == request { loading = false } }
        guard let client else {
            items = []; total = 0; nextCursor = nil; facets = LibraryFacets()
            problem = "资料库尚未连接。"; return
        }
        do {
            try await Task.sleep(for: .milliseconds(220))
            async let page = client.read("v1/library/\(kind)", query: filter.parameters, as: LibraryPage<Item>.self)
            async let facets = client.read("v1/library/\(kind)/facets", as: LibraryFacets.self)
            let result = try await (page, facets)
            guard request == generation, !Task.isCancelled else { return }
            items = result.0.items; total = result.0.total; nextCursor = result.0.nextCursor; self.facets = result.1
        } catch is CancellationError {} catch {
            if generation == request, !Task.isCancelled {
                items = []; total = 0; nextCursor = nil; problem = error.localizedDescription
            }
        }
    }
    func more(client: NodeClient?, kind: String, filter: LibraryFilter) async {
        guard let client, let cursor = nextCursor, !loading else { return }
        let request = generation
        loading = true
        defer { if generation == request { loading = false } }
        do {
            var parameters = filter.parameters; parameters["cursor"] = cursor
            let page = try await client.read("v1/library/\(kind)", query: parameters, as: LibraryPage<Item>.self)
            guard request == generation else { return }
            let existing = Set(items.map(\.id))
            items.append(contentsOf: page.items.filter { !existing.contains($0.id) })
            nextCursor = page.nextCursor; total = page.total; problem = nil
        } catch { if generation == request { problem = error.localizedDescription } }
    }
}
