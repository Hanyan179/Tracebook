import SwiftUI

enum DocumentContentRole: String, CaseIterable, Codable, Sendable {
    case unknown, user_text, transcript, generated
    var title: String {
        switch self {
        case .unknown: "性质未确认"
        case .user_text: "本人原文"
        case .transcript: "转录文字"
        case .generated: "模型生成内容"
        }
    }
    var notice: String {
        switch self {
        case .unknown: "资料性质与作者未确认，请核对陈述的归属。"
        case .user_text: "导入时指定为本人原文，具体引用和事实仍须核对。"
        case .transcript: "这是转录文字，说话人标签不能证明身份，请对照原音核对。"
        case .generated: "记忆依据请使用原始记录；此内容仍可阅读与检索。"
        }
    }
}

struct DocumentContentNotice: View {
    let role: DocumentContentRole
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(L(role.title), systemImage: role == .generated ? "sparkles" : "doc.text")
                .font(.callout.weight(.medium))
            Text(L(role.notice)).font(.footnote).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct DocumentSourceInfo: Codable, Sendable {
    struct Page: Codable, Sendable {
        let number: Int
        let status: String
        let start: Int?
        let end: Int?
    }
    let format: String
    let pageCount: Int?
    let pages: [Page]?
    let parserWarnings: Bool
    let rangesValid: Bool
    var parts: [DocumentPart]?
    var blocks: [DocumentBlock]?
    var warnings: [DocumentStructureWarning]?
}

struct DocumentPart: Codable, Sendable {
    let id: Int
    let kind: String
    let number: Int
    let blocks: Int
    static func title(_ kind: String) -> String {
        switch kind {
        case "body": "正文"
        case "header": "页眉"
        case "footer": "页脚"
        case "footnote": "脚注"
        case "endnote": "尾注"
        case "comment": "批注"
        default: "其他内容"
        }
    }
}

struct DocumentBlock: Codable, Sendable {
    let number: Int
    let part: Int
    let kind: String
    let start: Int?
    let end: Int?
    var table: Int?
    var row: Int?
    var cell: Int?
}

struct DocumentStructureWarning: Codable, Sendable {
    let reason: String
    let count: Int
    var explanation: String {
        switch reason {
        case "tables": "表格按单元格顺序提取，合并单元格和版式请对照原件。"
        case "revisions": "发现修订：保留插入内容，跳过删除和移出内容；请在原件核对修订。"
        case "images_objects": "图片、文本框和嵌入对象未提取。"
        case "equations": "公式未转换为文字。"
        case "hidden_text": "明确标记为隐藏的文字已跳过，样式产生的隐藏效果未还原。"
        case "fields": "仅保留已有域结果，未执行或更新动态内容。"
        case "external_links": "外部链接只保留可见文字，未读取链接目标。"
        case "alternate_content": "兼容内容仅使用已有的备用表示，可能有遗漏。"
        case "unreferenced_notes": "部分附注未发现对应引用标记，可能不是当前正文的一部分。"
        default: "存在不支持的文档内容，请对照原件核查。"
        }
    }
}

struct DocumentStructureCoverage: View {
    let parts: [DocumentPart]
    let blockCount: Int
    let warnings: [DocumentStructureWarning]
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(LF("已提取 %d 段文字，来自 %d 个部分。", blockCount, parts.filter { $0.blocks > 0 }.count))
            ForEach(["body", "header", "footer", "footnote", "endnote", "comment"], id: \.self) { kind in
                let matching = parts.filter { $0.kind == kind && $0.blocks > 0 }
                if !matching.isEmpty { Text(LF("%@：%d 段文字", L(DocumentPart.title(kind)), matching.reduce(0) { $0 + $1.blocks })) }
            }
            ForEach(Array(warnings.enumerated()), id: \.offset) { _, warning in Text(L(warning.explanation)) }
            Text("编号表示提取顺序，不是原文件页码。样式、自动编号和版式未还原；批注与附注不代表本人陈述。")
        }.fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct DocumentSourceNotice: View {
    let info: DocumentSourceInfo
    var quoteStart: Int? = nil
    var quoteLength: Int = 0
    private var citedPages: [Int] {
        guard info.rangesValid, let quoteStart, quoteLength > 0 else { return [] }
        return (info.pages ?? []).compactMap { page in
            guard let start = page.start, let end = page.end,
                  quoteStart < end, quoteStart + quoteLength > start else { return nil }
            return page.number
        }
    }
    var body: some View {
        if info.format == "docx" { docxNotice }
        else { pdfNotice }
    }
    private var docxNotice: some View {
        VStack(alignment: .leading, spacing: 8) {
            if info.rangesValid, let quoteStart, quoteLength > 0 {
                let cited = (info.blocks ?? []).filter {
                    guard let start = $0.start, let end = $0.end else { return false }
                    return quoteStart < end && quoteStart + quoteLength > start
                }
                if !cited.isEmpty {
                    Label(LF("提取段落：%@", cited.prefix(8).map { String($0.number) }.joined(separator: ", ") + (cited.count > 8 ? "…" : "")), systemImage: "doc.text")
                        .font(.callout.weight(.medium))
                }
            }
            DisclosureGroup {
                VStack(alignment: .leading, spacing: 8) {
                    DocumentStructureCoverage(parts: info.parts ?? [], blockCount: info.blocks?.count ?? 0, warnings: info.warnings ?? [])
                    if !info.rangesValid { Text("这份文字已被修改，原提取段落的位置无法继续核对。") }
                    Text("原文件保持原位，资料夹仅保存提取文字。")
                }.font(.footnote).foregroundStyle(.secondary).padding(.top, 8)
            } label: { Text("DOCX 提取说明").multilineTextAlignment(.leading).frame(maxWidth: .infinity, alignment: .leading) }
                .font(.callout)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    private var pdfNotice: some View {
        VStack(alignment: .leading, spacing: 8) {
            if !citedPages.isEmpty {
                Label(LF("PDF 页码：%@", citedPages.map(String.init).joined(separator: ", ")), systemImage: "doc.text")
                    .font(.callout.weight(.medium))
            }
            DisclosureGroup {
                VStack(alignment: .leading, spacing: 8) {
                    Text(LF("共 %d 页，其中 %d 页提取到文字。", info.pageCount ?? 0, (info.pages ?? []).filter { $0.status == "text" }.count))
                    let missing = (info.pages ?? []).filter { $0.status != "text" }.map(\.number)
                    if !missing.isEmpty { Text(LF("请核对未提取到文字或解析失败的页：%@", missing.map(String.init).joined(separator: ", "))) }
                    Text("此处是提取文字，未识别图片中的文字；阅读顺序与表格请对照原文件。")
                    if info.parserWarnings { Text("解析器报告了格式问题，请对照原文件核查。") }
                    if !info.rangesValid { Text("这份文字已被修改，原页码位置无法继续核对。") }
                }.font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.top, 8)
            } label: {
                Text("PDF 提取说明").multilineTextAlignment(.leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }.font(.callout)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}
