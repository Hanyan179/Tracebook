import SwiftUI

struct ArchiveFolderFeedback: View {
    @ObservedObject private var local = LocalNodeController.shared
    var body: some View {
        if local.archiveFeedbackLibrary == local.dataDirectory.path {
            if local.archiveFolderBusy {
                HStack {
                    ProgressView().controlSize(.small)
                    Text("正在复制并核对资料与历史").font(.callout)
                }
            }
            if local.archiveFolderBusy || local.archiveFolderProblem != nil || local.archiveFolderResult != nil || local.archiveFolderCounts != nil {
                if (local.archiveFolderBusy || local.archiveFolderProblem != nil), let target = local.archiveFolderTarget {
                    Text(verbatim: target).font(.caption).textSelection(.enabled).fixedSize(horizontal: false, vertical: true).id(target)
                }
                if let problem = local.archiveFolderProblem {
                    Text(L(problem)).font(.callout).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    if let path = local.archiveFolderFailurePath {
                        Text(verbatim: path).font(.callout.monospaced()).textSelection(.enabled).id(path)
                    }
                    if let recovery = local.archiveFolderRecovery {
                        Text(L(recovery)).font(.callout).fixedSize(horizontal: false, vertical: true)
                    }
                } else if !local.archiveFolderBusy {
                    if let counts = local.archiveFolderCounts {
                        Text(LF("已核对 %d 份文件，复制 %d 份，复用相同副本 %d 份。已切换目录，原资料保留。", counts.total, counts.copied, counts.reused))
                            .font(.callout).fixedSize(horizontal: false, vertical: true)
                    } else if let result = local.archiveFolderResult {
                        Text(L(result)).font(.callout).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }
}
