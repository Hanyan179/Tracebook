import SwiftUI

struct DocumentSourceInfo: Codable, Sendable {
    struct Page: Codable, Sendable {
        let number: Int
        let status: String
        let start: Int?
        let end: Int?
    }
    let format: String
    let pageCount: Int
    let pages: [Page]
    let parserWarnings: Bool
    let rangesValid: Bool
}

struct DocumentSourceNotice: View {
    let info: DocumentSourceInfo
    var quoteStart: Int? = nil
    var quoteLength: Int = 0
    private var citedPages: [Int] {
        guard info.rangesValid, let quoteStart, quoteLength > 0 else { return [] }
        return info.pages.compactMap { page in
            guard let start = page.start, let end = page.end,
                  quoteStart < end, quoteStart + quoteLength > start else { return nil }
            return page.number
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if !citedPages.isEmpty {
                Label(LF("PDF 页码：%@", citedPages.map(String.init).joined(separator: ", ")), systemImage: "doc.text")
                    .font(.callout.weight(.medium))
            }
            DisclosureGroup {
                VStack(alignment: .leading, spacing: 8) {
                    Text(LF("共 %d 页，其中 %d 页提取到文字。", info.pageCount, info.pages.filter { $0.status == "text" }.count))
                    let missing = info.pages.filter { $0.status != "text" }.map(\.number)
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
